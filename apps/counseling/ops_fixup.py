"""운영 DB 일회성·반복 적용 수정 작업."""

from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from apps.accounts.client_purge import purge_clients_by_name
from apps.accounts.models import User, UserRole
from apps.counseling.models import Case, CounselingApplication, CounselingMethod
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import (
    AppointmentServiceError,
    attach_zoom_meeting_to_confirmed_appointment,
    fix_mismatched_zoom_host_assignments,
)
from apps.scheduling.utils import (
    ZoomAPIError,
    ZoomNotConfiguredError,
    delete_zoom_meeting,
    is_zoom_configured,
)
from apps.scheduling.zoom_hosts import email_for_host_id
from apps.scheduling.zoom_links import sync_case_zoom_meeting_url
from apps.sessions_app.models import ZoomMeeting

KIM_JANGSEOYUL_NAME = "김장서율"
KIM_JANGSEOYUL_STUDENT_IDS = ("261110004", "26111004")

LEE_MYUNGRAN_NAME = "이명란"
LEE_MYUNGRAN_EMAIL = "starking0700@naver.com"

PARK_MIYEONG_NAME = "박미영"
PARK_MIYEONG_EMAIL = "myparkrang@naver.com"
PARK_MIYEONG_COUNSELOR = "이수정"
PARK_MIYEONG_SESSION1_LABEL = "2026-06-25 22:00"
PARK_MIYEONG_ZOOM_HOST_ID = "host_02"

KIM_SUMI_NAME = "김수미"
KIM_SUMI_EMAIL = "glory921@hanmail.net"
KIM_SUMI_COUNSELOR = "성소미"
KIM_SUMI_SESSION1_LABEL = "2026-06-26 11:00"
KIM_SUMI_ZOOM_HOST_ID = "host_01"

SOONSUNHEE_NAME = "성순희"
SOONSUNHEE_EMAIL = "sooni1028@naver.com"
SOONSUNHEE_COUNSELOR = "정영란"
SOONSUNHEE_SESSION1_LABEL = "2026-06-26 11:00"
SOONSUNHEE_ZOOM_HOST_ID = "host_02"

JEONG_HANGYEOL_NAME = "정한결"
JEONG_HANGYEOL_EMAIL = "hangyeol3884@naver.com"
JEONG_HANGYEOL_COUNSELOR = "정영란"
JEONG_HANGYEOL_CASE_NUMBER = "CASE-2026-0007"
JEONG_HANGYEOL_SESSION1_LABEL = "2026-06-30 15:00"
JEONG_HANGYEOL_ZOOM_HOST_ID = "host_02"

GUHYUNJEONG_NAME = "구현정"


@dataclass
class OpsFixupLine:
    task: str
    status: str
    detail: str


def _find_client(*, name: str, email: str | None = None) -> User | None:
    if email:
        client = User.objects.filter(email__iexact=email, role=UserRole.CLIENT).first()
        if client:
            return client
    return User.objects.filter(name=name, role=UserRole.CLIENT).first()


def switch_client_to_remote_with_zoom(
    *,
    client_name: str,
    client_email: str | None = None,
    dry_run: bool = True,
) -> OpsFixupLine:
    """내담자 상담 방식을 비대면으로 바꾸고, 확정 예약에 Zoom 회의를 연결."""
    client = _find_client(name=client_name, email=client_email)
    if not client:
        return OpsFixupLine(
            f"remote_{client_name}",
            "skip",
            "대상 내담자 없음",
        )

    if dry_run:
        app_count = CounselingApplication.objects.filter(client=client).count()
        case_count = Case.objects.filter(client=client).count()
        apt_count = Appointment.objects.filter(
            client=client,
            status=AppointmentStatus.CONFIRMED,
        ).count()
        return OpsFixupLine(
            f"remote_{client_name}",
            "dry_run",
            f"신청 {app_count}건·사례 {case_count}건 REMOTE, 확정 예약 Zoom {apt_count}건 확인",
        )

    CounselingApplication.objects.filter(client=client).update(
        counseling_method=CounselingMethod.REMOTE
    )
    Case.objects.filter(client=client).update(counseling_method=CounselingMethod.REMOTE)

    created = skipped = recreated = 0
    errors: list[str] = []
    appointments = Appointment.objects.filter(
        client=client,
        status=AppointmentStatus.CONFIRMED,
        case__counseling_method=CounselingMethod.REMOTE,
    ).select_related("case", "zoom_meeting")

    for appointment in appointments:
        before_id = ""
        zoom = getattr(appointment, "zoom_meeting", None)
        if zoom:
            before_id = (zoom.zoom_meeting_id or "").strip()
        try:
            result = attach_zoom_meeting_to_confirmed_appointment(appointment)
            after_id = (result.zoom_meeting_id or "").strip()
            if before_id and before_id == after_id:
                skipped += 1
            else:
                recreated += 1
        except ZoomNotConfiguredError:
            return OpsFixupLine(
                f"remote_{client_name}",
                "error",
                "Zoom API 미설정",
            )
        except Exception as exc:
            errors.append(str(exc))

    if errors:
        return OpsFixupLine(
            f"remote_{client_name}",
            "error",
            f"Zoom 재생성 {recreated}건, 실패 {len(errors)}건: {errors[0]}",
        )
    return OpsFixupLine(
        f"remote_{client_name}",
        "ok",
        f"비대면 전환 완료, Zoom 재생성 {recreated}건, 유효 {skipped}건",
    )


def switch_client_to_in_person(
    *,
    client_name: str,
    client_email: str | None = None,
    dry_run: bool = True,
) -> OpsFixupLine:
    """내담자 상담 방식을 대면으로 바꾸고 Zoom 회의 연결을 해제."""
    client = _find_client(name=client_name, email=client_email)
    if not client:
        return OpsFixupLine(
            f"in_person_{client_name}",
            "skip",
            "대상 내담자 없음",
        )

    if dry_run:
        case_count = Case.objects.filter(client=client).count()
        zoom_count = ZoomMeeting.objects.filter(
            appointment__client=client,
        ).count()
        return OpsFixupLine(
            f"in_person_{client_name}",
            "dry_run",
            f"사례 {case_count}건 IN_PERSON, Zoom 해제 {zoom_count}건",
        )

    CounselingApplication.objects.filter(client=client).update(
        counseling_method=CounselingMethod.IN_PERSON
    )
    Case.objects.filter(client=client).update(
        counseling_method=CounselingMethod.IN_PERSON,
        zoom_meeting_url="",
    )

    removed = 0
    for zoom in ZoomMeeting.objects.filter(appointment__client=client).select_related(
        "appointment"
    ):
        meeting_id = (zoom.zoom_meeting_id or "").strip()
        if meeting_id:
            delete_zoom_meeting(meeting_id)
        zoom.delete()
        removed += 1

    return OpsFixupLine(
        f"in_person_{client_name}",
        "ok",
        f"대면 전환 완료, Zoom 해제 {removed}건",
    )


def _appointment_local_label(appointment: Appointment) -> str:
    return timezone.localtime(appointment.scheduled_at).strftime("%Y-%m-%d %H:%M")


def _find_remote_session1_appointment(
    *,
    client_name: str,
    client_email: str,
    counselor_name: str,
    scheduled_label: str = "",
    case_number: str = "",
) -> Appointment | None:
    qs = Appointment.objects.filter(
        session_number=1,
        status=AppointmentStatus.CONFIRMED,
        case__counseling_method=CounselingMethod.REMOTE,
    ).select_related("case", "counselor", "zoom_meeting", "client")
    case_number = (case_number or "").strip()
    if case_number:
        qs = qs.filter(case__case_number=case_number)
    else:
        client = _find_client(name=client_name, email=client_email)
        if not client:
            return None
        qs = qs.filter(client=client, counselor__name=counselor_name)
    appointment = qs.order_by("-scheduled_at").first()
    if not appointment:
        return None
    if scheduled_label and _appointment_local_label(appointment) != scheduled_label:
        return None
    return appointment


def pin_appointment_zoom_join_url(
    *,
    client_name: str,
    client_email: str,
    counselor_name: str,
    join_url: str,
    meeting_id: str,
    host_id: str = "",
    password: str = "",
    scheduled_label: str = "",
    case_number: str = "",
    dry_run: bool = True,
) -> OpsFixupLine:
    """외부·수동 생성 Zoom 회의 join URL을 DB에 직접 고정 (API 재생성 없음)."""
    task = f"zoom_url_{client_name}_{case_number or scheduled_label or 'session1'}"
    join_url = (join_url or "").strip()
    meeting_id = (meeting_id or "").strip()
    if not join_url or not meeting_id:
        return OpsFixupLine(task, "error", "join_url·meeting_id 필요")

    appointment = _find_remote_session1_appointment(
        client_name=client_name,
        client_email=client_email,
        counselor_name=counselor_name,
        scheduled_label=scheduled_label,
        case_number=case_number,
    )
    if not appointment:
        return OpsFixupLine(task, "skip", "확정 비대면 1회기 예약 없음")

    host_email = (email_for_host_id(host_id) or "").strip() if host_id else ""
    zoom = getattr(appointment, "zoom_meeting", None)
    stored_url = (zoom.join_url or "").strip() if zoom else ""
    if stored_url.rstrip("/") == join_url.rstrip("/"):
        return OpsFixupLine(task, "ok", "이미 동일 join URL")

    if dry_run:
        return OpsFixupLine(
            task,
            "dry_run",
            f"{stored_url or '(없음)'} → {join_url}",
        )

    ZoomMeeting.objects.update_or_create(
        appointment=appointment,
        defaults={
            "zoom_meeting_id": meeting_id,
            "join_url": join_url,
            "start_url": "",
            "password": (password or "").strip(),
            "zoom_host_email": host_email,
        },
    )
    sync_case_zoom_meeting_url(appointment, join_url=join_url)
    return OpsFixupLine(task, "ok", f"join URL 고정 ({meeting_id})")


def recreate_appointment_zoom_on_host(
    *,
    case_number: str,
    host_id: str,
    session_number: int = 1,
    dry_run: bool = True,
) -> OpsFixupLine:
    """확정 비대면 예약 Zoom 회의를 지정 Licensed 호스트로 API 재생성."""
    task = f"zoom_recreate_{case_number}"
    if not is_zoom_configured():
        return OpsFixupLine(task, "skip", "Zoom 미설정")

    target_email = (email_for_host_id(host_id) or "").strip()
    if not target_email:
        return OpsFixupLine(task, "error", f"호스트 ID 오류: {host_id}")

    appointment = (
        Appointment.objects.filter(
            case__case_number=(case_number or "").strip(),
            session_number=session_number,
            status=AppointmentStatus.CONFIRMED,
            case__counseling_method=CounselingMethod.REMOTE,
        )
        .select_related("case", "zoom_meeting")
        .first()
    )
    if not appointment:
        return OpsFixupLine(task, "skip", "확정 비대면 예약 없음")

    zoom = getattr(appointment, "zoom_meeting", None)
    old_meeting_id = (zoom.zoom_meeting_id or "").strip() if zoom else ""
    if dry_run:
        return OpsFixupLine(
            task,
            "dry_run",
            f"{target_email}로 재생성 (기존 meeting_id={old_meeting_id or '없음'})",
        )

    try:
        from apps.scheduling.services import _create_zoom_meeting_for_appointment

        _create_zoom_meeting_for_appointment(
            appointment,
            host_user_email=target_email,
        )
        if old_meeting_id:
            refreshed = ZoomMeeting.objects.filter(appointment_id=appointment.pk).first()
            new_meeting_id = (
                (refreshed.zoom_meeting_id or "").strip() if refreshed else ""
            )
            if new_meeting_id and new_meeting_id != old_meeting_id:
                delete_zoom_meeting(old_meeting_id)
    except (ZoomAPIError, ZoomNotConfiguredError, AppointmentServiceError) as exc:
        return OpsFixupLine(task, "error", str(exc))

    refreshed = ZoomMeeting.objects.filter(appointment_id=appointment.pk).first()
    meeting_id = (refreshed.zoom_meeting_id or "").strip() if refreshed else ""
    join_url = (refreshed.join_url or "").strip() if refreshed else ""
    return OpsFixupLine(task, "ok", f"meeting_id={meeting_id} join={join_url}")


def force_appointment_zoom_host(
    *,
    client_name: str,
    client_email: str,
    counselor_name: str,
    scheduled_label: str,
    host_id: str,
    dry_run: bool = True,
) -> OpsFixupLine:
    """지정 예약 Zoom 호스트를 강제 지정 (자동 배정 알고리즘과 무관)."""
    task = f"zoom_host_{client_name}_{scheduled_label}"
    if not is_zoom_configured():
        return OpsFixupLine(task, "skip", "Zoom 미설정")

    target_email = (email_for_host_id(host_id) or "").strip()
    if not target_email:
        return OpsFixupLine(task, "error", f"호스트 ID 오류: {host_id}")

    client = _find_client(name=client_name, email=client_email)
    if not client:
        return OpsFixupLine(task, "skip", "대상 내담자 없음")

    appointment = (
        Appointment.objects.filter(
            client=client,
            counselor__name=counselor_name,
            session_number=1,
            status=AppointmentStatus.CONFIRMED,
            case__counseling_method=CounselingMethod.REMOTE,
        )
        .select_related("case", "counselor", "zoom_meeting")
        .order_by("-scheduled_at")
        .first()
    )
    if not appointment:
        return OpsFixupLine(task, "skip", "확정 비대면 1회기 예약 없음")

    current_label = _appointment_local_label(appointment)
    if current_label != scheduled_label:
        return OpsFixupLine(
            task,
            "skip",
            f"일시 불일치 (DB {current_label}, 기대 {scheduled_label})",
        )

    zoom = getattr(appointment, "zoom_meeting", None)
    stored = (zoom.zoom_host_email or "").strip().lower() if zoom else ""
    if stored == target_email.lower():
        return OpsFixupLine(task, "ok", f"이미 {host_id} ({target_email})")

    if dry_run:
        return OpsFixupLine(
            task,
            "dry_run",
            f"{stored or '(없음)'} → {host_id} ({target_email})",
        )

    old_meeting_id = (zoom.zoom_meeting_id or "").strip() if zoom else ""
    try:
        from apps.scheduling.services import _create_zoom_meeting_for_appointment

        _create_zoom_meeting_for_appointment(
            appointment,
            host_user_email=target_email,
        )
        if old_meeting_id:
            refreshed = ZoomMeeting.objects.filter(appointment_id=appointment.pk).first()
            new_meeting_id = (
                (refreshed.zoom_meeting_id or "").strip() if refreshed else ""
            )
            if new_meeting_id and new_meeting_id != old_meeting_id:
                delete_zoom_meeting(old_meeting_id)
    except (ZoomAPIError, ZoomNotConfiguredError, AppointmentServiceError) as exc:
        return OpsFixupLine(task, "error", str(exc))

    return OpsFixupLine(task, "ok", f"{host_id} ({target_email})로 재배정")


def ensure_park_miyeong_zoom_host_02(*, dry_run: bool = True) -> OpsFixupLine:
    return force_appointment_zoom_host(
        client_name=PARK_MIYEONG_NAME,
        client_email=PARK_MIYEONG_EMAIL,
        counselor_name=PARK_MIYEONG_COUNSELOR,
        scheduled_label=PARK_MIYEONG_SESSION1_LABEL,
        host_id=PARK_MIYEONG_ZOOM_HOST_ID,
        dry_run=dry_run,
    )


def ensure_kim_sumi_zoom_host_01(*, dry_run: bool = True) -> OpsFixupLine:
    return force_appointment_zoom_host(
        client_name=KIM_SUMI_NAME,
        client_email=KIM_SUMI_EMAIL,
        counselor_name=KIM_SUMI_COUNSELOR,
        scheduled_label=KIM_SUMI_SESSION1_LABEL,
        host_id=KIM_SUMI_ZOOM_HOST_ID,
        dry_run=dry_run,
    )


def ensure_soonsunhee_zoom_host_02(*, dry_run: bool = True) -> OpsFixupLine:
    return force_appointment_zoom_host(
        client_name=SOONSUNHEE_NAME,
        client_email=SOONSUNHEE_EMAIL,
        counselor_name=SOONSUNHEE_COUNSELOR,
        scheduled_label=SOONSUNHEE_SESSION1_LABEL,
        host_id=SOONSUNHEE_ZOOM_HOST_ID,
        dry_run=dry_run,
    )


def ensure_jeonghangyeol_zoom_host_02(*, dry_run: bool = True) -> OpsFixupLine:
    return recreate_appointment_zoom_on_host(
        case_number=JEONG_HANGYEOL_CASE_NUMBER,
        host_id=JEONG_HANGYEOL_ZOOM_HOST_ID,
        dry_run=dry_run,
    )


def ensure_guhyunjeong_session1_time(*, dry_run: bool = True) -> OpsFixupLine:
    """로스터 JSON 기준 구현정 1회기 일시 유지 (7/1 10:00)."""
    from pathlib import Path

    from django.conf import settings

    from apps.counseling.session1_bulk_import import (
        load_session1_matches,
        sync_session1_times_from_roster,
    )

    task = f"session1_time_{GUHYUNJEONG_NAME}"
    path = Path(settings.BASE_DIR) / "data" / "import" / "session1_matches_bulk_202606.json"
    rows = load_session1_matches(path)
    results = sync_session1_times_from_roster(
        rows,
        dry_run=dry_run,
        skip_availability=True,
        client_names=frozenset({GUHYUNJEONG_NAME}),
    )
    if not results:
        return OpsFixupLine(task, "skip", "로스터 행 없음")
    result = results[0]
    if result.status == "error":
        return OpsFixupLine(task, "error", result.detail)
    if result.status == "ok":
        return OpsFixupLine(task, "ok", "일치")
    if dry_run:
        return OpsFixupLine(task, "dry_run", result.detail)
    return OpsFixupLine(task, result.status, result.detail)


def apply_ops_production_fixup_june2026(*, dry_run: bool = True) -> list[OpsFixupLine]:
    lines: list[OpsFixupLine] = []

    purge_result = purge_clients_by_name(
        KIM_JANGSEOYUL_NAME,
        student_id_variants=KIM_JANGSEOYUL_STUDENT_IDS,
        dry_run=dry_run,
    )
    if purge_result.deleted_users:
        lines.append(
            OpsFixupLine(
                "purge_kim_jangseoyul",
                "ok",
                f"삭제 {purge_result.deleted_users}명",
            )
        )
    else:
        lines.append(
            OpsFixupLine(
                "purge_kim_jangseoyul",
                "skip",
                "대상 없음(이미 삭제됨)",
            )
        )

    lines.append(
        switch_client_to_in_person(
            client_name=LEE_MYUNGRAN_NAME,
            client_email=LEE_MYUNGRAN_EMAIL,
            dry_run=dry_run,
        )
    )
    lines.append(fix_zoom_host_mismatches(dry_run=dry_run))
    lines.append(ensure_guhyunjeong_session1_time(dry_run=dry_run))
    lines.append(ensure_kim_sumi_zoom_host_01(dry_run=dry_run))
    lines.append(ensure_soonsunhee_zoom_host_02(dry_run=dry_run))
    lines.append(ensure_park_miyeong_zoom_host_02(dry_run=dry_run))
    return lines


def fix_zoom_host_mismatches(*, dry_run: bool = True) -> OpsFixupLine:
    """겹치는 비대면 예약의 Zoom 호스트 배정 불일치를 수정."""
    if not is_zoom_configured():
        return OpsFixupLine("fix_zoom_host_mismatches", "skip", "Zoom 미설정")

    try:
        fixed, skipped, messages = fix_mismatched_zoom_host_assignments(dry_run=dry_run)
    except ZoomNotConfiguredError as exc:
        return OpsFixupLine("fix_zoom_host_mismatches", "skip", str(exc))

    error_msgs = [m for m in messages if not m.startswith("[would fix]")]
    status = "error" if error_msgs else "ok"
    detail = f"수정 {fixed}건, 건너뜀 {skipped}건"
    if messages:
        detail += f" ({'; '.join(messages[:3])})"
    return OpsFixupLine("fix_zoom_host_mismatches", status, detail)
