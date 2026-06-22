from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from django.db import transaction
from django.db.models import Count, DateTimeField, Exists, F, OuterRef, Q, QuerySet, Subquery, Window
from django.db.models.functions import Coalesce, RowNumber
from django.utils import timezone

from apps.accounts.models import ClientProfile, CounselorProfile, User, UserRole, UserStatus
from apps.documents.models import SessionMaterial
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import create_appointment_request
from apps.sessions_app.models import CounselingJournal, InitialCounselingRecord, TerminationCounselingRecord

from .cancellation_policy import (
    AppointmentOperationError,
    CANCELLATION_LOCK_HOURS,
    EARLY_CLOSE_DAY_CANCEL_THRESHOLD,
    cancel_triggers_session_penalty,
    client_cancel_blocked,
    client_change_blocked,
    is_appointment_in_past,
    is_same_day_as_appointment,
)
from .constants import REMOTE_CLIENT_NAMES
from .models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
    SessionScheduleChangeRequest,
)


def annotate_application_sequence(queryset: QuerySet) -> QuerySet:
    """신청일(오래된 순) 기준 연번 1부터 부여."""
    return queryset.annotate(
        application_no=Window(
            expression=RowNumber(),
            order_by=F("created_at").asc(),
        )
    )


def annotate_application_confirmed_at(queryset: QuerySet) -> QuerySet:
    """사례별 최신 확정 예약의 확정 일시(없으면 updated_at)를 주입."""
    confirmed_subq = (
        Appointment.objects.filter(
            case__application_id=OuterRef("pk"),
            status=AppointmentStatus.CONFIRMED,
        )
        .order_by("-confirmed_at", "-updated_at")
        .annotate(
            effective_at=Coalesce(
                F("confirmed_at"),
                F("updated_at"),
                output_field=DateTimeField(),
            )
        )
    )
    return queryset.annotate(
        consultation_confirmed_at=Subquery(confirmed_subq.values("effective_at")[:1])
    )


def annotate_application_has_confirmed(queryset: QuerySet) -> QuerySet:
    """확정(CONFIRMED) 예약 존재 여부."""
    confirmed = Appointment.objects.filter(
        case__application_id=OuterRef("pk"),
        status=AppointmentStatus.CONFIRMED,
    )
    return queryset.annotate(has_confirmed_appointment=Exists(confirmed))


def annotate_application_cancel_flags(queryset: QuerySet) -> QuerySet:
    """취소 대기(CANCEL_PENDING) 예약 존재 여부."""
    cancel_pending = Appointment.objects.filter(
        case__application_id=OuterRef("pk"),
        status=AppointmentStatus.CANCEL_PENDING,
    )
    return queryset.annotate(has_cancel_pending=Exists(cancel_pending))


def get_confirmed_appointment_for_application(
    application: CounselingApplication,
) -> Appointment | None:
    try:
        case = application.case
    except Case.DoesNotExist:
        return None
    return (
        case.appointments.filter(status=AppointmentStatus.CONFIRMED)
        .order_by("-confirmed_at", "-updated_at")
        .first()
    )


def confirmed_appointment_blocks_client_change(
    application: CounselingApplication,
) -> bool:
    """확정 예약 기준 24시간 이내 변경(신청 수정 등) 불가 여부."""
    appointment = get_confirmed_appointment_for_application(application)
    if not appointment:
        return False
    return client_change_blocked(appointment)


@transaction.atomic
def apply_cancel_request_operating_rules(
    case: Case,
    appointment: Appointment,
) -> list[str]:
    """
    취소 요청 접수 시 운영 규칙 적용.
    - 24시간 이내: remaining_sessions 1회 차감
    - 예약 당일: day_of_cancel_count 증가, 3회 이상 조기 종결
    """
    from .emailing import send_early_termination_counselor_notification

    messages: list[str] = []
    case = Case.objects.select_for_update().get(pk=case.pk)

    if cancel_triggers_session_penalty(appointment):
        before = case.remaining_sessions
        consume_counseling_session(case)
        case.refresh_from_db()
        if before != case.remaining_sessions:
            messages.append(
                "상담 예정 24시간 이내 취소로 남은 상담 회기 1회가 차감되었습니다. "
                f"(남은 회기: {case.remaining_sessions} / {case.total_sessions})"
            )

    if is_same_day_as_appointment(appointment):
        case.day_of_cancel_count += 1
        case.save(update_fields=["day_of_cancel_count"])
        messages.append(
            f"예약 당일 취소가 누적되었습니다. (당일 취소 {case.day_of_cancel_count}회)"
        )
        if (
            case.day_of_cancel_count >= EARLY_CLOSE_DAY_CANCEL_THRESHOLD
            and case.status == CaseStatus.ACTIVE
        ):
            close_case_for_early_termination(case)
            send_early_termination_counselor_notification(case)
            messages.append(
                f"당일 취소가 {EARLY_CLOSE_DAY_CANCEL_THRESHOLD}회 이상 누적되어 "
                "상담이 조기 종결되었습니다. 담당 상담사에게 안내 메일을 발송했습니다."
            )

    return messages


@transaction.atomic
def close_case_for_early_termination(case: Case) -> None:
    """당일 취소 누적 등으로 사례·신청을 조기 종결(CLOSED)."""
    now = timezone.now()
    case.status = CaseStatus.CLOSED
    case.closed_at = case.closed_at or now
    case.save(update_fields=["status", "closed_at"])

    application = case.application
    if application.status not in (
        ApplicationStatus.CANCELLED,
        ApplicationStatus.CLOSED,
    ):
        application.status = ApplicationStatus.CLOSED
        application.save(update_fields=["status", "updated_at"])


@transaction.atomic
def request_appointment_cancel(appointment: Appointment, *, cancel_reason: str) -> Appointment:
    """확정 예약에 대한 내담자 취소 요청."""
    if appointment.status != AppointmentStatus.CONFIRMED:
        raise ValueError("not_confirmed")

    if client_cancel_blocked(appointment):
        raise AppointmentOperationError(
            "past_appointment",
            "이미 지난 상담 예약은 취소할 수 없습니다.",
        )

    case = Case.objects.select_related("application").get(pk=appointment.case_id)
    rule_messages = apply_cancel_request_operating_rules(case, appointment)
    appointment._cancel_rule_messages = rule_messages  # type: ignore[attr-defined]

    now = timezone.now()
    appointment.status = AppointmentStatus.CANCEL_PENDING
    appointment.cancel_reason = cancel_reason.strip()
    appointment.cancel_requested_at = now
    appointment.save(
        update_fields=["status", "cancel_reason", "cancel_requested_at", "updated_at"]
    )
    return appointment


@transaction.atomic
def withdraw_pending_session_appointment(appointment: Appointment) -> None:
    """내담자 PENDING 예약 요청 철회 — 회기를 예약 전 상태로 되돌림."""
    if appointment.status != AppointmentStatus.PENDING:
        raise ValueError("not_pending")

    session_number = appointment.session_number
    case_id = appointment.case_id
    appointment.delete()

    if session_number:
        SessionScheduleChangeRequest.objects.filter(
            case_id=case_id,
            session_number=session_number,
        ).delete()


def _restore_cancel_request_effects(case: Case, appointment: Appointment) -> None:
    """취소 요청 철회·반려 시 요청 접수 때 적용된 회기 차감·당일 취소 카운트 복원."""
    cancel_requested_at = appointment.cancel_requested_at
    scheduled_at = appointment.scheduled_at

    if cancel_requested_at and scheduled_at:
        delta = scheduled_at - cancel_requested_at
        had_session_penalty = (
            timedelta(0) < delta < timedelta(hours=CANCELLATION_LOCK_HOURS)
        )
        if had_session_penalty and case.remaining_sessions < case.total_sessions:
            case.remaining_sessions += 1
            case.save(update_fields=["remaining_sessions"])

        req_local = timezone.localtime(cancel_requested_at)
        sched_local = timezone.localtime(scheduled_at)
        if req_local.date() == sched_local.date() and case.day_of_cancel_count > 0:
            case.day_of_cancel_count -= 1
            case.save(update_fields=["day_of_cancel_count"])


@transaction.atomic
def withdraw_appointment_cancel_request(appointment: Appointment) -> Appointment:
    """내담자 취소 요청(CANCEL_PENDING) 철회 — 예약 확정으로 복원."""
    if appointment.status != AppointmentStatus.CANCEL_PENDING:
        raise ValueError("not_cancel_pending")

    case = Case.objects.select_for_update().get(pk=appointment.case_id)
    _restore_cancel_request_effects(case, appointment)

    appointment.status = AppointmentStatus.CONFIRMED
    appointment.cancel_reason = ""
    appointment.cancel_requested_at = None
    appointment.save(
        update_fields=[
            "status",
            "cancel_reason",
            "cancel_requested_at",
            "updated_at",
        ]
    )
    return appointment


@transaction.atomic
def approve_appointment_cancel_request(appointment: Appointment) -> Appointment:
    """상담사: 내담자 취소 요청 승인 — 예약 취소 확정."""
    if appointment.status != AppointmentStatus.CANCEL_PENDING:
        raise ValueError("not_cancel_pending")

    now = timezone.now()
    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancelled_at = now
    appointment.save(update_fields=["status", "cancelled_at", "updated_at"])
    return appointment


@transaction.atomic
def cancel_confirmed_appointment_by_counselor(
    appointment: Appointment,
    *,
    cancel_reason: str,
) -> Appointment:
    """상담사: 확정 예약 직접 취소 (내담자 회기 차감·당일 취소 누적 미적용)."""
    if appointment.status != AppointmentStatus.CONFIRMED:
        raise AppointmentOperationError(
            "not_confirmed",
            "확정된 예약만 취소할 수 있습니다.",
        )

    if is_appointment_in_past(appointment):
        raise AppointmentOperationError(
            "past_appointment",
            "이미 지난 상담 예약은 취소할 수 없습니다.",
        )

    reason = (cancel_reason or "").strip()
    if len(reason) < 5:
        raise AppointmentOperationError(
            "cancel_reason_required",
            "취소 사유를 5자 이상 입력해 주세요.",
        )

    if appointment.session_number:
        SessionScheduleChangeRequest.objects.filter(
            case_id=appointment.case_id,
            session_number=appointment.session_number,
        ).delete()

    now = timezone.now()
    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancel_reason = reason
    appointment.cancelled_at = now
    appointment.cancel_requested_at = None
    appointment.save(
        update_fields=[
            "status",
            "cancel_reason",
            "cancelled_at",
            "cancel_requested_at",
            "updated_at",
        ]
    )
    return appointment


@transaction.atomic
def reject_appointment_cancel_request(appointment: Appointment, *, reason: str) -> Appointment:
    """상담사: 내담자 취소 요청 반려 — 예약 확정 유지."""
    if appointment.status != AppointmentStatus.CANCEL_PENDING:
        raise ValueError("not_cancel_pending")

    reason = (reason or "").strip()
    if not reason:
        raise AppointmentOperationError("reject_reason_required", "반려 사유를 입력해 주세요.")

    case = Case.objects.select_for_update().get(pk=appointment.case_id)
    _restore_cancel_request_effects(case, appointment)

    appointment.status = AppointmentStatus.CONFIRMED
    appointment.cancel_reason = ""
    appointment.cancel_requested_at = None
    appointment.save(
        update_fields=[
            "status",
            "cancel_reason",
            "cancel_requested_at",
            "updated_at",
        ]
    )
    appointment._cancel_reject_reason = reason  # type: ignore[attr-defined]
    return appointment


@transaction.atomic
def approve_session_schedule_change_request(
    schedule_request: SessionScheduleChangeRequest,
) -> tuple[Appointment, datetime, str | None]:
    """상담사: 확정 회기 일정 변경 요청 승인."""
    from apps.scheduling.services import (
        AppointmentServiceError,
        reschedule_confirmed_appointment,
    )

    appointment = schedule_request.appointment
    if appointment is None or appointment.status != AppointmentStatus.CONFIRMED:
        raise AppointmentOperationError(
            "invalid_schedule_change",
            "확정된 예약에 대한 변경 요청만 승인할 수 있습니다.",
        )

    preferred = schedule_request.preferred_datetime
    if not preferred:
        raise AppointmentOperationError(
            "missing_preferred_datetime",
            "변경 희망 일시가 없습니다.",
        )

    old_scheduled_at = appointment.scheduled_at
    try:
        appointment, zoom_warning = reschedule_confirmed_appointment(
            appointment,
            new_scheduled_at=preferred,
        )
    except AppointmentServiceError as exc:
        raise AppointmentOperationError("reschedule_failed", str(exc)) from exc

    SessionScheduleChangeRequest.objects.filter(pk=schedule_request.pk).delete()
    return appointment, old_scheduled_at, zoom_warning


@transaction.atomic
def reject_session_schedule_change_request(
    schedule_request: SessionScheduleChangeRequest,
    *,
    reason: str,
) -> tuple[Appointment, datetime | None]:
    """상담사: 확정 회기 일정 변경 요청 반려 — 기존 일정 유지."""
    appointment = schedule_request.appointment
    if appointment is None or appointment.status != AppointmentStatus.CONFIRMED:
        raise AppointmentOperationError(
            "invalid_schedule_change",
            "확정된 예약에 대한 변경 요청만 반려할 수 있습니다.",
        )

    reason = (reason or "").strip()
    if not reason:
        raise AppointmentOperationError(
            "reject_reason_required",
            "반려 사유를 입력해 주세요.",
        )

    preferred = schedule_request.preferred_datetime
    SessionScheduleChangeRequest.objects.filter(pk=schedule_request.pk).delete()
    return appointment, preferred


def get_schedule_change_requests_for_counselor(
    case: Case,
) -> list[SessionScheduleChangeRequest]:
    """상담사 사례 상세 — 처리 대기 중인 일정 변경 요청(회기별 최신)."""
    latest = _latest_schedule_change_requests(case)
    pending = [
        req
        for req in latest.values()
        if req.appointment_id
        and req.appointment.status == AppointmentStatus.CONFIRMED
    ]
    return sorted(pending, key=lambda req: (req.session_number, req.created_at))


def count_cancel_pending_appointments(*, use_cache: bool = True) -> int:
    from apps.reports.cache_utils import safe_cache_get, safe_cache_set

    cache_key = "kscu:cancel_pending_count"
    if use_cache:
        cached = safe_cache_get(cache_key)
        if cached is not None:
            return int(cached)
    count = Appointment.objects.filter(status=AppointmentStatus.CANCEL_PENDING).count()
    if use_cache:
        safe_cache_set(cache_key, count, 60)
    return count


def application_has_confirmed_appointment(application: CounselingApplication) -> bool:
    try:
        case = application.case
    except Case.DoesNotExist:
        return False
    return case.appointments.filter(status=AppointmentStatus.CONFIRMED).exists()


def client_can_edit_application(application: CounselingApplication) -> bool:
    if application.status == ApplicationStatus.CANCELLED:
        return False
    if application_has_confirmed_appointment(application):
        return not confirmed_appointment_blocks_client_change(application)
    return True


def client_can_delete_application(application: CounselingApplication) -> bool:
    """매칭·예약 확정 전 접수/매칭대기 신청만 내담자가 삭제 가능."""
    if application.status not in (
        ApplicationStatus.RECEIVED,
        ApplicationStatus.WAITING_MATCH,
    ):
        return False
    if application_has_confirmed_appointment(application):
        return False
    try:
        case = application.case
    except Case.DoesNotExist:
        return True
    if case.counselor_id:
        return False
    blocking_statuses = (
        AppointmentStatus.PENDING,
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.CANCEL_PENDING,
    )
    return not case.appointments.filter(status__in=blocking_statuses).exists()


def build_apply_initial_from_application(
    application: CounselingApplication,
    *,
    user: User | None = None,
) -> dict[str, Any]:
    """상담 신청 폼 pre-fill 데이터."""
    user = user or application.client
    ps = application.preferred_schedule or {}
    initial: dict[str, Any] = {
        "name": user.name,
        "phone": user.phone or "",
        "counseling_types": application.counseling_types or [],
        "reason": application.reason,
        "residence_region": application.residence_region or "",
        "clinical_diagnosis": application.clinical_diagnosis or "",
        "current_medication": application.current_medication or "",
        "occupation": application.occupation or "",
        "counseling_method": counseling_method_for_application(application),
    }

    student_id = ps.get("student_id") or ""
    if not student_id:
        try:
            student_id = user.client_profile.student_id or ""
        except ClientProfile.DoesNotExist:
            student_id = ""
    initial["student_id"] = student_id

    birth_date = ps.get("birth_date") or ""
    if not birth_date:
        try:
            profile = user.client_profile
            birth_date = profile.birth_date.isoformat() if profile.birth_date else ""
        except ClientProfile.DoesNotExist:
            birth_date = ""
    elif hasattr(birth_date, "isoformat"):
        birth_date = birth_date.isoformat()
    if birth_date:
        initial["birth_date"] = date.fromisoformat(birth_date)

    department = ps.get("department") or ""
    if not department:
        try:
            department = user.client_profile.department or ""
        except ClientProfile.DoesNotExist:
            department = ""
    initial["department"] = department

    pref_date = ps.get("preferred_date")
    if pref_date:
        initial["preferred_date"] = (
            date.fromisoformat(pref_date) if isinstance(pref_date, str) else pref_date
        )

    pref_time = ps.get("preferred_time")
    if pref_time:
        if isinstance(pref_time, str):
            for fmt in ("%H:%M", "%H:%M:%S"):
                try:
                    initial["preferred_time"] = datetime.strptime(pref_time, fmt).time()
                    break
                except ValueError:
                    continue
        elif isinstance(pref_time, time):
            initial["preferred_time"] = pref_time

    return initial


def serialize_apply_initial(initial: dict[str, Any]) -> dict[str, Any]:
    """세션 저장용 직렬화."""
    data = dict(initial)
    pref_date = data.get("preferred_date")
    if isinstance(pref_date, date):
        data["preferred_date"] = pref_date.isoformat()
    birth_date = data.get("birth_date")
    if isinstance(birth_date, date):
        data["birth_date"] = birth_date.isoformat()
    pref_time = data.get("preferred_time")
    if isinstance(pref_time, time):
        data["preferred_time"] = pref_time.strftime("%H:%M")
    return data


def deserialize_apply_initial(data: dict[str, Any]) -> dict[str, Any]:
    """세션에서 복원."""
    initial = dict(data)
    pref_date = initial.get("preferred_date")
    if isinstance(pref_date, str):
        initial["preferred_date"] = date.fromisoformat(pref_date)
    birth_date = initial.get("birth_date")
    if isinstance(birth_date, str) and birth_date:
        initial["birth_date"] = date.fromisoformat(birth_date)
    pref_time = initial.get("preferred_time")
    if isinstance(pref_time, str):
        initial["preferred_time"] = datetime.strptime(pref_time, "%H:%M").time()
    return initial


@transaction.atomic
def cancel_application_for_reapply(application: CounselingApplication) -> dict[str, Any]:
    """
    신청을 취소하고 사례·대기 예약을 정리한 뒤, 재신청 폼 initial을 반환.
    """
    if not client_can_edit_application(application):
        if confirmed_appointment_blocks_client_change(application):
            raise ValueError("lock_window")
        raise ValueError("confirmed")

    initial = build_apply_initial_from_application(application)

    application.status = ApplicationStatus.CANCELLED
    application.save(update_fields=["status", "updated_at"])

    try:
        case = application.case
    except Case.DoesNotExist:
        return initial

    now = timezone.now()
    case.appointments.exclude(
        status__in=[AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED]
    ).update(
        status=AppointmentStatus.CANCELLED,
        cancelled_at=now,
        cancel_reason="내담자 신청 내용 수정",
    )

    if case.status == CaseStatus.ACTIVE:
        case.status = CaseStatus.CLOSED
        case.closed_at = now
        case.zoom_meeting_url = ""
        case.save(update_fields=["status", "closed_at", "zoom_meeting_url"])

    return initial


def get_available_counselors():
    """배정 가능한 상담사 목록 (승인된 상담사 우선)"""
    base_qs = CounselorProfile.objects.filter(
        user__role=UserRole.COUNSELOR,
        user__status=UserStatus.ACTIVE,
    ).select_related("user").order_by("user__name")

    approved = base_qs.filter(is_approved=True)
    if approved.exists():
        return approved
    return base_qs


def get_counselor_active_case_counts():
    """상담사별 진행 중(ACTIVE) 사례 건수 {user_id: count}"""
    rows = (
        Case.objects.filter(status=CaseStatus.ACTIVE, counselor_id__isnull=False)
        .values("counselor_id")
        .annotate(count=Count("id"))
    )
    return {row["counselor_id"]: row["count"] for row in rows}


def _counseling_method_for_client(client) -> str:
    if (getattr(client, "name", "") or "").strip() in REMOTE_CLIENT_NAMES:
        return CounselingMethod.REMOTE
    return CounselingMethod.IN_PERSON


def counseling_method_for_application(application: CounselingApplication) -> str:
    """신청서에 저장된 상담 방식 (없으면 내담자 기본값)."""
    method = (getattr(application, "counseling_method", None) or "").strip()
    if method in CounselingMethod.values:
        return method
    return _counseling_method_for_client(application.client)


def sync_case_counseling_method_from_application(application: CounselingApplication) -> None:
    """신청서 상담 방식을 연결된 사례에 반영."""
    try:
        case = application.case
    except Case.DoesNotExist:
        return
    method = counseling_method_for_application(application)
    if case.counseling_method != method:
        case.counseling_method = method
        case.save(update_fields=["counseling_method"])


@transaction.atomic
def assign_counselor(
    application: CounselingApplication,
    counselor: User,
    *,
    total_sessions: int = 10,
) -> Case:
    """
    상담사 배정: 신청 상태를 진행 중으로 변경하고 활성 사례(Case) 생성·갱신.
    최초 매칭 시 total_sessions·remaining_sessions를 동일 값으로 설정합니다.
    """
    if counselor.role != UserRole.COUNSELOR:
        raise ValueError("선택한 사용자는 상담사가 아닙니다.")
    if application.client.role != UserRole.CLIENT:
        raise ValueError(
            f"내담자({application.client.email}) 계정 역할이 내담자(CLIENT)가 아닙니다. "
            "관리자에게 계정 역할 확인을 요청해 주세요."
        )
    if total_sessions < 1:
        raise ValueError("총 회기 수는 1 이상이어야 합니다.")

    application.status = ApplicationStatus.IN_PROGRESS
    application.save(update_fields=["status", "updated_at"])

    case, created = Case.objects.get_or_create(
        application=application,
        defaults={
            "client": application.client,
            "counselor": counselor,
            "status": CaseStatus.ACTIVE,
            "total_sessions": total_sessions,
            "remaining_sessions": total_sessions,
            "counseling_method": counseling_method_for_application(application),
        },
    )
    if not created:
        needs_session_reset = not case.counselor_id or case.total_sessions < 1
        case.counselor = counselor
        case.client = application.client
        case.counseling_method = counseling_method_for_application(application)
        if case.status != CaseStatus.ACTIVE:
            case.status = CaseStatus.ACTIVE
            case.closed_at = None
        if needs_session_reset:
            case.total_sessions = total_sessions
            case.remaining_sessions = total_sessions
        case.save()

    return case


@transaction.atomic
def close_case_after_sessions_exhausted(case: Case) -> None:
    """남은 회기가 0이 되었을 때 사례·신청을 종결 처리."""
    now = timezone.now()
    case.status = CaseStatus.CLOSED
    case.closed_at = case.closed_at or now
    case.remaining_sessions = 0
    case.save(update_fields=["status", "closed_at", "remaining_sessions"])

    application = case.application
    if application.status not in (
        ApplicationStatus.CANCELLED,
        ApplicationStatus.CLOSED,
    ):
        application.status = ApplicationStatus.CLOSED
        application.save(update_fields=["status", "updated_at"])


@transaction.atomic
def consume_counseling_session(case: Case) -> None:
    """상담 1회 완료 시 남은 회기를 1 차감하고, 0이면 종결합니다."""
    locked = Case.objects.select_for_update().get(pk=case.pk)
    if locked.status != CaseStatus.ACTIVE:
        return
    if locked.remaining_sessions <= 0:
        return

    locked.remaining_sessions -= 1
    locked.save(update_fields=["remaining_sessions"])

    if locked.remaining_sessions <= 0:
        close_case_after_sessions_exhausted(locked)


def finalize_completed_journal(journal) -> None:
    """
    상담일지 최종 저장(임시저장 해제) 시 회기 차감.
    동일 일지에 대해 중복 차감하지 않습니다.
    """
    if journal.is_draft or journal.session_consumed:
        return

    consume_counseling_session(journal.case)
    journal.session_consumed = True
    journal.save(update_fields=["session_consumed"])


def reassign_counselor(case: Case, counselor: User) -> Case:
    """기존 사례의 담당 상담사 변경"""
    if counselor.role != UserRole.COUNSELOR:
        raise ValueError("선택한 사용자는 상담사가 아닙니다.")
    case.counselor = counselor
    case.save(update_fields=["counselor"])
    application = case.application
    if application.status in (
        ApplicationStatus.RECEIVED,
        ApplicationStatus.WAITING_MATCH,
        ApplicationStatus.MATCHED,
    ):
        application.status = ApplicationStatus.IN_PROGRESS
        application.save(update_fields=["status", "updated_at"])
    return case


def get_client_home_dashboard(user: User) -> dict[str, Any] | None:
    """
    메인 화면용 내담자 요약.
    진행 중(ACTIVE) 사례·최근 예약(최대 3건)·담당 상담사를 반환합니다.
    """
    if not user.is_authenticated or user.role != UserRole.CLIENT:
        return None

    active_cases = list(
        Case.objects.filter(client=user, status=CaseStatus.ACTIVE)
        .select_related("counselor", "application")
        .order_by("-opened_at")
    )

    if not active_cases:
        return {"has_active_counseling": False}

    case_ids = [case.pk for case in active_cases]
    list_statuses = (
        AppointmentStatus.PENDING,
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.COMPLETED,
    )
    recent_list = list(
        Appointment.objects.filter(
            case_id__in=case_ids,
            client=user,
            status__in=list_statuses,
        )
        .select_related("case", "case__counselor", "counselor", "zoom_meeting")
        .order_by("-scheduled_at")[:3]
    )

    recent_appointments = []
    for apt in recent_list:
        case = apt.case
        zoom_url = _resolve_appointment_zoom_url(apt, case)
        show_zoom = (
            apt.status == AppointmentStatus.CONFIRMED
            and case.counseling_method == CounselingMethod.REMOTE
            and bool(zoom_url)
        )
        recent_appointments.append(
            {
                "scheduled_at": apt.scheduled_at,
                "status": apt.status,
                "status_display": apt.get_status_display(),
                "show_zoom": show_zoom,
                "zoom_url": zoom_url,
                "case_pk": case.pk,
            }
        )

    primary_case = recent_list[0].case if recent_list else active_cases[0]

    counselor = primary_case.counselor
    if counselor:
        counselor_name = counselor.name
        counselor_email = counselor.email
        counselor_photo_url = ""
    else:
        counselor_name = "배정 대기"
        counselor_email = ""
        counselor_photo_url = ""

    return {
        "has_active_counseling": True,
        "counselor_name": counselor_name,
        "counselor_email": counselor_email,
        "counselor_photo_url": counselor_photo_url,
        "case_detail_url": primary_case.pk,
        "recent_appointments": recent_appointments,
        "remaining_sessions": primary_case.remaining_sessions,
        "total_sessions": primary_case.total_sessions,
        "sessions_label": primary_case.sessions_label,
    }


@dataclass(frozen=True)
class CaseSessionCard:
    """사례 상세 화면용 회기 슬롯 카드 (1..total_sessions)."""

    session_number: int
    appointment: Optional[Appointment]
    journal: Optional[CounselingJournal]
    zoom_url: str
    status_code: str
    status_label: str
    materials: tuple[SessionMaterial, ...] = field(default_factory=tuple)
    schedule_change_request: Optional[SessionScheduleChangeRequest] = None
    rejected_appointment: Optional[Appointment] = None
    pending_appointment: Optional[Appointment] = None
    initial_record: Optional[InitialCounselingRecord] = None
    termination_record: Optional[TerminationCounselingRecord] = None
    counselor_assigned: bool = False
    total_sessions: int = 0
    counseling_method: str = CounselingMethod.IN_PERSON

    @property
    def has_materials(self) -> bool:
        return bool(self.materials)

    @property
    def scheduled_datetime(self):
        """상담 예정·요청 일시(화면 표시용)."""
        if self.appointment and self.appointment.status not in (
            AppointmentStatus.CANCELLED,
            AppointmentStatus.NO_SHOW,
        ):
            return self.appointment.scheduled_at
        if (
            self.schedule_change_request
            and self.schedule_change_request.preferred_datetime
        ):
            return self.schedule_change_request.preferred_datetime
        return None

    @property
    def confirmed_datetime(self):
        """하위 호환 — 상담 예정 일시."""
        return self.scheduled_datetime

    @property
    def show_pending_review_actions(self) -> bool:
        """상담사 — 대기 중 예약 확정/반려 버튼."""
        return self.show_counselor_review_buttons

    @property
    def show_counselor_review_buttons(self) -> bool:
        """예약 요청중(REQUESTED) 회기 — 확정/반려 버튼 표시."""
        if self.status_code == "REQUESTED":
            return True
        if self.appointment is None:
            return False
        return self.appointment.status == AppointmentStatus.PENDING

    @property
    def show_counselor_direct_booking(self) -> bool:
        """상담사 — 내담자 신청 없이 일정 입력·확정."""
        if not self.counselor_assigned:
            return False
        if self.status_code in (
            "COMPLETED",
            "NO_SHOW",
            "CONFIRMED",
            "CHANGE_REQUESTED",
            "REQUESTED",
        ):
            return False
        if self.has_session_cancel_pending:
            return False
        if self.show_counselor_schedule_change_review_actions:
            return False
        if self.pending_appointment is not None:
            return False
        if self.appointment is not None and self.appointment.status in (
            AppointmentStatus.PENDING,
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.CANCEL_PENDING,
            AppointmentStatus.COMPLETED,
            AppointmentStatus.NO_SHOW,
        ):
            return False
        if self.status_code == "CANCELLED" and not self.is_counselor_rejection_notice:
            return False
        return self.show_session_actions

    @property
    def show_counselor_appointment_actions(self) -> bool:
        """상담사 예약 확정/반려 버튼 영역 표시."""
        return self.show_counselor_review_buttons

    @property
    def action_appointment(self):
        """템플릿·API용 — 확정/반려 대상 Appointment."""
        if self.pending_appointment is not None:
            return self.pending_appointment
        if self.appointment is not None and self.appointment.status == AppointmentStatus.PENDING:
            return self.appointment
        return None

    @property
    def pending_request_message(self) -> str:
        return self.requested_reason

    @property
    def requested_reason(self) -> str:
        """회기 예약·일정 변경 요청 시 내담자가 입력한 내용."""
        apt = self.pending_appointment
        if apt is None and self.appointment and self.appointment.status == AppointmentStatus.PENDING:
            apt = self.appointment
        if apt and apt.request_message:
            return apt.request_message.strip()
        if self.schedule_change_request and self.schedule_change_request.message:
            return self.schedule_change_request.message.strip()
        return ""

    @property
    def is_client_cancel_completed(self) -> bool:
        """내담자 취소 요청 → 상담사 승인으로 확정된 회기."""
        apt = self.rejected_appointment
        return bool(apt and apt.cancel_requested_at)

    @property
    def is_counselor_direct_cancel_completed(self) -> bool:
        """상담사가 확정 예약을 직접 취소한 회기."""
        apt = self.rejected_appointment
        if not apt or apt.cancel_requested_at:
            return False
        return bool(apt.confirmed_at)

    @property
    def has_cancel_completed_notice(self) -> bool:
        return self.is_client_cancel_completed or self.is_counselor_direct_cancel_completed

    @property
    def is_counselor_rejection_notice(self) -> bool:
        """상담사가 대기 중 예약을 반려한 회기."""
        apt = self.rejected_appointment
        if not apt or not (apt.cancel_reason or "").strip():
            return False
        if apt.cancel_requested_at or apt.confirmed_at:
            return False
        return True

    @property
    def has_rejection_notice(self) -> bool:
        return self.is_counselor_rejection_notice

    @property
    def has_client_cancel_notice(self) -> bool:
        return self.has_cancel_completed_notice

    @property
    def rejection_reason(self) -> str:
        if self.rejected_appointment and self.rejected_appointment.cancel_reason:
            return self.rejected_appointment.cancel_reason.strip()
        return ""

    @property
    def client_cancel_reason(self) -> str:
        if self.has_cancel_completed_notice and self.rejected_appointment:
            return (self.rejected_appointment.cancel_reason or "").strip()
        return ""

    @property
    def show_session_actions(self) -> bool:
        """자료 첨부 — 완료·취소·노쇼 회기 제외."""
        if self.status_code in ("COMPLETED", "CANCELLED", "NO_SHOW"):
            return False
        if self.appointment and self.appointment.status in (
            AppointmentStatus.COMPLETED,
            AppointmentStatus.CANCELLED,
            AppointmentStatus.NO_SHOW,
        ):
            return False
        return True

    @property
    def show_client_materials_section(self) -> bool:
        """내담자 회기 카드 — 첨부 자료 목록·업로드 영역."""
        if self.has_materials:
            return True
        return self.show_session_actions

    @property
    def is_confirmed(self) -> bool:
        """예약 확정(CONFIRMED) 회기 여부."""
        return (
            self.appointment is not None
            and self.appointment.status == AppointmentStatus.CONFIRMED
        )

    @property
    def schedule_action_label(self) -> str:
        """일정 버튼 명칭 — 확정: 변경, 예약 전·대기: 예약."""
        if self.is_confirmed:
            return "예약일정 변경"
        return "상담일정 예약"

    @property
    def client_status_label(self) -> str:
        """내담자·상담사 상담 상세 회기 상태 배지 문구."""
        if self.has_cancel_completed_notice:
            return "취소 완료"
        if self.has_rejection_notice:
            return "반려"
        labels = {
            "REQUESTED": "예약 요청중",
            "CHANGE_REQUESTED": "일정 변경 요청 중",
            "PLANNED": "예정",
            "COMPLETED": "상담종료",
            "CANCELLED": "취소 완료",
            "REJECTED": "반려",
            "CONFIRMED": "예약 확정",
        }
        return labels.get(self.status_code, self.status_label)

    @property
    def show_schedule_change(self) -> bool:
        """일정 변경·예약 요청 — 예정·확정·대기·취소 완료·반려 후 재예약."""
        if self.status_code in ("COMPLETED", "NO_SHOW"):
            return False
        if (
            self.is_client_cancel_completed
            or self.is_counselor_direct_cancel_completed
            or self.is_counselor_rejection_notice
        ):
            return True
        if self.status_code in ("CANCELLED", "REJECTED"):
            return False
        if self.appointment and self.appointment.status in (
            AppointmentStatus.COMPLETED,
            AppointmentStatus.CANCELLED,
            AppointmentStatus.NO_SHOW,
        ):
            return False
        if self.status_code == "PLANNED":
            return True
        if self.appointment and self.appointment.status in (
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.PENDING,
            AppointmentStatus.SCHEDULED,
            AppointmentStatus.CANCEL_PENDING,
        ):
            return True
        return False

    @property
    def schedule_change_blocked(self) -> bool:
        """확정·기타 비대기 예약 — 24시간 이내·이미 지난 경우 변경 불가."""
        if not self.appointment:
            return False
        if self.appointment.status == AppointmentStatus.PENDING:
            return False
        return client_change_blocked(self.appointment)

    @property
    def confirmed_actions_blocked(self) -> bool:
        """확정 회기 일정 변경·취소 — 24시간 이내·이미 지난 예약."""
        if not self.is_confirmed or not self.appointment:
            return False
        return client_change_blocked(self.appointment)

    @property
    def has_session_cancel_pending(self) -> bool:
        return (
            self.appointment is not None
            and self.appointment.status == AppointmentStatus.CANCEL_PENDING
        )

    @property
    def show_counselor_cancel_review_actions(self) -> bool:
        """상담사 — 취소 요청 승인/반려."""
        return self.has_session_cancel_pending

    @property
    def show_counselor_direct_cancel(self) -> bool:
        """상담사 — 확정 예약 직접 취소."""
        if not self.counselor_assigned or not self.is_confirmed:
            return False
        if self.has_session_cancel_pending:
            return False
        if self.appointment and is_appointment_in_past(self.appointment):
            return False
        return True

    @property
    def show_counselor_schedule_change_review_actions(self) -> bool:
        """상담사 — 확정 회기 일정 변경 요청 승인/반려."""
        return (
            self.status_code == "CHANGE_REQUESTED"
            and self.schedule_change_request is not None
            and self.is_confirmed
        )

    @property
    def schedule_change_preferred_datetime(self):
        if self.schedule_change_request:
            return self.schedule_change_request.preferred_datetime
        return None

    @property
    def schedule_change_requested_at(self):
        if self.schedule_change_request:
            return self.schedule_change_request.created_at
        return None

    @property
    def confirmed_appointment_datetime(self):
        """확정 예약의 현재(기존) 일시."""
        if self.appointment and self.appointment.status == AppointmentStatus.CONFIRMED:
            return self.appointment.scheduled_at
        return None

    @property
    def cancel_request_reason(self) -> str:
        if not self.has_session_cancel_pending or not self.appointment:
            return ""
        return (self.appointment.cancel_reason or "").strip()

    @property
    def cancel_requested_at(self):
        if self.has_session_cancel_pending and self.appointment:
            return self.appointment.cancel_requested_at
        return None

    @property
    def show_pending_session_actions(self) -> bool:
        """예약 요청 중 회기 — 일정 수정·요청 철회."""
        if self.has_session_cancel_pending:
            return False
        if self.status_code != "REQUESTED":
            return False
        if self.appointment is None:
            return False
        return self.appointment.status == AppointmentStatus.PENDING

    @property
    def show_confirmed_session_actions(self) -> bool:
        """예약 확정 회기 — 일정 변경·취소 요청 버튼."""
        if not self.is_confirmed:
            return False
        if self.has_session_cancel_pending:
            return False
        if self.status_code == "CHANGE_REQUESTED":
            return False
        return True

    @property
    def show_schedule_change_booking(self) -> bool:
        """미확정·취소 완료·반려 후 — 상담일정 예약 버튼."""
        if self.show_pending_session_actions:
            return False
        if (
            self.is_client_cancel_completed
            or self.is_counselor_direct_cancel_completed
            or self.is_counselor_rejection_notice
        ):
            return True
        return self.show_schedule_change and not self.is_confirmed

    @property
    def show_zoom(self) -> bool:
        return (
            self.appointment is not None
            and self.appointment.status == AppointmentStatus.CONFIRMED
            and self.counseling_method == CounselingMethod.REMOTE
            and bool(self.zoom_url)
        )

    @property
    def show_zoom_host_key_help(self) -> bool:
        """상담사 — Claim Host 안내 (비대면 확정 회기 + 호스트 키 설정 시)."""
        from apps.scheduling.utils import is_zoom_host_key_configured

        return self.show_zoom and is_zoom_host_key_configured()

    @property
    def zoom_host_key(self) -> str:
        from apps.scheduling.utils import get_zoom_host_key

        if not self.show_zoom_host_key_help:
            return ""
        return get_zoom_host_key()

    @property
    def show_counselor_journal(self) -> bool:
        """상담사: 내담자 매칭(담당 배정) 후 일지 작성·열람."""
        return self.counselor_assigned

    @property
    def counselor_journal_label(self) -> str:
        if self.journal and not self.journal.is_draft:
            return "상담일지 보기"
        return "상담일지 작성"

    @property
    def show_initial_record(self) -> bool:
        """상담사: 1회기에서 초기상담 기록지 작성·열람 (매칭 후)."""
        return self.session_number == 1 and self.counselor_assigned

    @property
    def initial_record_label(self) -> str:
        if self.initial_record and not self.initial_record.is_draft:
            return "초기상담 기록지 보기"
        if self.initial_record:
            return "초기상담 기록지 이어쓰기"
        return "초기상담 기록지 작성"

    @property
    def show_termination_record(self) -> bool:
        """상담사: 마지막 회기에서 종결기록지 작성·열람 (매칭 후)."""
        if not self.counselor_assigned or self.total_sessions < 1:
            return False
        return self.session_number == self.total_sessions

    @property
    def termination_record_label(self) -> str:
        if self.termination_record and not self.termination_record.is_draft:
            return "종결기록지 보기"
        if self.termination_record:
            return "종결기록지 이어쓰기"
        return "종결기록지 작성"

    @property
    def counselor_can_update_status(self) -> bool:
        if self.appointment is None:
            return False
        return self.appointment.status in (
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.COMPLETED,
        )

    @property
    def counselor_status_form_value(self) -> str:
        if not self.appointment:
            return "PLANNED"
        if self.appointment.status == AppointmentStatus.COMPLETED:
            return "COMPLETED"
        if self.appointment.status == AppointmentStatus.CONFIRMED:
            return "CONFIRMED"
        return self.appointment.status


def _session_status_for_appointment(
    appointment: Optional[Appointment],
    *,
    schedule_change_request: Optional[SessionScheduleChangeRequest] = None,
) -> tuple[str, str]:
    if appointment is None:
        if schedule_change_request:
            return "REQUESTED", "예약 요청 중"
        return "PLANNED", "예정"
    status = appointment.status
    if status == AppointmentStatus.COMPLETED:
        return "COMPLETED", "완료"
    if status == AppointmentStatus.CANCELLED:
        return "CANCELLED", "취소"
    if status == AppointmentStatus.NO_SHOW:
        return "NO_SHOW", "노쇼"
    if status == AppointmentStatus.CONFIRMED:
        if schedule_change_request:
            return "CHANGE_REQUESTED", "일정 변경 요청 중"
        return "CONFIRMED", "예약 확정"
    if status == AppointmentStatus.PENDING:
        return "REQUESTED", "예약 요청 중"
    if status == AppointmentStatus.CANCEL_PENDING:
        return status, appointment.get_status_display()
    if status == AppointmentStatus.SCHEDULED:
        return "SCHEDULED", "예약"
    return status, appointment.get_status_display()


def _map_appointments_to_sessions(
    appointments: list[Appointment],
    total: int,
    journals_by_appointment: dict,
) -> dict[int, Appointment]:
    """예약을 회기 번호에 매핑 — session_number · 일지 회차 · 빈 슬롯 순."""
    by_session: dict[int, Appointment] = {}
    unassigned: list[Appointment] = []

    for apt in appointments:
        if apt.session_number and 1 <= apt.session_number <= total:
            key = apt.session_number
            if key not in by_session:
                by_session[key] = apt
            else:
                unassigned.append(apt)
            continue
        journal = journals_by_appointment.get(apt.pk)
        if journal and 1 <= journal.session_number <= total:
            key = journal.session_number
            if key not in by_session:
                by_session[key] = apt
            else:
                unassigned.append(apt)
            continue
        unassigned.append(apt)

    for apt in unassigned:
        for session_num in range(1, total + 1):
            if session_num not in by_session:
                by_session[session_num] = apt
                break
    return by_session


def _latest_schedule_change_requests(case: Case) -> dict[int, SessionScheduleChangeRequest]:
    """회기별 최신 일정 변경 요청."""
    requests: dict[int, SessionScheduleChangeRequest] = {}
    for req in SessionScheduleChangeRequest.objects.filter(case=case).select_related(
        "appointment", "client"
    ).order_by(
        "-created_at"
    ):
        if req.session_number not in requests:
            requests[req.session_number] = req
    return requests


def _resolve_pending_appointment_for_session(
    session_number: int,
    active_appointments: list[Appointment],
    appointment_by_session: dict[int, Appointment],
) -> Optional[Appointment]:
    """회기별 대기(PENDING) Appointment — session_number·매핑 모두 확인."""
    for apt in active_appointments:
        if apt.status != AppointmentStatus.PENDING:
            continue
        if apt.session_number == session_number:
            return apt
    mapped = appointment_by_session.get(session_number)
    if mapped and mapped.status == AppointmentStatus.PENDING:
        return mapped
    return None


def _is_zoom_host_url(url: str) -> bool:
    """Zoom 호스트(start) URL — 참가 버튼에 사용하지 않음."""
    normalized = (url or "").strip().lower()
    if not normalized:
        return False
    return "/s/" in normalized or "zak=" in normalized


def _resolve_appointment_zoom_url(
    appointment: Optional[Appointment],
    case: Case,
) -> str:
    """참가 join_url — 상담사·내담자 공통."""
    if appointment is None:
        return ""
    zoom = getattr(appointment, "zoom_meeting", None)
    if zoom and zoom.join_url:
        return zoom.join_url
    if case.zoom_meeting_url and not _is_zoom_host_url(case.zoom_meeting_url):
        return case.zoom_meeting_url
    return ""


def build_case_session_cards(case: Case) -> list[CaseSessionCard]:
    """
    사례의 total_sessions만큼 회기 슬롯(1..N)을 생성하고,
    기존 Appointment·일지 데이터를 회차에 매핑합니다.

    Appointment는 회기별로 내담자가 예약 신청할 때마다 1건씩 생성되므로,
    확정 시점에 N개를 미리 만들지 않습니다.
    """
    total = case.total_sessions or 0
    if total < 1:
        return []

    appointments = list(
        Appointment.objects.filter(case=case)
        .select_related("counselor", "zoom_meeting")
        .order_by("scheduled_at", "created_at")
    )
    journals_by_appointment = {
        j.appointment_id: j
        for j in CounselingJournal.objects.filter(
            case=case,
            is_draft=False,
            appointment_id__isnull=False,
        )
    }
    journals_by_number = {
        j.session_number: j
        for j in CounselingJournal.objects.filter(case=case, is_draft=False)
    }
    initial_record = None
    try:
        initial_record = case.initial_counseling_record
    except InitialCounselingRecord.DoesNotExist:
        pass
    termination_record = None
    try:
        termination_record = case.termination_counseling_record
    except TerminationCounselingRecord.DoesNotExist:
        pass
    counselor_assigned = bool(case.counselor_id)

    rejected_by_session: dict[int, Appointment] = {}
    active_appointments: list[Appointment] = []
    for apt in appointments:
        if apt.status == AppointmentStatus.CANCELLED:
            if apt.cancel_reason and apt.session_number:
                sn = apt.session_number
                if sn not in rejected_by_session:
                    rejected_by_session[sn] = apt
            continue
        active_appointments.append(apt)

    appointment_by_session = _map_appointments_to_sessions(
        active_appointments, total, journals_by_appointment
    )
    schedule_requests_by_session = _latest_schedule_change_requests(case)

    appointment_id_to_session = {
        apt.pk: session_num for session_num, apt in appointment_by_session.items()
    }
    materials_by_session: dict[int, list[SessionMaterial]] = {
        n: [] for n in range(1, total + 1)
    }
    seen_material_ids: set = set()
    for material in SessionMaterial.objects.filter(
        Q(case=case) | Q(appointment__case=case),
        is_shared=False,
    ).select_related("uploaded_by", "appointment").order_by("-created_at"):
        session_num = material.session_number
        if not session_num and material.appointment_id:
            session_num = appointment_id_to_session.get(material.appointment_id)
        if not session_num or session_num not in materials_by_session:
            continue
        if material.pk in seen_material_ids:
            continue
        seen_material_ids.add(material.pk)
        materials_by_session[session_num].append(material)

    cards: list[CaseSessionCard] = []
    for session_number in range(1, total + 1):
        appointment = appointment_by_session.get(session_number)
        pending_appointment = _resolve_pending_appointment_for_session(
            session_number,
            active_appointments,
            appointment_by_session,
        )
        if appointment is None and pending_appointment:
            appointment = pending_appointment
        journal = journals_by_number.get(session_number)
        if appointment and not journal:
            journal = journals_by_appointment.get(appointment.pk)
        schedule_request = schedule_requests_by_session.get(session_number)
        rejected = rejected_by_session.get(session_number)
        if appointment is None and rejected:
            rejected_appointment = rejected
        else:
            rejected_appointment = None
        status_code, status_label = _session_status_for_appointment(
            appointment,
            schedule_change_request=schedule_request,
        )
        if appointment is None and rejected_appointment:
            if (
                rejected_appointment.cancel_requested_at
                or rejected_appointment.confirmed_at
            ):
                status_code, status_label = "CANCELLED", "취소 완료"
            elif (rejected_appointment.cancel_reason or "").strip():
                status_code, status_label = "REJECTED", "반려"
            else:
                status_code, status_label = "CANCELLED", "취소"
        cards.append(
            CaseSessionCard(
                session_number=session_number,
                appointment=appointment,
                journal=journal,
                initial_record=initial_record if session_number == 1 else None,
                termination_record=termination_record if session_number == total else None,
                counselor_assigned=counselor_assigned,
                total_sessions=total,
                counseling_method=case.counseling_method,
                zoom_url=_resolve_appointment_zoom_url(appointment, case)
                if appointment
                else "",
                status_code=status_code,
                status_label=status_label,
                materials=tuple(materials_by_session.get(session_number, [])),
                schedule_change_request=schedule_request,
                rejected_appointment=rejected_appointment,
                pending_appointment=pending_appointment,
            )
        )
    return cards


class CounselorSessionCardView:
    """상담사 회기 카드 + 확정/반려용 PENDING Appointment 연결."""

    __slots__ = ("_card", "action_appointment", "cohort_journals")

    def __init__(
        self,
        card: CaseSessionCard,
        action_appointment: Optional[Appointment] = None,
        cohort_journals=(),
    ):
        self._card = card
        self.action_appointment = action_appointment
        self.cohort_journals = tuple(cohort_journals or ())

    def __getattr__(self, name: str):
        return getattr(self._card, name)

    @property
    def show_counselor_materials_section(self) -> bool:
        """상담사 회기 카드 — 첨부 자료·액션 버튼 영역."""
        if self.has_materials:
            return True
        return self.show_session_actions

    @property
    def show_counselor_session_toolbar(self) -> bool:
        """자료/과제 버튼 행 — 모든 회기에 표시."""
        return True

    @property
    def zoom_url(self) -> str:
        """상담사·내담자 공통 join_url 입장."""
        appointment = self.appointment
        if appointment is None:
            return self._card.zoom_url
        return _resolve_appointment_zoom_url(
            appointment,
            appointment.case,
        )

    @property
    def show_counselor_appointment_actions(self) -> bool:
        if self.action_appointment is not None:
            return True
        return self._card.show_counselor_appointment_actions

    @property
    def show_initial_record(self) -> bool:
        return self._card.show_initial_record

    @property
    def initial_record(self):
        return self._card.initial_record

    @property
    def initial_record_label(self) -> str:
        return self._card.initial_record_label

    @property
    def show_termination_record(self) -> bool:
        return self._card.show_termination_record

    @property
    def termination_record(self):
        return self._card.termination_record

    @property
    def termination_record_label(self) -> str:
        return self._card.termination_record_label

    @property
    def has_cohort_journals(self) -> bool:
        return bool(self.cohort_journals)


def build_case_session_cards_cached(case: Case) -> list[CaseSessionCard]:
    """동일 요청·동일 Case 인스턴스에서 회기 카드 빌드를 한 번만 수행."""
    cache_attr = "_kscu_session_cards_cache"
    cached = getattr(case, cache_attr, None)
    if cached is None:
        cached = build_case_session_cards(case)
        setattr(case, cache_attr, cached)
    return cached


def build_counselor_session_views(
    case: Case,
    *,
    prebuilt_cards: list[CaseSessionCard] | None = None,
    cohort_journals_by_session: dict | None = None,
) -> list[CounselorSessionCardView]:
    """상담사 사례 상세 — 회기 카드에 PENDING Appointment를 확실히 연결."""
    cohort_journals_by_session = cohort_journals_by_session or {}
    cards = prebuilt_cards or build_case_session_cards(case)
    pending_apts = list(
        case.appointments.filter(status=AppointmentStatus.PENDING).order_by(
            "session_number", "scheduled_at", "created_at"
        )
    )
    pending_by_session: dict[int, Appointment] = {}
    for apt in pending_apts:
        if apt.session_number and apt.session_number not in pending_by_session:
            pending_by_session[apt.session_number] = apt

    unscoped_pending = [apt for apt in pending_apts if not apt.session_number]
    needs_link = [
        card
        for card in cards
        if card.status_code == "REQUESTED"
        and not (
            card.action_appointment
            or card.pending_appointment
            or pending_by_session.get(card.session_number)
        )
    ]
    for apt in unscoped_pending:
        if not needs_link:
            break
        card = needs_link.pop(0)
        pending_by_session[card.session_number] = apt

    views: list[CounselorSessionCardView] = []
    for card in cards:
        action_apt = (
            card.pending_appointment
            or card.action_appointment
            or pending_by_session.get(card.session_number)
        )
        if action_apt is None and card.status_code == "REQUESTED":
            for apt in pending_apts:
                if apt.session_number == card.session_number:
                    action_apt = apt
                    break
        if action_apt is None and card.status_code == "REQUESTED":
            action_apt = repair_orphan_session_request(case, card)
            if action_apt:
                pending_by_session[card.session_number] = action_apt
        views.append(
            CounselorSessionCardView(
                card,
                action_apt,
                cohort_journals=cohort_journals_by_session.get(card.session_number, ()),
            )
        )
    return views


def sync_orphan_session_requests(
    case: Case,
    cards: list[CaseSessionCard] | None = None,
) -> None:
    """일정 변경 요청만 있는 회기 → PENDING Appointment 동기화."""
    if not SessionScheduleChangeRequest.objects.filter(case=case).exists():
        return
    cards = cards or build_case_session_cards(case)
    for card in cards:
        repair_orphan_session_request(case, card)


def repair_orphan_session_request(
    case: Case, card: CaseSessionCard
) -> Optional[Appointment]:
    """
    SessionScheduleChangeRequest만 있고 Appointment가 없을 때 PENDING 예약 생성.
    (화면 '예약 요청중' ↔ Admin Appointment 불일치 복구)
    """
    if card.status_code != "REQUESTED":
        return None
    existing = Appointment.objects.filter(
        case=case,
        session_number=card.session_number,
        status=AppointmentStatus.PENDING,
    ).first()
    if existing:
        return existing
    schedule_request = card.schedule_change_request
    if not schedule_request or not schedule_request.preferred_datetime:
        return None
    client = schedule_request.client or case.client
    message = (schedule_request.message or "").strip()
    appointment = create_appointment_request(
        case=case,
        client=client,
        scheduled_at=schedule_request.preferred_datetime,
        session_number=card.session_number,
        request_message=message,
        notify=False,
    )
    SessionScheduleChangeRequest.objects.filter(
        case=case,
        session_number=card.session_number,
    ).delete()
    return appointment


def get_case_shared_materials(case: Case):
    """사례 게시판 글 (상담사·관리자 작성, is_shared=True)."""
    return list(
        SessionMaterial.objects.filter(
            case=case,
            is_shared=True,
        )
        .exclude(uploaded_by__role=UserRole.CLIENT)
        .select_related("uploaded_by")
        .order_by("-created_at")
    )


def user_can_manage_board(user, case: Case) -> bool:
    """게시판 작성·수정·삭제 권한 (담당 상담사·관리자)."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.role == UserRole.ADMIN:
        return True
    return user.role == UserRole.COUNSELOR and case.counselor_id == user.pk
