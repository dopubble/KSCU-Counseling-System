from datetime import datetime

from django.db import transaction
from django.utils import timezone

import logging

from apps.counseling.models import CounselingMethod
from apps.sessions_app.models import ZoomMeeting

from .forms import DEFAULT_APPOINTMENT_DURATION_MINUTES
from .models import Appointment, AppointmentStatus
from .availability import is_counselor_slot_available, normalize_client_preferred_datetime, format_local_datetime
from .utils import (
    ZoomAPIError,
    ZoomNotConfiguredError,
    clear_zoom_token_cache,
    create_zoom_meeting,
    delete_zoom_meeting,
    get_zoom_meeting,
    is_zoom_configured,
    parse_zoom_meeting_start_datetime,
    pick_meeting_launch_url,
    update_zoom_meeting,
    update_zoom_meeting_participant_settings,
)
from .remote_zoom_capacity import ensure_remote_zoom_capacity
from .in_person_room_capacity import ensure_in_person_room_capacity
from .zoom_hosts import (
    assign_host_emails_for_appointments,
    get_zoom_licensed_user_emails,
    confirmed_remote_appointments_queryset,
    resolve_zoom_host_email_for_appointment,
)
from .zoom_links import (
    capture_appointment_zoom_join_url,
    resolve_appointment_zoom_join_url,
    sync_case_zoom_meeting_url,
)

logger = logging.getLogger(__name__)


class AppointmentServiceError(Exception):
    """예약 처리 오류"""


def _counselor_slot_taken(counselor_id, scheduled_at, exclude_appointment_id=None):
    qs = Appointment.objects.filter(
        counselor_id=counselor_id,
        scheduled_at=scheduled_at,
        status__in=[AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED],
    )
    if exclude_appointment_id:
        qs = qs.exclude(pk=exclude_appointment_id)
    return qs.exists()


@transaction.atomic
def create_appointment_request(
    *,
    case,
    client,
    scheduled_at,
    duration_minutes: int | None = None,
    session_number: int | None = None,
    request_message: str = "",
    notify: bool = True,
) -> Appointment:
    """내담자 예약 신청 (PENDING, Zoom 미생성). 시간(분)은 상담사 확정 시 조정."""
    if not case.counselor_id:
        raise AppointmentServiceError("담당 상담사가 배정되지 않아 예약 신청을 할 수 없습니다.")

    duration = duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
    scheduled_at = normalize_client_preferred_datetime(scheduled_at)
    available, message = is_counselor_slot_available(
        case.counselor_id,
        scheduled_at,
        duration_minutes=duration,
        require_full_duration=False,
    )
    if not available:
        raise AppointmentServiceError(message)

    appointment = Appointment.objects.create(
        case=case,
        counselor=case.counselor,
        client=client,
        scheduled_at=scheduled_at,
        duration_minutes=duration,
        status=AppointmentStatus.PENDING,
        session_number=session_number,
        request_message=(request_message or "").strip(),
    )
    if notify:
        _notify_appointment_request(appointment)
    return appointment


def _notify_appointment_request(appointment: Appointment) -> None:
    from apps.counseling.emailing import send_appointment_request_notification

    send_appointment_request_notification(appointment)


def _notify_appointment_confirmation(appointment: Appointment) -> None:
    from apps.counseling.emailing import send_appointment_confirmation_notification

    send_appointment_confirmation_notification(appointment)


def _maybe_notify_zoom_link_change(
    appointment: Appointment,
    previous_url: str,
    *,
    notify: bool,
    new_url: str | None = None,
) -> None:
    if not notify or appointment.status != AppointmentStatus.CONFIRMED:
        return
    resolved = (new_url or resolve_appointment_zoom_join_url(appointment, appointment.case)).strip()
    prev = (previous_url or "").strip()
    if not resolved or (prev and prev.rstrip("/") == resolved.rstrip("/")):
        return
    from apps.counseling.emailing import send_appointment_zoom_link_updated_notification

    send_appointment_zoom_link_updated_notification(appointment, previous_url=prev, zoom_url=resolved)


def _notify_appointment_pending_update(appointment: Appointment) -> None:
    from apps.counseling.emailing import send_appointment_pending_update_notification

    send_appointment_pending_update_notification(appointment)


def ensure_pending_session_appointment(
    *,
    case,
    client,
    session_number: int,
    scheduled_at,
    request_message: str = "",
    notify: bool = True,
) -> Appointment:
    """회기별 PENDING Appointment — 없으면 생성, 있으면 일시·요청 내용 갱신."""
    message = (request_message or "").strip()
    pending = Appointment.objects.filter(
        case=case,
        session_number=session_number,
        status=AppointmentStatus.PENDING,
    ).first()
    if pending:
        pending.scheduled_at = scheduled_at
        pending.request_message = message
        pending.save(update_fields=["scheduled_at", "request_message", "updated_at"])
        if notify:
            _notify_appointment_request(pending)
        return pending
    return create_appointment_request(
        case=case,
        client=client,
        scheduled_at=scheduled_at,
        session_number=session_number,
        request_message=message,
        notify=notify,
    )


def update_pending_appointment(
    appointment: Appointment,
    *,
    scheduled_at,
    duration_minutes: int,
    notify_client: bool = True,
) -> Appointment:
    """상담사: 대기 중 예약 시간 수정 (확정 전)"""
    if appointment.status != AppointmentStatus.PENDING:
        raise AppointmentServiceError("대기 중인 예약만 시간을 수정할 수 있습니다.")
    appointment.scheduled_at = scheduled_at
    appointment.duration_minutes = duration_minutes
    appointment.save(update_fields=["scheduled_at", "duration_minutes", "updated_at"])
    if notify_client:
        _notify_appointment_pending_update(appointment)
    return appointment


def _create_zoom_meeting_for_appointment(
    appointment: Appointment,
    *,
    host_user_email: str | None = None,
    notify_link_change: bool = False,
) -> tuple[ZoomMeeting, str]:
    """예약 1건에 Zoom 회의 생성·저장. join_url 반환."""
    previous_url = (
        capture_appointment_zoom_join_url(appointment) if notify_link_change else ""
    )
    case = appointment.case
    topic = f"[KSCU 상담] {case.client.name} · {case.case_number}"
    host_email = (host_user_email or resolve_zoom_host_email_for_appointment(appointment)).strip()

    meeting_data = create_zoom_meeting(
        topic=topic,
        start_time=appointment.scheduled_at,
        duration_minutes=appointment.duration_minutes,
        host_user_email=host_email or None,
    )

    join_url = (meeting_data.get("join_url") or "").strip()
    start_url = (meeting_data.get("start_url") or "").strip()
    if not join_url and not start_url:
        raise ZoomAPIError("Zoom 회의 참여 링크(Join URL)를 받지 못했습니다.")

    zoom_meeting, _created = ZoomMeeting.objects.update_or_create(
        appointment=appointment,
        defaults={
            "zoom_meeting_id": str(meeting_data.get("id", "")),
            "join_url": join_url,
            "start_url": start_url,
            "password": meeting_data.get("password", "") or "",
            "zoom_host_email": host_email,
        },
    )

    launch_url = join_url or start_url
    sync_case_zoom_meeting_url(appointment, join_url=launch_url)
    _maybe_notify_zoom_link_change(
        appointment,
        previous_url,
        notify=notify_link_change,
        new_url=launch_url,
    )
    return zoom_meeting, launch_url


def _appointment_uses_zoom(appointment: Appointment) -> bool:
    return appointment.case.counseling_method == CounselingMethod.REMOTE


def _zoom_meeting_record_is_usable(zoom: ZoomMeeting | None) -> bool:
    """DB에 저장된 Zoom 회의가 API에서 조회 가능한지 확인."""
    if zoom is None:
        return False
    meeting_id = (zoom.zoom_meeting_id or "").strip()
    join_url = (zoom.join_url or "").strip()
    if not meeting_id or not join_url:
        return False
    if not is_zoom_configured():
        return True
    try:
        meeting_data = get_zoom_meeting(meeting_id)
    except ZoomAPIError:
        return False
    return bool(pick_meeting_launch_url(meeting_data))


def _sync_zoom_meeting_from_api(
    zoom: ZoomMeeting,
    appointment: Appointment,
) -> ZoomMeeting | None:
    """Zoom API 기준으로 join_url을 맞춘 뒤 반환. 회의가 없으면 None."""
    meeting_id = (zoom.zoom_meeting_id or "").strip()
    if not meeting_id:
        return None
    if not is_zoom_configured():
        return zoom if (zoom.join_url or "").strip() else None

    try:
        meeting_data = get_zoom_meeting(meeting_id)
        join_url = pick_meeting_launch_url(meeting_data)
        if not join_url:
            return None
    except ZoomAPIError:
        return None

    update_fields: list[str] = []
    if zoom.join_url != join_url:
        zoom.join_url = join_url
        update_fields.append("join_url")
    start_url = (meeting_data.get("start_url") or "").strip()
    if start_url and zoom.start_url != start_url:
        zoom.start_url = start_url
        update_fields.append("start_url")
    if update_fields:
        zoom.save(update_fields=update_fields)

    sync_case_zoom_meeting_url(appointment, join_url=join_url)

    return zoom


@transaction.atomic
def attach_zoom_meeting_to_confirmed_appointment(
    appointment: Appointment,
) -> ZoomMeeting:
    """확정된 비대면 예약에 Zoom 회의가 없거나 무효하면 생성·재생성."""
    if appointment.status != AppointmentStatus.CONFIRMED:
        raise AppointmentServiceError("확정된 예약만 Zoom 회의를 연결할 수 있습니다.")
    if not _appointment_uses_zoom(appointment):
        raise AppointmentServiceError("비대면 상담만 Zoom 회의를 연결할 수 있습니다.")

    existing = getattr(appointment, "zoom_meeting", None)
    if existing:
        synced = _sync_zoom_meeting_from_api(existing, appointment)
        if synced is not None:
            return synced

    old_meeting_id = (existing.zoom_meeting_id or "").strip() if existing else ""

    ensure_remote_zoom_capacity(appointment)
    zoom_meeting, _launch_url = _create_zoom_meeting_for_appointment(appointment)

    new_meeting_id = (zoom_meeting.zoom_meeting_id or "").strip()
    if old_meeting_id and old_meeting_id != new_meeting_id:
        delete_zoom_meeting(old_meeting_id)

    return zoom_meeting


def backfill_missing_zoom_meetings(
    *,
    dry_run: bool = False,
) -> tuple[int, int, list[str]]:
    """
    Zoom 없이 확정된 예약에 회의 생성.
    반환: (created, skipped, errors)
    """
    if not is_zoom_configured():
        raise ZoomNotConfiguredError(
            "Zoom API 설정이 없습니다. ZOOM_* 환경 변수를 확인해 주세요."
        )

    qs = (
        Appointment.objects.filter(
            status=AppointmentStatus.CONFIRMED,
            case__counseling_method=CounselingMethod.REMOTE,
        )
        .select_related("case", "case__client", "zoom_meeting")
        .order_by("scheduled_at")
    )

    created = skipped = 0
    errors: list[str] = []

    for appointment in qs.iterator():
        zoom = getattr(appointment, "zoom_meeting", None)
        if _zoom_meeting_record_is_usable(zoom):
            skipped += 1
            continue

        if dry_run:
            created += 1
            continue

        try:
            attach_zoom_meeting_to_confirmed_appointment(appointment)
            created += 1
        except (ZoomAPIError, ZoomNotConfiguredError, AppointmentServiceError) as exc:
            clear_zoom_token_cache()
            failed_label = f"{appointment.case.client.name} ({appointment.pk})"
            errors.append(f"{failed_label}: {exc}")
            logger.warning("Zoom backfill failed for appointment %s: %s", appointment.pk, exc)

    return created, skipped, errors


def _is_zoom_daily_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate limit" in msg
        or "too many" in msg
        or "400 meeting create" in msg
        or "400 meeting update" in msg
    )


def fix_mismatched_zoom_host_assignments(
    *,
    dry_run: bool = False,
    notify_link_change: bool = False,
    scheduled_from: datetime | None = None,
    scheduled_to: datetime | None = None,
    limit: int | None = None,
    stop_on_rate_limit: bool = True,
) -> tuple[int, int, list[str]]:
    """
    zoom_host_email이 호스트 배정 알고리즘(30분 버퍼 포함)과 다르면 재생성.
    반환: (fixed, skipped, errors)
    """
    if not is_zoom_configured():
        raise ZoomNotConfiguredError(
            "Zoom API 설정이 없습니다. ZOOM_* 환경 변수를 확인해 주세요."
        )

    appointments = list(confirmed_remote_appointments_queryset())
    licensed = get_zoom_licensed_user_emails()
    primary_host = licensed[0].strip().lower() if licensed else ""
    licensed_set = {email.strip().lower() for email in licensed if email.strip()}

    def _stored_host(appointment: Appointment) -> str:
        zoom = getattr(appointment, "zoom_meeting", None)
        return (zoom.zoom_host_email or "").strip().lower() if zoom else ""

    assignable = [
        apt
        for apt in appointments
        if not (_stored_host(apt) and _stored_host(apt) not in licensed_set)
    ]
    expected = assign_host_emails_for_appointments(assignable)
    mismatches: list[tuple[Appointment, str, str, str]] = []
    skipped = 0

    for appointment in appointments:
        stored_host = _stored_host(appointment)
        if stored_host and stored_host not in licensed_set:
            skipped += 1
            continue

        zoom = getattr(appointment, "zoom_meeting", None)
        meeting_id = (zoom.zoom_meeting_id or "").strip() if zoom else ""
        if not meeting_id:
            skipped += 1
            continue

        stored = stored_host
        exp = (expected.get(str(appointment.pk), "") or "").strip().lower()
        if not exp or stored == exp:
            skipped += 1
            continue

        if scheduled_from is not None and appointment.scheduled_at < scheduled_from:
            skipped += 1
            continue
        if scheduled_to is not None and appointment.scheduled_at >= scheduled_to:
            skipped += 1
            continue

        label = (
            f"{appointment.case.client.name} "
            f"{appointment.scheduled_at:%Y-%m-%d %H:%M}"
        )
        mismatches.append((appointment, stored, exp, label))

    # host_02(비-primary) 대상 먼저 — host_01 일일 API 한도 보호
    mismatches.sort(
        key=lambda item: (
            item[2] == primary_host,
            item[0].scheduled_at,
            str(item[0].pk),
        )
    )
    if limit is not None and limit > 0:
        mismatches = mismatches[:limit]

    fixed = 0
    errors: list[str] = []

    for appointment, stored, exp, label in mismatches:
        if dry_run:
            fixed += 1
            errors.append(f"[would fix] {label}: {stored or '(empty)'} -> {exp}")
            continue

        zoom = getattr(appointment, "zoom_meeting", None)
        meeting_id = (zoom.zoom_meeting_id or "").strip() if zoom else ""
        previous_url = (
            capture_appointment_zoom_join_url(appointment) if notify_link_change else ""
        )
        try:
            _create_zoom_meeting_for_appointment(
                appointment,
                host_user_email=exp,
                notify_link_change=False,
            )
            refreshed = ZoomMeeting.objects.filter(appointment_id=appointment.pk).first()
            new_id = (refreshed.zoom_meeting_id or "").strip() if refreshed else ""
            if meeting_id and new_id and meeting_id != new_id:
                delete_zoom_meeting(meeting_id)
            new_url = (refreshed.join_url or "").strip() if refreshed else ""
            _maybe_notify_zoom_link_change(
                appointment,
                previous_url,
                notify=notify_link_change,
                new_url=new_url,
            )
            fixed += 1
        except (ZoomAPIError, ZoomNotConfiguredError, AppointmentServiceError) as exc:
            clear_zoom_token_cache()
            errors.append(f"{label}: {exc}")
            logger.warning(
                "Zoom host fix failed for appointment %s: %s",
                appointment.pk,
                exc,
            )
            if stop_on_rate_limit and _is_zoom_daily_quota_error(exc):
                errors.append(
                    "[rate limit] Zoom 일일 API 한도 도달 — 나머지는 내일 재시도하세요."
                )
                break

    return fixed, skipped, errors


def recreate_all_zoom_meetings(
    *,
    dry_run: bool = False,
) -> tuple[int, int, list[str]]:
    """
    확정 비대면 예약 Zoom 회의를 Licensed 호스트 1/2에 재배정해 전부 재생성.
    반환: (recreated, skipped, errors)
    """
    if not is_zoom_configured():
        raise ZoomNotConfiguredError(
            "Zoom API 설정이 없습니다. ZOOM_* 환경 변수를 확인해 주세요."
        )

    appointments = list(confirmed_remote_appointments_queryset())
    if not appointments:
        return 0, 0, []

    host_emails = assign_host_emails_for_appointments(appointments)
    recreated = skipped = 0
    errors: list[str] = []

    if dry_run:
        for appointment in appointments:
            host_email = host_emails.get(str(appointment.pk), "")
            label = f"{appointment.case.client.name} {appointment.scheduled_at:%Y-%m-%d %H:%M}"
            errors.append(f"[would recreate] {label} -> {host_email or '(default host)'}")
            recreated += 1
        return recreated, skipped, errors

    for appointment in appointments:
        client_name = appointment.case.client.name
        label = f"{client_name} ({appointment.pk})"
        host_email = host_emails.get(str(appointment.pk), "")

        existing = getattr(appointment, "zoom_meeting", None)
        old_meeting_id = (existing.zoom_meeting_id or "").strip() if existing else ""

        try:
            _create_zoom_meeting_for_appointment(
                appointment,
                host_user_email=host_email,
            )
            new_meeting = getattr(appointment, "zoom_meeting", None)
            new_meeting_id = (new_meeting.zoom_meeting_id or "").strip() if new_meeting else ""
            if old_meeting_id and old_meeting_id != new_meeting_id:
                delete_zoom_meeting(old_meeting_id)
            recreated += 1
        except (ZoomAPIError, ZoomNotConfiguredError, AppointmentServiceError) as exc:
            clear_zoom_token_cache()
            errors.append(f"{label} -> {host_email}: {exc}")
            logger.warning(
                "Zoom recreate failed for appointment %s: %s",
                appointment.pk,
                exc,
            )

    return recreated, skipped, errors


@transaction.atomic
def create_and_confirm_appointment_by_counselor(
    *,
    case,
    session_number: int,
    scheduled_at,
    duration_minutes: int | None = None,
    notify: bool = True,
) -> tuple[Appointment, ZoomMeeting | None]:
    """상담사가 내담자 신청 없이 회기 예약을 생성하고 바로 확정."""
    if not case.counselor_id:
        raise AppointmentServiceError("담당 상담사가 배정되지 않았습니다.")

    duration = duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
    scheduled_at = normalize_client_preferred_datetime(scheduled_at)

    if Appointment.objects.filter(
        case=case,
        session_number=session_number,
        status=AppointmentStatus.PENDING,
    ).exists():
        raise AppointmentServiceError(
            "이 회기에 대기 중인 예약 신청이 있습니다. 해당 신청을 확정해 주세요."
        )

    if Appointment.objects.filter(
        case=case,
        session_number=session_number,
        status__in=(
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.SCHEDULED,
            AppointmentStatus.CANCEL_PENDING,
        ),
    ).exists():
        raise AppointmentServiceError("이 회기에 이미 확정된 예약이 있습니다.")

    available, message = is_counselor_slot_available(
        case.counselor_id,
        scheduled_at,
        duration_minutes=duration,
        require_full_duration=True,
    )
    if not available:
        raise AppointmentServiceError(message)

    appointment = Appointment.objects.create(
        case=case,
        counselor=case.counselor,
        client=case.client,
        scheduled_at=scheduled_at,
        duration_minutes=duration,
        status=AppointmentStatus.PENDING,
        session_number=session_number,
        request_message="",
    )
    return confirm_appointment_with_zoom(appointment, notify=notify)


@transaction.atomic
def confirm_appointment_with_zoom(
    appointment: Appointment,
    *,
    notify: bool = True,
) -> tuple[Appointment, ZoomMeeting | None]:
    """
    상담사 예약 확정.
    비대면(REMOTE)만 Zoom 회의 생성 및 Case.zoom_meeting_url 저장.
    """
    if appointment.status != AppointmentStatus.PENDING:
        raise AppointmentServiceError("이미 처리된 예약입니다.")

    if _counselor_slot_taken(
        appointment.counselor_id,
        appointment.scheduled_at,
        exclude_appointment_id=appointment.pk,
    ):
        raise AppointmentServiceError(
            "해당 시간에 이미 확정된 다른 상담이 있습니다. 시간을 수정해 주세요."
        )

    zoom_meeting: ZoomMeeting | None = None
    if _appointment_uses_zoom(appointment):
        ensure_remote_zoom_capacity(appointment)
        try:
            zoom_meeting, _launch_url = _create_zoom_meeting_for_appointment(appointment)
        except (ZoomAPIError, ZoomNotConfiguredError):
            raise
    else:
        ensure_in_person_room_capacity(appointment)

    appointment.status = AppointmentStatus.CONFIRMED
    appointment.confirmed_at = timezone.now()
    appointment.save(update_fields=["status", "confirmed_at", "updated_at"])

    if notify:
        _notify_appointment_confirmation(appointment)

    return appointment, zoom_meeting


@transaction.atomic
def reschedule_confirmed_appointment(
    appointment: Appointment,
    *,
    new_scheduled_at,
    duration_minutes: int | None = None,
    skip_availability: bool = False,
    notify_zoom_link_change: bool = True,
) -> tuple[Appointment, str | None]:
    """
    확정 예약 일시 변경 — 슬롯·중복 검사 후 DB 저장.
    Zoom 갱신 실패 시에도 DB 변경은 유지하고 경고 메시지를 반환한다.
    """
    if appointment.status != AppointmentStatus.CONFIRMED:
        raise AppointmentServiceError("확정된 예약만 일정을 변경할 수 있습니다.")

    new_scheduled_at = normalize_client_preferred_datetime(new_scheduled_at)
    duration = (
        duration_minutes
        if duration_minutes is not None
        else appointment.duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES
    )
    if not skip_availability:
        available, message = is_counselor_slot_available(
            appointment.counselor_id,
            new_scheduled_at,
            duration_minutes=duration,
            require_full_duration=True,
        )
        if not available:
            raise AppointmentServiceError(message)

    if _counselor_slot_taken(
        appointment.counselor_id,
        new_scheduled_at,
        exclude_appointment_id=appointment.pk,
    ):
        raise AppointmentServiceError(
            "해당 시간에 이미 확정된 다른 상담이 있습니다. 다른 시간을 선택해 주세요."
        )

    if _appointment_uses_zoom(appointment):
        ensure_remote_zoom_capacity(
            appointment,
            scheduled_at=new_scheduled_at,
            duration_minutes=duration,
            exclude_appointment_id=appointment.pk,
        )
    else:
        ensure_in_person_room_capacity(
            appointment,
            scheduled_at=new_scheduled_at,
            duration_minutes=duration,
            exclude_appointment_id=appointment.pk,
        )

    appointment.scheduled_at = new_scheduled_at
    update_fields = ["scheduled_at", "updated_at"]
    if appointment.duration_minutes != duration:
        appointment.duration_minutes = duration
        update_fields.append("duration_minutes")
    appointment.save(update_fields=update_fields)

    previous_url = (
        capture_appointment_zoom_join_url(appointment)
        if notify_zoom_link_change
        else ""
    )
    zoom_warning: str | None = None
    zoom_meeting = (
        ZoomMeeting.objects.filter(appointment_id=appointment.pk).first()
    )
    if _appointment_uses_zoom(appointment) and zoom_meeting and zoom_meeting.zoom_meeting_id:
        expected_host = resolve_zoom_host_email_for_appointment(appointment).strip()
        current_host = (zoom_meeting.zoom_host_email or "").strip()
        old_meeting_id = (zoom_meeting.zoom_meeting_id or "").strip()

        if expected_host and current_host.lower() != expected_host.lower():
            try:
                _create_zoom_meeting_for_appointment(
                    appointment,
                    host_user_email=expected_host,
                )
                refreshed = getattr(appointment, "zoom_meeting", None)
                new_meeting_id = (
                    (refreshed.zoom_meeting_id or "").strip() if refreshed else ""
                )
                if old_meeting_id and old_meeting_id != new_meeting_id:
                    delete_zoom_meeting(old_meeting_id)
            except ZoomAPIError as exc:
                clear_zoom_token_cache()
                zoom_warning = str(exc)
                logger.warning(
                    "Zoom host reassignment failed for appointment %s "
                    "(meeting_id=%s, expected_host=%s): %s",
                    appointment.pk,
                    old_meeting_id,
                    expected_host,
                    exc,
                )
        else:
            try:
                update_zoom_meeting(
                    zoom_meeting.zoom_meeting_id,
                    start_time=new_scheduled_at,
                    duration_minutes=appointment.duration_minutes,
                )
                _sync_zoom_meeting_from_api(zoom_meeting, appointment)
            except ZoomAPIError as exc:
                clear_zoom_token_cache()
                zoom_warning = str(exc)
                logger.warning(
                    "Zoom meeting update skipped for appointment %s (meeting_id=%s): %s",
                    appointment.pk,
                    zoom_meeting.zoom_meeting_id,
                    exc,
                )

    if notify_zoom_link_change and _appointment_uses_zoom(appointment):
        fix_mismatched_zoom_host_assignments(
            notify_link_change=True,
        )

    refreshed_zoom = ZoomMeeting.objects.filter(appointment_id=appointment.pk).first()
    final_url = (refreshed_zoom.join_url or "").strip() if refreshed_zoom else ""
    _maybe_notify_zoom_link_change(
        appointment,
        previous_url,
        notify=notify_zoom_link_change,
        new_url=final_url or None,
    )

    return appointment, zoom_warning


def sync_existing_zoom_join_urls(
    *,
    dry_run: bool = False,
) -> tuple[int, int, int, list[str]]:
    """
    기존 ZoomMeeting·Case 링크를 join_url 기준으로 정리하고 Zoom 설정을 갱신.
    반환: (updated, skipped, failed, error_messages)
    """
    if not is_zoom_configured():
        raise ZoomNotConfiguredError(
            "Zoom API 설정이 없습니다. ZOOM_* 환경 변수를 확인해 주세요."
        )

    qs = (
        ZoomMeeting.objects.exclude(zoom_meeting_id="")
        .select_related("appointment", "appointment__case")
        .filter(appointment__status=AppointmentStatus.CONFIRMED)
        .order_by("created_at")
    )

    updated = skipped = failed = 0
    errors: list[str] = []
    cases_to_refresh: dict = {}

    for zoom_meeting in qs.iterator():
        appointment = zoom_meeting.appointment
        case = appointment.case
        meeting_id = zoom_meeting.zoom_meeting_id

        if dry_run:
            updated += 1
            cases_to_refresh[case.pk] = case
            continue

        try:
            update_zoom_meeting_participant_settings(meeting_id)
            meeting_data = get_zoom_meeting(meeting_id)
            join_url = pick_meeting_launch_url(meeting_data)
            if not join_url:
                skipped += 1
                continue

            password = (meeting_data.get("password") or "").strip()
            start_url = (meeting_data.get("start_url") or zoom_meeting.start_url or "").strip()

            zoom_fields = []
            if zoom_meeting.join_url != join_url:
                zoom_meeting.join_url = join_url
                zoom_fields.append("join_url")
            if start_url and zoom_meeting.start_url != start_url:
                zoom_meeting.start_url = start_url
                zoom_fields.append("start_url")
            if password and zoom_meeting.password != password:
                zoom_meeting.password = password
                zoom_fields.append("password")
            if zoom_fields:
                zoom_meeting.save(update_fields=zoom_fields)

            sync_case_zoom_meeting_url(appointment)
            cases_to_refresh[case.pk] = case
            updated += 1
        except ZoomAPIError as exc:
            clear_zoom_token_cache()
            failed += 1
            errors.append(f"fail {meeting_id} (case {case.case_number}): {exc}")
            logger.warning(
                "Zoom join URL sync failed for meeting %s: %s",
                meeting_id,
                exc,
            )

    if not dry_run:
        for case in cases_to_refresh.values():
            latest_appointment = (
                Appointment.objects.filter(
                    case=case,
                    status=AppointmentStatus.CONFIRMED,
                    zoom_meeting__join_url__gt="",
                )
                .select_related("zoom_meeting")
                .order_by("-confirmed_at", "-zoom_meeting__created_at")
                .first()
            )
            if latest_appointment is None:
                continue
            sync_case_zoom_meeting_url(latest_appointment)

    return updated, skipped, failed, errors


def _local_minute(dt: datetime) -> datetime:
    return timezone.localtime(dt).replace(second=0, microsecond=0)


def _zoom_meeting_time_matches_appointment(
    appointment: Appointment,
    meeting_data: dict,
) -> bool:
    zoom_start = parse_zoom_meeting_start_datetime(meeting_data)
    if zoom_start is None:
        return False
    db_minute = _local_minute(appointment.scheduled_at)
    zoom_minute = _local_minute(zoom_start)
    zoom_duration = meeting_data.get("duration")
    duration_match = (
        zoom_duration is None
        or int(zoom_duration) == appointment.duration_minutes
    )
    return db_minute == zoom_minute and duration_match


def sync_zoom_meeting_times(
    *,
    dry_run: bool = False,
) -> tuple[int, int, int, int, list[dict], list[str]]:
    """
    DB 예약 일시(KST)와 Zoom 회의 start_time 불일치를 찾아 Zoom PATCH로 맞춘다.

    반환: (in_sync, updated, skipped, failed, mismatches, errors)
    mismatches 항목: client_name, case_number, session_number, meeting_id,
                    db_local, zoom_local, db_duration, zoom_duration
    """
    if not is_zoom_configured():
        raise ZoomNotConfiguredError(
            "Zoom API 설정이 없습니다. ZOOM_* 환경 변수를 확인해 주세요."
        )

    qs = (
        ZoomMeeting.objects.exclude(zoom_meeting_id="")
        .select_related(
            "appointment",
            "appointment__case",
            "appointment__case__client",
            "appointment__counselor",
        )
        .filter(
            appointment__status=AppointmentStatus.CONFIRMED,
            appointment__case__counseling_method=CounselingMethod.REMOTE,
        )
        .order_by("appointment__scheduled_at")
    )

    in_sync = updated = skipped = failed = 0
    mismatches: list[dict] = []
    errors: list[str] = []

    for zoom_meeting in qs.iterator():
        appointment = zoom_meeting.appointment
        case = appointment.case
        meeting_id = (zoom_meeting.zoom_meeting_id or "").strip()
        client_name = case.client.name
        case_number = case.case_number
        session_number = appointment.session_number

        try:
            meeting_data = get_zoom_meeting(meeting_id)
        except ZoomAPIError as exc:
            clear_zoom_token_cache()
            failed += 1
            errors.append(
                f"조회 실패 {meeting_id} ({client_name} {case_number}): {exc}"
            )
            continue

        if _zoom_meeting_time_matches_appointment(appointment, meeting_data):
            in_sync += 1
            continue

        zoom_start = parse_zoom_meeting_start_datetime(meeting_data)
        zoom_local = (
            format_local_datetime(zoom_start)
            if zoom_start
            else (meeting_data.get("start_time") or "?")
        )
        zoom_duration = meeting_data.get("duration")
        try:
            zoom_duration_int = int(zoom_duration) if zoom_duration is not None else None
        except (TypeError, ValueError):
            zoom_duration_int = None

        mismatches.append(
            {
                "appointment": appointment,
                "zoom_meeting": zoom_meeting,
                "meeting_id": meeting_id,
                "client_name": client_name,
                "case_number": case_number,
                "session_number": session_number,
                "db_local": format_local_datetime(appointment.scheduled_at),
                "zoom_local": zoom_local,
                "db_duration": appointment.duration_minutes,
                "zoom_duration": zoom_duration_int,
            }
        )

    if dry_run:
        return in_sync, 0, 0, failed, mismatches, errors

    for row in mismatches:
        appointment = row["appointment"]
        zoom_meeting = row["zoom_meeting"]
        meeting_id = row["meeting_id"]
        label = (
            f"{row['client_name']} | {row['case_number']} | "
            f"{row['session_number'] or '?'}회기 | {meeting_id}"
        )

        try:
            update_zoom_meeting(
                meeting_id,
                start_time=appointment.scheduled_at,
                duration_minutes=appointment.duration_minutes,
            )
            _sync_zoom_meeting_from_api(zoom_meeting, appointment)
            updated += 1
        except ZoomAPIError as exc:
            clear_zoom_token_cache()
            failed += 1
            errors.append(f"PATCH 실패 {label}: {exc}")
            logger.warning(
                "Zoom meeting time sync failed for appointment %s: %s",
                appointment.pk,
                exc,
            )

    return in_sync, updated, skipped, failed, mismatches, errors


@transaction.atomic
def reject_appointment_request(
    appointment: Appointment,
    *,
    reason: str,
    notify_client: bool = True,
) -> Appointment:
    """상담사: 대기 중 예약 반려."""
    if appointment.status != AppointmentStatus.PENDING:
        raise AppointmentServiceError("대기 중인 예약만 반려할 수 있습니다.")
    reason = (reason or "").strip()
    if not reason:
        raise AppointmentServiceError("반려 사유를 입력해 주세요.")

    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancel_reason = reason
    appointment.cancelled_at = timezone.now()
    appointment.save(
        update_fields=["status", "cancel_reason", "cancelled_at", "updated_at"]
    )
    if notify_client:
        from apps.counseling.emailing import send_appointment_rejection_notification

        send_appointment_rejection_notification(appointment, reason=reason)
    return appointment
