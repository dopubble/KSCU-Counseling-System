"""운영 DB 일회성·반복 적용 수정 작업."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from apps.accounts.client_purge import purge_clients_by_name
from apps.accounts.models import User, UserRole
from apps.counseling.models import Case, CounselingApplication, CounselingMethod
from apps.counseling.session1_bulk_import import force_client_session1_schedule
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import attach_zoom_meeting_to_confirmed_appointment
from apps.scheduling.utils import ZoomNotConfiguredError, delete_zoom_meeting
from apps.sessions_app.models import ZoomMeeting

KIM_JANGSEOYUL_NAME = "김장서율"
KIM_JANGSEOYUL_STUDENT_IDS = ("261110004", "26111004")

KIM_AREUM_NAME = "김아름"
KIM_AREUM_EMAIL = "arsui90@naver.com"
KIM_AREUM_SESSION1_AT = datetime(2026, 6, 25, 16, 0, tzinfo=ZoneInfo("Asia/Seoul"))

LEE_MYUNGRAN_NAME = "이명란"
LEE_MYUNGRAN_EMAIL = "starking0700@naver.com"


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

    session_result = force_client_session1_schedule(
        client_name=KIM_AREUM_NAME,
        client_email=KIM_AREUM_EMAIL,
        scheduled_at=KIM_AREUM_SESSION1_AT,
        dry_run=dry_run,
    )
    lines.append(
        OpsFixupLine(
            "force_kim_areum_session1",
            session_result.status,
            session_result.detail,
        )
    )

    lines.append(
        switch_client_to_in_person(
            client_name=LEE_MYUNGRAN_NAME,
            client_email=LEE_MYUNGRAN_EMAIL,
            dry_run=dry_run,
        )
    )
    return lines
