from django.db import transaction
from django.utils import timezone

import logging

from apps.sessions_app.models import ZoomMeeting

from .forms import DEFAULT_APPOINTMENT_DURATION_MINUTES
from .models import Appointment, AppointmentStatus
from .availability import is_counselor_slot_available, normalize_client_preferred_datetime
from .utils import (
    ZoomAPIError,
    ZoomNotConfiguredError,
    clear_zoom_token_cache,
    create_zoom_meeting,
    pick_meeting_launch_url,
    update_zoom_meeting,
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

    return Appointment.objects.create(
        case=case,
        counselor=case.counselor,
        client=client,
        scheduled_at=scheduled_at,
        duration_minutes=duration,
        status=AppointmentStatus.PENDING,
        session_number=session_number,
        request_message=(request_message or "").strip(),
    )


def ensure_pending_session_appointment(
    *,
    case,
    client,
    session_number: int,
    scheduled_at,
    request_message: str = "",
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
        return pending
    return create_appointment_request(
        case=case,
        client=client,
        scheduled_at=scheduled_at,
        session_number=session_number,
        request_message=message,
    )


def update_pending_appointment(
    appointment: Appointment,
    *,
    scheduled_at,
    duration_minutes: int,
) -> Appointment:
    """상담사: 대기 중 예약 시간 수정 (확정 전)"""
    if appointment.status != AppointmentStatus.PENDING:
        raise AppointmentServiceError("대기 중인 예약만 시간을 수정할 수 있습니다.")
    appointment.scheduled_at = scheduled_at
    appointment.duration_minutes = duration_minutes
    appointment.save(update_fields=["scheduled_at", "duration_minutes", "updated_at"])
    return appointment


@transaction.atomic
def confirm_appointment_with_zoom(appointment: Appointment) -> tuple[Appointment, ZoomMeeting]:
    """
    상담사 예약 확정 시 Zoom 회의 생성 및 Case.zoom_meeting_url 저장.
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

    case = appointment.case
    topic = f"[KSCU 상담] {case.client.name} · {case.case_number}"

    try:
        meeting_data = create_zoom_meeting(
            topic=topic,
            start_time=appointment.scheduled_at,
            duration_minutes=appointment.duration_minutes,
        )
    except (ZoomAPIError, ZoomNotConfiguredError):
        raise

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
        },
    )

    # 사례·내담자용 링크는 짧은 join_url 우선 (start_url은 호스트용·매우 길 수 있음)
    case.zoom_meeting_url = join_url or start_url
    case.save(update_fields=["zoom_meeting_url"])

    appointment.status = AppointmentStatus.CONFIRMED
    appointment.confirmed_at = timezone.now()
    appointment.save(update_fields=["status", "confirmed_at", "updated_at"])

    return appointment, zoom_meeting


@transaction.atomic
def reschedule_confirmed_appointment(
    appointment: Appointment,
    *,
    new_scheduled_at,
) -> tuple[Appointment, str | None]:
    """
    확정 예약 일시 변경 — 슬롯·중복 검사 후 DB 저장.
    Zoom 갱신 실패 시에도 DB 변경은 유지하고 경고 메시지를 반환한다.
    """
    if appointment.status != AppointmentStatus.CONFIRMED:
        raise AppointmentServiceError("확정된 예약만 일정을 변경할 수 있습니다.")

    new_scheduled_at = normalize_client_preferred_datetime(new_scheduled_at)
    available, message = is_counselor_slot_available(
        appointment.counselor_id,
        new_scheduled_at,
        duration_minutes=appointment.duration_minutes,
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

    appointment.scheduled_at = new_scheduled_at
    appointment.save(update_fields=["scheduled_at", "updated_at"])

    zoom_warning: str | None = None
    zoom_meeting = getattr(appointment, "zoom_meeting", None)
    if zoom_meeting and zoom_meeting.zoom_meeting_id:
        try:
            update_zoom_meeting(
                zoom_meeting.zoom_meeting_id,
                start_time=new_scheduled_at,
                duration_minutes=appointment.duration_minutes,
            )
        except ZoomAPIError as exc:
            clear_zoom_token_cache()
            zoom_warning = str(exc)
            logger.warning(
                "Zoom meeting update skipped for appointment %s (meeting_id=%s): %s",
                appointment.pk,
                zoom_meeting.zoom_meeting_id,
                exc,
            )

    return appointment, zoom_warning


@transaction.atomic
def reject_appointment_request(appointment: Appointment, *, reason: str) -> Appointment:
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
    return appointment
