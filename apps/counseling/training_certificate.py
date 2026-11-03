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

from apps.accounts.models import CounselorProfile, SupervisorProfile, UserRole
from apps.counseling.journal_permissions import user_can_browse_counselor_journals
from apps.counseling.models import (
    Case,
    CasePresentationPost,
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


OWN_CASE_SUPERVISION_MINUTES_PER_POST = 90


def own_case_supervision_post_count(counselor) -> int:
    """사례발표게시판에 수련자 본인이 작성한 사례보고서 게시글 수.

    CasePresentationPost 한 건이 1이다. 댓글은 세지 않는다.
    같은 Case에 올린 글도 각각 센다. case_report_submitted_count의
    사례 단위 집계·상한과는 다른 값이다.
    """
    if counselor is None or not getattr(counselor, "pk", None):
        return 0
    return CasePresentationPost.objects.filter(author_id=counselor.pk).count()


def own_case_supervision_time_label(post_count: int) -> str:
    """게시글 1건 = 90분. 0분은 0시간으로 적고, 분이 0이면 분 표기를 빼다."""
    minutes = max(int(post_count or 0), 0) * OWN_CASE_SUPERVISION_MINUTES_PER_POST
    if minutes <= 0:
        shown = "0시간"
    else:
        shown = format_training_minutes(minutes)
    return f"내 사례로 슈퍼비전 받은시간 : {shown}"


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


def _counselor_profile(counselor):
    try:
        return counselor.counselor_profile
    except CounselorProfile.DoesNotExist:
        return None


def _minute_buckets(progress: dict, prefix: str) -> dict:
    return {
        "in_person": int(progress.get(f"{prefix}_in_person_minutes", 0) or 0),
        "remote": int(progress.get(f"{prefix}_remote_minutes", 0) or 0),
        "unknown": int(progress.get(f"{prefix}_unknown_minutes", 0) or 0),
    }


def _frozen_minutes_label(total_minutes: int) -> str:
    """승인 당시 수련시간 표기. 이후 화면 서식 변경과 분리한다."""
    minutes = max(int(total_minutes or 0), 0)
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}시간 {remainder}분"
    if hours:
        return f"{hours}시간"
    return f"{remainder}분"


def _frozen_date_label(day) -> str:
    if not day:
        return "—"
    return f"{day.year}년 {day.month}월 {day.day}일"


def _frozen_time_rows(item_name, in_person, remote, unknown, remark=""):
    parts: list[tuple[int, str]] = []
    if in_person:
        parts.append((int(in_person), "대면"))
    if remote:
        parts.append((int(remote), "비대면"))
    if unknown:
        parts.append((int(unknown), "방식 미확인"))
    if not parts:
        return [[item_name, _frozen_minutes_label(0), "—", remark]]
    rows: list[list[str]] = []
    for index, (minutes, method) in enumerate(parts):
        rows.append(
            [
                item_name if index == 0 else "",
                _frozen_minutes_label(minutes),
                method,
                remark if index == 0 else "",
            ]
        )
    return rows


def _supervisor_label(context: dict) -> str:
    if "supervisor_label" in context:
        return context.get("supervisor_label") or "—"
    for label, value in context.get("identity") or []:
        if label == "수퍼바이저":
            return value or "—"
    return "—"


def _issued_on_label(context: dict) -> str:
    issued_on = context.get("issued_on")
    if issued_on:
        return _frozen_date_label(issued_on)
    return context.get("issued_on_label") or "—"


def build_training_certificate_approval_payload(counselor, context: dict) -> dict:
    """새로 저장하는 승인 실적. 화면 문장과 표 서식은 넣지 않는다."""
    progress = context["progress"]
    profile = _counselor_profile(counselor)
    birth_date = profile.birth_date if profile is not None else None
    issued_on = context.get("issued_on")
    return {
        "schema": "certificate-metrics-v2",
        "counselor_id": str(counselor.pk),
        "name": getattr(counselor, "name", "") or "",
        "birth_date": birth_date.isoformat() if birth_date else "",
        "supervisor": _supervisor_label(context),
        "issued_on": issued_on.isoformat() if issued_on else "",
        "cases_5plus": int(context["counseling_cases_5plus_count"]),
        "case_report_count": int(progress["case_report_submitted_count"]),
        "own_case_supervision_posts": int(context.get("own_case_supervision_posts") or 0),
        "intake_minutes": _minute_buckets(progress, "intake"),
        "counseling_minutes": _minute_buckets(progress, "counseling"),
        "supervision_minutes": _minute_buckets(progress, "supervision"),
    }


def legacy_display_approval_payload(counselor, context: dict, remark: str) -> dict:
    """승인 도입 당시의 확정 문서. 비고 문장만 인자로 받고 나머지 실적은 그대로 넣는다."""
    progress = context["progress"]
    profile = _counselor_profile(counselor)
    birth_date = profile.birth_date if profile is not None else None
    identity = [["성명", getattr(counselor, "name", "") or "—"]]
    if birth_date:
        identity.append(["생년월일", _frozen_date_label(birth_date)])
    identity.append(["수퍼바이저", _supervisor_label(context)])
    intake = _minute_buckets(progress, "intake")
    counseling = _minute_buckets(progress, "counseling")
    supervision = _minute_buckets(progress, "supervision")
    rows: list[list[str]] = []
    rows.extend(_frozen_time_rows("접수면접", **intake))
    rows.extend(_frozen_time_rows("상담자 경험", **counseling, remark=remark))
    rows.extend(_frozen_time_rows("수퍼비전", **supervision))
    rows.append(
        [
            "사례보고서",
            f"{int(progress['case_report_submitted_count'])}사례",
            "비대면",
            "",
        ]
    )
    return {
        "counselor_id": str(counselor.pk),
        "identity": identity,
        "rows": rows,
        "issued_on": _issued_on_label(context),
        "cases_5plus": int(context["counseling_cases_5plus_count"]),
        "case_report_count": int(progress["case_report_submitted_count"]),
        "intake_minutes": intake,
        "counseling_minutes": counseling,
        "supervision_minutes": supervision,
    }


def _hash_payload(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def calculate_training_certificate_hash(counselor, context: dict) -> str:
    return _hash_payload(build_training_certificate_approval_payload(counselor, context))


def legacy_metrics_approval_hash(counselor, context: dict) -> str:
    """own_case_supervision_posts가 없던 certificate-metrics-v1.

    이미 저장된 과거 승인을 재현할 때만 쓴다.
    내 사례 슈퍼비전 시간이 승인 대상에 없었으므로 현재 승인으로는 인정하지 않는다.
    """
    payload = build_training_certificate_approval_payload(counselor, context)
    payload["schema"] = "certificate-metrics-v1"
    payload.pop("own_case_supervision_posts", None)
    return _hash_payload(payload)


def legacy_display_approval_hash(counselor, context: dict, remark: str) -> str:
    return _hash_payload(legacy_display_approval_payload(counselor, context, remark))


def stored_approval_matches_current(stored_hash: str, counselor, context: dict) -> bool:
    """저장된 hash가 현재 certificate-metrics-v2와 같을 때만 참이다.

    v1 실적 hash와 화면 문장을 포함한 과거 hash에는
    내 사례 슈퍼비전 시간이 없으므로 일치로 보지 않는다.
    """
    if not stored_hash:
        return False
    return stored_hash == calculate_training_certificate_hash(counselor, context)


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
    elif stored_approval_matches_current(latest.content_hash, counselor, context):
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
