"""수련확인증 계산·승인.

접수면접·상담자 경험·5회기 이상 사례는 records_submitted_at이 있는 Case만 인정한다.
수퍼비전·사례보고서·확인증 날짜·수련현황 집계는 기존 정책을 유지한다.
진행방식은 snapshot 우선, legacy NULL은 기존 fallback이다.
"""

import hashlib
import json

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils import timezone

from apps.accounts.models import SupervisorProfile, UserRole
from apps.counseling.journal_permissions import user_can_browse_counselor_journals
from apps.counseling.models import (
    Case,
    CounselingMethod,
    SupervisionAttendanceStatus,
    SupervisionSession,
    SupervisionSessionStatus,
    TrainingCertificateApproval,
)
from apps.counseling.training_progress import (
    TRAINING_CREDIT_MINUTES_PER_SESSION,
    _METHOD_UNKNOWN,
    _effective_method_expr,
    _fold_method_count_rows,
    _intake_submitted_exists,
    _method_fields_from_buckets,
    format_training_minutes,
    get_counselor_training_progress,
)
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.sessions_app.models import CounselingJournal

_UNKNOWN = "UNKNOWN"
_CASES_5PLUS_MIN_JOURNALS = 5


def submitted_cases_for_counselor(counselor):
    """수련확인증에 반영하는 최종 제출 Case. records_submitted_at이 완료 표시다."""
    return Case.objects.filter(
        counselor_id=counselor.pk,
        records_submitted_at__isnull=False,
    )


def submitted_case_count(counselor) -> int:
    if counselor is None or not getattr(counselor, "pk", None):
        return 0
    return submitted_cases_for_counselor(counselor).count()


def case_submission_counts(counselor) -> tuple[int, int]:
    """배정 Case 수와 최종 제출 Case 수.

    총 건수는 상태와 관계없이 counselor에게 배정된 Case다.
    최종 제출 건수는 records_submitted_at이 있는 Case다.
    """
    if counselor is None or not getattr(counselor, "pk", None):
        return 0, 0
    row = Case.objects.filter(counselor_id=counselor.pk).aggregate(
        assigned=Count("pk"),
        submitted=Count("pk", filter=Q(records_submitted_at__isnull=False)),
    )
    return int(row["assigned"] or 0), int(row["submitted"] or 0)


def counseling_cases_5plus_count(counselor) -> int:
    """최종 제출 Case 중 작성완료 상담일지 5개 이상인 건수(Case 단위, draft 제외).

    Count 경로는 Case.journals related_name 기준이며,
    filter는 부모 Case 기준으로 journals__is_draft를 쓴다.
    distinct=True로 journal 행 중복 집계를 막는다.
    records_submitted_at이 없는 Case는 확인증 비고에 넣지 않는다.
    """
    return (
        submitted_cases_for_counselor(counselor)
        .annotate(
            completed_journal_count=Count(
                "journals",
                filter=Q(journals__is_draft=False),
                distinct=True,
            )
        )
        .filter(completed_journal_count__gte=_CASES_5PLUS_MIN_JOURNALS)
        .count()
    )


def counseling_cases_5plus_label(count: int) -> str:
    return f"5회기 이상 지속된 사례 총 {count}건 포함"


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
        .annotate(
            effective_method=_effective_method_expr(
                snapshot_field="intake_counseling_method",
                fallback_field="counseling_method",
            )
        )
        .values_list("effective_method", flat=True)
        .distinct()
    )


def intake_delivery_mode_label(counselor) -> str:
    """접수면접지 사례의 effective 방식 종류(snapshot 우선, legacy는 Case fallback)."""
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
    """작성완료 일지의 effective 방식 종류(snapshot 우선, legacy는 Case fallback)."""
    methods = (
        CounselingJournal.objects.filter(
            counselor_id=counselor.pk,
            case__counselor_id=counselor.pk,
            is_draft=False,
        )
        .annotate(
            effective_method=_effective_method_expr(
                snapshot_field="counseling_method",
                fallback_field="case__counseling_method",
            )
        )
        .values_list("effective_method", flat=True)
        .distinct()
    )
    return format_case_method_kinds(methods)


def certificate_intake_method_minutes(counselor) -> dict[str, int]:
    """최종 제출 Case의 접수면접 방식별 인정분. snapshot 우선, 없으면 Case fallback."""
    rows = (
        submitted_cases_for_counselor(counselor)
        .annotate(intake_submitted=_intake_submitted_exists())
        .filter(intake_submitted=True)
        .annotate(
            effective_method=_effective_method_expr(
                snapshot_field="intake_counseling_method",
                fallback_field="counseling_method",
            )
        )
        .values("effective_method")
        .annotate(total=Count("pk"))
    )
    return _fold_method_count_rows(
        rows,
        method_key="effective_method",
        count_key="total",
        minutes_per=TRAINING_CREDIT_MINUTES_PER_SESSION,
    )


def certificate_journal_method_minutes(counselor) -> dict[str, int]:
    """최종 제출 Case의 작성완료 일지 방식별 인정분. snapshot 우선, 없으면 Case fallback."""
    rows = (
        CounselingJournal.objects.filter(
            counselor_id=counselor.pk,
            case__counselor_id=counselor.pk,
            case__records_submitted_at__isnull=False,
            is_draft=False,
        )
        .annotate(
            effective_method=_effective_method_expr(
                snapshot_field="counseling_method",
                fallback_field="case__counseling_method",
            )
        )
        .values("effective_method")
        .annotate(total=Count("pk"))
    )
    return _fold_method_count_rows(
        rows,
        method_key="effective_method",
        count_key="total",
        minutes_per=TRAINING_CREDIT_MINUTES_PER_SESSION,
    )


def certificate_training_figures(counselor) -> dict:
    """확인증 PDF에 넘길 진행 dict.

    수퍼비전·사례보고서·날짜 원천은 get_counselor_training_progress 그대로다.
    접수면접·상담자 경험 분만 최종 제출 Case로 다시 계산한다.
    반환 dict는 수련현황 화면과 공유하지 않는다.
    """
    base = get_counselor_training_progress(counselor)
    intake = certificate_intake_method_minutes(counselor)
    counseling = certificate_journal_method_minutes(counselor)
    supervision = {
        CounselingMethod.IN_PERSON: base["supervision_in_person_minutes"],
        CounselingMethod.REMOTE: base["supervision_remote_minutes"],
        _METHOD_UNKNOWN: base["supervision_unknown_minutes"],
    }
    figures = dict(base)
    figures.update(
        _method_fields_from_buckets(
            intake=intake,
            counseling=counseling,
            supervision=supervision,
        )
    )
    intake_minutes = sum(intake.values())
    counseling_minutes = sum(counseling.values())
    attended = int(figures["supervision_attended_minutes"] or 0)
    figures["intake_completed_minutes"] = intake_minutes
    figures["intake_completed_count"] = (
        intake_minutes // TRAINING_CREDIT_MINUTES_PER_SESSION
    )
    figures["intake_completed_label"] = format_training_minutes(intake_minutes)
    figures["counseling_completed_minutes"] = counseling_minutes
    figures["journal_completed_count"] = (
        counseling_minutes // TRAINING_CREDIT_MINUTES_PER_SESSION
    )
    figures["counseling_completed_label"] = format_training_minutes(counseling_minutes)
    figures["training_completed_minutes"] = (
        intake_minutes + counseling_minutes + attended
    )
    figures["training_completed_label"] = format_training_minutes(
        figures["training_completed_minutes"]
    )
    return figures


def build_counselor_training_certificate_context(counselor) -> dict:
    """PDF와 승인 hash가 함께 쓰는 확인증 context."""
    from apps.counseling.training_certificate_pdf import build_training_certificate_context

    return build_training_certificate_context(
        counselor,
        certificate_training_figures(counselor),
        certificate_date_for_counselor(counselor),
        supervisor_label=supervisor_label_for_counselor(counselor),
        counseling_cases_5plus_count=counseling_cases_5plus_count(counselor),
    )


def build_training_certificate_approval_payload(counselor, context: dict) -> dict:
    """PDF에 찍히는 논리 데이터. 생성 시각·PDF bytes는 넣지 않는다."""
    progress = context["progress"]
    return {
        "counselor_id": str(counselor.pk),
        "identity": [[label, value] for label, value in context["identity"]],
        "rows": context["rows"],
        "issued_on": context["issued_on_label"],
        "cases_5plus": int(context["counseling_cases_5plus_count"]),
        "case_report_count": int(progress["case_report_submitted_count"]),
        "intake_minutes": {
            "in_person": int(progress["intake_in_person_minutes"]),
            "remote": int(progress["intake_remote_minutes"]),
            "unknown": int(progress["intake_unknown_minutes"]),
        },
        "counseling_minutes": {
            "in_person": int(progress["counseling_in_person_minutes"]),
            "remote": int(progress["counseling_remote_minutes"]),
            "unknown": int(progress["counseling_unknown_minutes"]),
        },
        "supervision_minutes": {
            "in_person": int(progress["supervision_in_person_minutes"]),
            "remote": int(progress["supervision_remote_minutes"]),
            "unknown": int(progress["supervision_unknown_minutes"]),
        },
    }


def calculate_training_certificate_hash(counselor, context: dict) -> str:
    payload = build_training_certificate_approval_payload(counselor, context)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


STATUS_UNAPPROVED = "unapproved"
STATUS_APPROVED = "approved"
STATUS_REAPPROVAL_REQUIRED = "reapproval_required"

STATUS_LABELS = {
    STATUS_UNAPPROVED: "미승인",
    STATUS_APPROVED: "승인완료",
    STATUS_REAPPROVAL_REQUIRED: "재승인 필요",
}

MESSAGE_NO_SUBMITTED_CASE = (
    "최종 제출된 상담사례가 없습니다. "
    "상담사례 최종 제출 후 수퍼바이저 또는 관리자 승인을 받아야 "
    "수련확인증을 출력할 수 있습니다."
)
MESSAGE_UNAPPROVED = (
    "수련확인증은 최종 제출된 상담사례를 기준으로 발급됩니다. "
    "수퍼바이저 또는 관리자 승인 후 출력할 수 있습니다."
)
MESSAGE_REAPPROVAL = (
    "수련실적이 변경되어 재승인이 필요합니다. "
    "수퍼바이저 또는 관리자 승인 후 수련확인증을 출력할 수 있습니다."
)
MESSAGE_APPROVAL_REQUIRES_SUBMITTED_CASE = (
    "최종 제출된 상담사례가 없어 수련확인증을 승인할 수 없습니다."
)


class TrainingCertificateApprovalError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def latest_training_certificate_approval(counselor):
    if counselor is None or not getattr(counselor, "pk", None):
        return None
    return (
        TrainingCertificateApproval.objects.filter(counselor_id=counselor.pk)
        .select_related("approved_by")
        .order_by("-approved_at", "-pk")
        .first()
    )


def _approved_on_label(approval) -> str:
    if approval is None or approval.approved_at is None:
        return ""
    return timezone.localtime(approval.approved_at).strftime("%Y.%m.%d")


def _approved_by_name(approval) -> str:
    if approval is None:
        return ""
    approver = approval.approved_by
    if approver is None:
        return "—"
    return (approver.name or "").strip() or "—"


def counselor_certificate_issue_message(status: str, submitted_case_count: int) -> str:
    if status == STATUS_APPROVED:
        return ""
    if status == STATUS_REAPPROVAL_REQUIRED:
        return MESSAGE_REAPPROVAL
    if submitted_case_count <= 0:
        return MESSAGE_NO_SUBMITTED_CASE
    return MESSAGE_UNAPPROVED


def get_training_certificate_approval_status(counselor, context=None) -> dict:
    """최신 승인 hash와 현재 확인증 hash로 미승인·승인완료·재승인 필요를 가른다."""
    if context is None:
        context = build_counselor_training_certificate_context(counselor)
    content_hash = calculate_training_certificate_hash(counselor, context)
    latest = latest_training_certificate_approval(counselor)
    if latest is None:
        status = STATUS_UNAPPROVED
    elif latest.content_hash == content_hash:
        status = STATUS_APPROVED
    else:
        status = STATUS_REAPPROVAL_REQUIRED
    assigned_count, submitted_count = case_submission_counts(counselor)
    return {
        "status": status,
        "label": STATUS_LABELS[status],
        "assigned_case_count": assigned_count,
        "submitted_case_count": submitted_count,
        "can_issue": status == STATUS_APPROVED,
        "content_hash": content_hash,
        "context": context,
        "latest": latest,
        "approved_by_name": _approved_by_name(latest) if status == STATUS_APPROVED else "",
        "approved_on_label": _approved_on_label(latest) if status == STATUS_APPROVED else "",
        "issue_message": counselor_certificate_issue_message(status, submitted_count),
    }


def _approver_may_approve(user, counselor) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or user.role == UserRole.ADMIN:
        return True
    if user.role != UserRole.SUPERVISOR:
        return False
    return user_can_browse_counselor_journals(user, counselor)


@transaction.atomic
def approve_training_certificate(counselor, approved_by) -> TrainingCertificateApproval:
    """현재 확인증 context의 hash로 승인 행을 추가한다. 기존 행은 지우지 않는다."""
    if not _approver_may_approve(approved_by, counselor):
        raise PermissionDenied("수련확인증을 승인할 수 없습니다.")
    if submitted_case_count(counselor) < 1:
        raise TrainingCertificateApprovalError(MESSAGE_APPROVAL_REQUIRES_SUBMITTED_CASE)
    context = build_counselor_training_certificate_context(counselor)
    content_hash = calculate_training_certificate_hash(counselor, context)
    return TrainingCertificateApproval.objects.create(
        counselor=counselor,
        approved_by=approved_by,
        approved_role=getattr(approved_by, "role", "") or "",
        content_hash=content_hash,
    )
