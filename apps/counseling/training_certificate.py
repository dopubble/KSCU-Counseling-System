"""수련확인증에 그릴 진행방식·수퍼바이저 이름.

수련 건수·시간은 get_counselor_training_progress()를 그대로 쓴다.
진행방식 요약 라벨은 회기/세션 스냅샷 종류만 표시한다.
"""

from django.db.models import Exists, OuterRef, Sum
from django.utils import timezone

from apps.accounts.models import SupervisorProfile
from apps.counseling.models import (
    Case,
    CounselingMethod,
    SupervisionAttendanceStatus,
    SupervisionSession,
    SupervisionSessionStatus,
)
from apps.counseling.training_progress import format_training_minutes
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.sessions_app.models import CounselingJournal

_UNKNOWN = "UNKNOWN"


def supervisor_label_for_counselor(counselor) -> str:
    """담당 기수 수퍼바이저 이름. 없으면 —."""
    profile = getattr(counselor, "counselor_profile", None)
    cohort = profile.cohort if profile is not None else None
    if cohort is None:
        return "—"
    try:
        target = int(cohort)
    except (TypeError, ValueError):
        return "—"
    names: list[str] = []
    seen: set[str] = set()
    profiles = SupervisorProfile.objects.select_related("user").order_by(
        "user__name",
        "user__email",
    )
    for supervisor in profiles:
        assigned: list[int] = []
        for value in supervisor.assigned_cohorts or []:
            try:
                assigned.append(int(value))
            except (TypeError, ValueError):
                continue
        if target not in assigned:
            continue
        name = (supervisor.user.name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    if not names:
        return "—"
    return ", ".join(f"{name} 교수" for name in names)


def _bucket(raw) -> str:
    value = (raw or "").strip()
    if value in (CounselingMethod.IN_PERSON, CounselingMethod.REMOTE):
        return value
    return _UNKNOWN


def format_case_method_kinds(methods) -> str:
    """스냅샷 방식 종류. 건수와 회기 수는 붙이지 않는다."""
    kinds = {_bucket(value) for value in methods}
    if not kinds:
        return "—"
    has_in_person = CounselingMethod.IN_PERSON in kinds
    has_remote = CounselingMethod.REMOTE in kinds
    has_unknown = _UNKNOWN in kinds
    if has_in_person and has_remote and has_unknown:
        return "대면·비대면 혼합 · 일부 확인불가"
    if has_in_person and has_remote:
        return "대면·비대면 혼합"
    if has_in_person and has_unknown:
        return "대면 · 일부 확인불가"
    if has_remote and has_unknown:
        return "비대면 · 일부 확인불가"
    if has_unknown:
        return "확인불가"
    if has_in_person:
        return "대면"
    return "비대면"


def _intake_case_methods(counselor):
    submitted = ConsentDocument.objects.filter(
        application_id=OuterRef("application_id"),
        doc_type=ConsentDocType.INTAKE,
    ).exclude(file="")
    return (
        Case.objects.filter(counselor_id=counselor.pk)
        .filter(Exists(submitted))
        .values_list("intake_counseling_method", flat=True)
        .distinct()
    )


def intake_delivery_mode_label(counselor) -> str:
    """접수면접지가 있는 담당 사례의 접수면접 스냅샷 방식 종류."""
    return format_case_method_kinds(_intake_case_methods(counselor))


def supervision_attended_time_label(counselor) -> str:
    """완료 회차 중 출석(PRESENT)한 duration_minutes 합계."""
    profile = getattr(counselor, "counselor_profile", None)
    cohort = getattr(profile, "cohort", None) if profile is not None else None
    if profile is None or cohort is None:
        return format_training_minutes(0)
    total = (
        SupervisionSession.objects.filter(
            cohort=cohort,
            status=SupervisionSessionStatus.COMPLETED,
            attendances__counselor_id=profile.pk,
            attendances__status=SupervisionAttendanceStatus.PRESENT,
        ).aggregate(total=Sum("duration_minutes"))["total"]
        or 0
    )
    return format_training_minutes(total)


def certificate_date_for_counselor(counselor):
    """취소되지 않은 기수 수퍼비전 중 가장 늦은 회차의 현지 날짜. 없으면 None."""
    profile = getattr(counselor, "counselor_profile", None)
    cohort = getattr(profile, "cohort", None) if profile is not None else None
    if cohort is None:
        return None
    scheduled_at = (
        SupervisionSession.objects.filter(cohort=cohort)
        .exclude(status=SupervisionSessionStatus.CANCELLED)
        .order_by("-scheduled_at")
        .values_list("scheduled_at", flat=True)
        .first()
    )
    if scheduled_at is None:
        return None
    if timezone.is_naive(scheduled_at):
        scheduled_at = timezone.make_aware(scheduled_at, timezone.get_current_timezone())
    return timezone.localtime(scheduled_at).date()


def journal_delivery_mode_label(counselor) -> str:
    """작성완료 일지의 회기 스냅샷 방식 종류."""
    methods = (
        CounselingJournal.objects.filter(
            counselor_id=counselor.pk,
            case__counselor_id=counselor.pk,
            is_draft=False,
        )
        .values_list("counseling_method", flat=True)
        .distinct()
    )
    return format_case_method_kinds(methods)
