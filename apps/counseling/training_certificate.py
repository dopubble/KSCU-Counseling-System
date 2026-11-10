"""수련확인증 계산·승인.

접수면접·상담자 경험·5회기 이상 사례는 records_submitted_at이 있는 Case만 인정한다.
수련확인증의 수퍼비전 인정 시간은 참석 횟수와 사례발표 게시글 수로 계산한다.
이미 저장된 승인 hash와 맞는 과거 확인증은 승인 당시의 회차 시간으로 발급한다.
수련현황의 출석률·회차 저장 시간과 확인증 날짜는 기존 정책을 유지한다.
진행방식은 snapshot 우선, legacy NULL은 기존 fallback이다.
"""

import hashlib
import json

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils import timezone

from apps.accounts.models import CounselorProfile, SupervisorProfile, UserRole
from apps.counseling.journal_permissions import (
    supervisor_can_access_cohort,
    user_can_browse_counselor_journals,
)
from apps.counseling.models import (
    Case,
    CasePresentationPost,
    CounselingMethod,
    SupervisionAttendance,
    SupervisionAttendanceStatus,
    SupervisionSession,
    SupervisionSessionStatus,
    TrainingCertificateApproval,
    TrainingCertificateApprovalRevocation,
)
from apps.counseling.training_progress import (
    TRAINING_CREDIT_MINUTES_PER_SESSION,
    _METHOD_UNKNOWN,
    _effective_method_expr,
    _fold_method_count_rows,
    _supervision_effective_method_expr,
    _intake_submitted_exists,
    _method_fields_from_buckets,
    format_training_minutes,
    get_counselor_training_progress,
)
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.sessions_app.models import CounselingJournal

_UNKNOWN = "UNKNOWN"
_CASES_5PLUS_MIN_JOURNALS = 5


def submitted_cases_for_counselor(counselor, training_enrollment=None):
    """수련확인증에 반영하는 최종 제출 Case. records_submitted_at이 완료 표시다."""
    if counselor is None or not getattr(counselor, "pk", None):
        return Case.objects.none()
    cases = Case.objects.filter(
        counselor_id=counselor.pk,
        records_submitted_at__isnull=False,
    )
    if training_enrollment is not None:
        cases = cases.filter(training_enrollment_id=training_enrollment.pk)
    return cases


def submitted_case_count(counselor, training_enrollment=None) -> int:
    if counselor is None or not getattr(counselor, "pk", None):
        return 0
    return submitted_cases_for_counselor(counselor, training_enrollment).count()


def case_submission_counts(counselor, training_enrollment=None) -> tuple[int, int]:
    """배정 Case 수와 최종 제출 Case 수.

    총 건수는 상태와 관계없이 counselor에게 배정된 Case다.
    최종 제출 건수는 records_submitted_at이 있는 Case다.
    training_enrollment가 있으면 그 수련 단위의 Case만 센다.
    """
    if counselor is None or not getattr(counselor, "pk", None):
        return 0, 0
    cases = Case.objects.filter(counselor_id=counselor.pk)
    if training_enrollment is not None:
        cases = cases.filter(training_enrollment_id=training_enrollment.pk)
    row = cases.aggregate(
        assigned=Count("pk"),
        submitted=Count("pk", filter=Q(records_submitted_at__isnull=False)),
    )
    return int(row["assigned"] or 0), int(row["submitted"] or 0)


def counseling_cases_5plus_count(counselor, training_enrollment=None) -> int:
    """최종 제출 Case 중 작성완료 상담일지 5개 이상인 건수(Case 단위, draft 제외).

    Count 경로는 Case.journals related_name 기준이며,
    filter는 부모 Case 기준으로 journals__is_draft를 쓴다.
    distinct=True로 journal 행 중복 집계를 막는다.
    records_submitted_at이 없는 Case는 확인증 비고에 넣지 않는다.
    """
    return (
        submitted_cases_for_counselor(counselor, training_enrollment)
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


OWN_CASE_SUPERVISION_MINUTES_PER_POST = 120


def own_case_supervision_post_count(counselor, *, cohort=None) -> int:
    """사례발표게시판에 수련자 본인이 작성한 사례보고서 게시글 수.

    CasePresentationPost 한 건이 1이다. 댓글은 세지 않는다.
    같은 Case에 올린 글도 각각 센다. case_report_submitted_count의
    사례 단위 집계·상한과는 다른 값이다.
    """
    if counselor is None or not getattr(counselor, "pk", None):
        return 0
    posts = CasePresentationPost.objects.filter(author_id=counselor.pk)
    if cohort is not None:
        posts = posts.filter(cohort=cohort)
    return posts.count()


_GROUP_GENERAL_MINUTES = 180
_GROUP_PRESENTATION_MINUTES = 90
_METHOD_LABELS = {
    CounselingMethod.IN_PERSON: "대면",
    CounselingMethod.REMOTE: "비대면",
    _METHOD_UNKNOWN: "방식 미확인",
}


def own_case_supervision_time_label(post_count: int) -> str:
    """인정 발표 1회 = 개인 수퍼비전 2시간.

    이 시간은 개인 수퍼비전 항목과 같으며 집단 수퍼비전에 다시 더하지 않는다.
    게시글은 출석 회차에 연결되지 않는다.
    """
    minutes = max(int(post_count or 0), 0) * OWN_CASE_SUPERVISION_MINUTES_PER_POST
    if minutes <= 0:
        shown = "0시간"
    else:
        shown = format_training_minutes(minutes)
    return f"내 사례로 슈퍼비전 받은시간 : {shown}"


def recognized_supervision_credit(attendance_count, presentation_count) -> dict:
    """집단·개인 수퍼비전 인정 시간.

    인정 발표 횟수는 참석 횟수와 게시글 수 중 작은 값이다.
    일반 참석은 3시간, 인정 발표 참석은 집단에서 1.5시간이다.
    개인 수퍼비전은 인정 발표 1회당 2시간이며 집단 시간에 다시 더하지 않는다.
    """
    attendance = max(int(attendance_count or 0), 0)
    presentations = max(int(presentation_count or 0), 0)
    recognized = min(attendance, presentations)
    return {
        "attendance_count": attendance,
        "presentation_count": presentations,
        "recognized_count": recognized,
        "group_minutes": (
            (attendance - recognized) * _GROUP_GENERAL_MINUTES
            + recognized * _GROUP_PRESENTATION_MINUTES
        ),
        "individual_minutes": recognized * OWN_CASE_SUPERVISION_MINUTES_PER_POST,
    }


def _certificate_cohort(counselor, training_enrollment):
    if training_enrollment is not None:
        return training_enrollment.cohort
    profile = _counselor_profile(counselor)
    return getattr(profile, "cohort", None) if profile is not None else None


def _present_method_counts(counselor, cohort) -> dict[str, int]:
    profile = _counselor_profile(counselor)
    if profile is None or cohort is None:
        return {}
    rows = (
        SupervisionAttendance.objects.filter(
            counselor_id=profile.pk,
            status=SupervisionAttendanceStatus.PRESENT,
            session__status=SupervisionSessionStatus.COMPLETED,
            session__cohort=cohort,
        )
        .annotate(effective_method=_supervision_effective_method_expr())
        .values("effective_method")
        .annotate(total=Count("pk"))
    )
    counts: dict[str, int] = {}
    for row in rows:
        count = int(row["total"] or 0)
        if count:
            counts[row["effective_method"]] = count
    return counts


def _zero_supervision_buckets() -> dict[str, int]:
    return {
        CounselingMethod.IN_PERSON: 0,
        CounselingMethod.REMOTE: 0,
        _METHOD_UNKNOWN: 0,
    }


def _write_supervision_buckets(figures: dict, buckets: dict[str, int]) -> None:
    figures["supervision_in_person_minutes"] = buckets[CounselingMethod.IN_PERSON]
    figures["supervision_remote_minutes"] = buckets[CounselingMethod.REMOTE]
    figures["supervision_unknown_minutes"] = buckets[_METHOD_UNKNOWN]
    attended = sum(buckets.values())
    figures["supervision_attended_minutes"] = attended
    completed = (
        int(figures.get("intake_completed_minutes") or 0)
        + int(figures.get("counseling_completed_minutes") or 0)
        + attended
    )
    figures["training_completed_minutes"] = completed
    figures["training_completed_label"] = format_training_minutes(completed)


def _recognized_supervision_buckets(counselor, cohort, credit: dict) -> tuple[dict[str, int], str, bool]:
    """표시 시간과 맞는 방식별 분. 세 번째는 방식별 행으로 나눌지다."""
    buckets = _zero_supervision_buckets()
    counts = _present_method_counts(counselor, cohort)
    if credit["recognized_count"] == 0:
        for method, count in counts.items():
            if method not in buckets:
                method = _METHOD_UNKNOWN
            buckets[method] += count * _GROUP_GENERAL_MINUTES
        if sum(buckets.values()) != credit["group_minutes"]:
            buckets = _zero_supervision_buckets()
            buckets[_METHOD_UNKNOWN] = credit["group_minutes"]
            return buckets, "—", False
        return buckets, "", True
    present_methods = [method for method, count in counts.items() if count]
    if len(present_methods) == 1:
        method = present_methods[0]
        if method not in buckets:
            method = _METHOD_UNKNOWN
        buckets[method] = credit["group_minutes"]
        return buckets, _METHOD_LABELS.get(method, "—"), False
    buckets[_METHOD_UNKNOWN] = credit["group_minutes"]
    return buckets, "—", False


def _drop_supervision_rows(rows: list[list[str]]) -> list[list[str]]:
    kept: list[list[str]] = []
    skipping = False
    for row in rows:
        if row[0] == "수퍼비전":
            skipping = True
            continue
        if skipping and not (row[0] or "").strip():
            continue
        skipping = False
        kept.append(row)
    return kept


def _individual_time_text(presentation_count: int) -> str:
    label = own_case_supervision_time_label(presentation_count)
    return label.split(" : ", 1)[1]


def _recognized_supervision_rows(rows: list[list[str]], credit: dict) -> list[list[str]]:
    from apps.counseling.training_certificate_pdf import _certificate_time_rows

    body = _drop_supervision_rows(rows)
    remark = own_case_supervision_time_label(credit["recognized_count"])
    individual = _individual_time_text(credit["recognized_count"])
    if credit.get("split_methods"):
        renamed = []
        replaced = False
        for row in rows:
            if row[0] == "수퍼비전" and not replaced:
                renamed.append(["집단 수퍼비전", row[1], row[2], remark])
                replaced = True
                continue
            if replaced and not (row[0] or "").strip():
                renamed.append(row)
                continue
            if replaced:
                replaced = False
            renamed.append(row)
        renamed.append(["개인 수퍼비전", individual, "—", ""])
        return renamed
    group_rows = _certificate_time_rows(
        "집단 수퍼비전",
        in_person=0,
        remote=0,
        unknown=credit["group_minutes"] or 0,
        remark=remark,
    )
    if credit["method_label"] and credit["method_label"] != "방식 미확인":
        group_rows[0][2] = credit["method_label"]
    elif credit["group_minutes"]:
        group_rows[0][2] = credit["method_label"] or "—"
    body.extend(group_rows)
    body.append(["개인 수퍼비전", individual, "—", ""])
    return body


def supervisor_label_for_counselor(counselor, cohort=None) -> str:
    """담당 기수 수퍼바이저 이름. 없으면 —.

    cohort를 넘기면 CounselorProfile.cohort 대신 그 기수를 쓴다.
    """
    if cohort is None:
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


def certificate_date_for_counselor(counselor, cohort=None):
    """취소되지 않은 기수 수퍼비전 중 가장 늦은 회차의 현지 날짜. 없으면 None."""
    if cohort is None:
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


def certificate_intake_method_minutes(counselor, training_enrollment=None) -> dict[str, int]:
    """최종 제출 Case의 접수면접 방식별 인정분. snapshot 우선, 없으면 Case fallback."""
    rows = (
        submitted_cases_for_counselor(counselor, training_enrollment)
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


def certificate_journal_method_minutes(counselor, training_enrollment=None) -> dict[str, int]:
    """최종 제출 Case의 작성완료 일지 방식별 인정분. snapshot 우선, 없으면 Case fallback."""
    journals = CounselingJournal.objects.filter(
        counselor_id=counselor.pk,
        case__counselor_id=counselor.pk,
        case__records_submitted_at__isnull=False,
        is_draft=False,
    )
    if training_enrollment is not None:
        journals = journals.filter(case__training_enrollment_id=training_enrollment.pk)
    rows = (
        journals
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


def certificate_training_figures(counselor, training_enrollment=None) -> dict:
    """확인증 PDF에 넘길 진행 dict.

    수퍼비전·사례보고서·날짜 원천은 get_counselor_training_progress 그대로다.
    접수면접·상담자 경험 분만 최종 제출 Case로 다시 계산한다.
    training_enrollment가 있으면 그 수련 단위만 집계한다.
    반환 dict는 수련현황 화면과 공유하지 않는다.
    """
    base = get_counselor_training_progress(
        counselor, training_enrollment=training_enrollment
    )
    intake = certificate_intake_method_minutes(counselor, training_enrollment)
    counseling = certificate_journal_method_minutes(counselor, training_enrollment)
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


def build_counselor_training_certificate_context(
    counselor, training_enrollment=None, *, supervision_credit="recognized"
) -> dict:
    """PDF와 승인 hash가 함께 쓰는 확인증 context.

    training_enrollment가 있으면 그 수련 단위의 실적·기수·수퍼바이저만 넣는다.
    supervision_credit가 duration이면 승인 당시의 회차 시간 합계를 유지한다.
    """
    from apps.counseling.training_certificate_pdf import build_training_certificate_context

    cohort = _certificate_cohort(counselor, training_enrollment)
    presentation_count = (
        own_case_supervision_post_count(counselor, cohort=cohort)
        if training_enrollment is not None
        else own_case_supervision_post_count(counselor)
    )
    figures = certificate_training_figures(counselor, training_enrollment)
    credit = None
    if supervision_credit == "recognized":
        credit = recognized_supervision_credit(
            figures.get("supervision_attended_count"),
            presentation_count,
        )
        buckets, method_label, split_methods = _recognized_supervision_buckets(
            counselor, cohort, credit
        )
        _write_supervision_buckets(figures, buckets)
        credit["method_label"] = method_label
        credit["split_methods"] = split_methods
    context = build_training_certificate_context(
        counselor,
        figures,
        certificate_date_for_counselor(counselor, cohort),
        supervisor_label=supervisor_label_for_counselor(counselor, cohort),
        counseling_cases_5plus_count=counseling_cases_5plus_count(
            counselor, training_enrollment
        ),
        own_case_post_count=presentation_count,
        cohort_label=(f"{cohort}기" if training_enrollment is not None else ""),
    )
    if training_enrollment is not None:
        context["training_enrollment"] = training_enrollment
    if credit is not None:
        context["supervision_credit_mode"] = "recognized"
        context["supervision_credit"] = credit
        context["rows"] = _recognized_supervision_rows(context["rows"], credit)
    return context


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
    payload = {
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
    enrollment = context.get("training_enrollment")
    if context.get("supervision_credit_mode") == "recognized":
        credit = context.get("supervision_credit") or {}
        payload["schema"] = "certificate-metrics-v4"
        payload.pop("own_case_supervision_posts", None)
        payload["group_supervision_minutes"] = credit.get("group_minutes")
        payload["group_supervision_method"] = credit.get("method_label") or ""
        payload["individual_supervision_minutes"] = credit.get("individual_minutes")
        if enrollment is not None:
            payload["training_enrollment_id"] = str(enrollment.pk)
        return payload
    if enrollment is not None:
        payload["schema"] = "certificate-metrics-v3"
        payload["training_enrollment_id"] = str(enrollment.pk)
    return payload


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
    payload.pop("training_enrollment_id", None)
    payload.pop("group_supervision_minutes", None)
    payload.pop("group_supervision_method", None)
    payload.pop("individual_supervision_minutes", None)
    return _hash_payload(payload)


def legacy_display_approval_hash(counselor, context: dict, remark: str) -> str:
    return _hash_payload(legacy_display_approval_payload(counselor, context, remark))


def current_certificate_hashes(counselor, context: dict) -> list[str]:
    """지금 실적으로 인정하는 hash.

    Enrollment context의 현재 값은 certificate-metrics-v3다.
    같은 실적의 certificate-metrics-v2는 이미 저장된 단일 기수 승인만 이어 받기 위해 포함한다.
    v1과 화면 문장 hash는 포함하지 않는다.
    """
    current = calculate_training_certificate_hash(counselor, context)
    hashes = [current]
    if context.get("supervision_credit_mode") == "recognized":
        return hashes
    if context.get("training_enrollment") is None:
        return hashes
    legacy = build_training_certificate_approval_payload(counselor, context)
    legacy["schema"] = "certificate-metrics-v2"
    legacy.pop("training_enrollment_id", None)
    legacy_hash = _hash_payload(legacy)
    if legacy_hash not in hashes:
        hashes.append(legacy_hash)
    return hashes


def stored_approval_matches_current(stored_hash: str, counselor, context: dict) -> bool:
    """저장된 hash가 현재 확인증 hash일 때만 참이다.

    v1 실적 hash와 화면 문장을 포함한 과거 hash에는
    내 사례 슈퍼비전 시간이 없으므로 일치로 보지 않는다.
    """
    if not stored_hash:
        return False
    return stored_hash in current_certificate_hashes(counselor, context)


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


class TrainingCertificateRevocationError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def latest_training_certificate_approval(counselor, training_enrollment=None):
    if counselor is None or not getattr(counselor, "pk", None):
        return None
    rows = TrainingCertificateApproval.objects.filter(counselor_id=counselor.pk)
    if training_enrollment is not None:
        rows = rows.filter(training_enrollment_id=training_enrollment.pk)
    return (
        rows.select_related("approved_by")
        .order_by("-approved_at", "-pk")
        .first()
    )


def _latest_matching_approval(counselor, content_hash, training_enrollment=None):
    """현재 hash와 일치하는 승인 중 가장 최근 행. 더 오래된 동일 hash는 보지 않는다."""
    if counselor is None or not getattr(counselor, "pk", None) or not content_hash:
        return None
    rows = TrainingCertificateApproval.objects.filter(
        counselor_id=counselor.pk,
        content_hash=content_hash,
    )
    if training_enrollment is not None:
        rows = rows.filter(training_enrollment_id=training_enrollment.pk)
    return (
        rows.select_related("approved_by", "revocation__revoked_by")
        .order_by("-approved_at", "-pk")
        .first()
    )


def _latest_enrollment_approval(training_enrollment, hashes):
    """이 Enrollment에서 현재 hash들 중 가장 최근 행. 다른 기수 승인은 보지 않는다."""
    if training_enrollment is None or not hashes:
        return None
    return (
        TrainingCertificateApproval.objects.filter(
            training_enrollment_id=training_enrollment.pk,
            content_hash__in=list(hashes),
        )
        .select_related("approved_by", "revocation__revoked_by")
        .order_by("-approved_at", "-pk")
        .first()
    )


def _resolved_certificate_enrollment(counselor, training_enrollment):
    if training_enrollment is not None:
        if training_enrollment.counselor_id != getattr(counselor, "pk", None):
            raise PermissionDenied("수련확인증을 승인할 수 없습니다.")
        return training_enrollment
    from apps.counseling.training_enrollment import (
        resolve_legacy_counselor_training_enrollment,
    )

    return resolve_legacy_counselor_training_enrollment(counselor)


def _revocation_of(approval):
    if approval is None:
        return None
    try:
        return approval.revocation
    except TrainingCertificateApprovalRevocation.DoesNotExist:
        return None


def current_valid_training_certificate_approval(counselor, context):
    """현재 hash의 최신 승인이 취소되지 않았을 때만 그 행을 반환한다."""
    enrollment = context.get("training_enrollment")
    if enrollment is not None:
        match = _latest_enrollment_approval(
            enrollment, current_certificate_hashes(counselor, context)
        )
    else:
        match = _latest_matching_approval(
            counselor,
            calculate_training_certificate_hash(counselor, context),
        )
    if _revocation_of(match) is not None:
        return None
    return match


def _approved_on_label(approval) -> str:
    if approval is None or approval.approved_at is None:
        return ""
    return timezone.localtime(approval.approved_at).strftime("%Y.%m.%d")


def _person_name(user) -> str:
    if user is None:
        return "—"
    return (user.name or "").strip() or "—"


def _approved_by_name(approval) -> str:
    if approval is None:
        return ""
    return _person_name(approval.approved_by)


def _revoked_by_name(revocation) -> str:
    if revocation is None:
        return ""
    return _person_name(revocation.revoked_by)


def _revoked_at_label(revocation) -> str:
    if revocation is None or revocation.revoked_at is None:
        return ""
    return timezone.localtime(revocation.revoked_at).strftime("%Y-%m-%d %H:%M")


def counselor_certificate_issue_message(status: str, submitted_case_count: int) -> str:
    if status == STATUS_APPROVED:
        return ""
    if status == STATUS_REAPPROVAL_REQUIRED:
        return MESSAGE_REAPPROVAL
    if submitted_case_count <= 0:
        return MESSAGE_NO_SUBMITTED_CASE
    return MESSAGE_UNAPPROVED


def get_training_certificate_approval_status(
    counselor, context=None, training_enrollment=None
) -> dict:
    """현재 hash의 최신 승인이 취소되지 않았을 때만 승인완료다.

    그 최신 승인이 취소되면 더 오래된 동일 hash 승인으로 되돌리지 않고 미승인이다.
    현재 hash와 맞는 승인이 없으면, 그 Enrollment의 과거 승인이 있을 때 재승인 필요하다.
    다른 Enrollment의 승인은 이 판정에 넣지 않는다.
    취소 여부는 확인증 hash에 넣지 않는다.
    """
    if training_enrollment is None and context is not None:
        training_enrollment = context.get("training_enrollment")
    if training_enrollment is None:
        training_enrollment = _resolved_certificate_enrollment(counselor, None)
    recognized = build_counselor_training_certificate_context(
        counselor, training_enrollment, supervision_credit="recognized"
    )
    duration = build_counselor_training_certificate_context(
        counselor, training_enrollment, supervision_credit="duration"
    )
    if training_enrollment is not None:
        latest = latest_training_certificate_approval(counselor, training_enrollment)
        recognized_match = _latest_enrollment_approval(
            training_enrollment, current_certificate_hashes(counselor, recognized)
        )
        duration_match = _latest_enrollment_approval(
            training_enrollment, current_certificate_hashes(counselor, duration)
        )
    else:
        latest = latest_training_certificate_approval(counselor)
        recognized_match = _latest_matching_approval(
            counselor, calculate_training_certificate_hash(counselor, recognized)
        )
        duration_match = _latest_matching_approval(
            counselor, calculate_training_certificate_hash(counselor, duration)
        )
    if recognized_match is not None:
        match = recognized_match
        context = recognized
    elif duration_match is not None:
        match = duration_match
        context = duration
    else:
        match = None
        context = recognized
    content_hash = calculate_training_certificate_hash(counselor, context)
    revocation = _revocation_of(match)
    if match is not None and revocation is None:
        status = STATUS_APPROVED
        valid = match
    elif match is not None:
        status = STATUS_UNAPPROVED
        valid = None
    elif latest is not None:
        status = STATUS_REAPPROVAL_REQUIRED
        valid = None
    else:
        status = STATUS_UNAPPROVED
        valid = None
    assigned_count, submitted_count = case_submission_counts(
        counselor, training_enrollment
    )
    return {
        "status": status,
        "label": STATUS_LABELS[status],
        "assigned_case_count": assigned_count,
        "submitted_case_count": submitted_count,
        "can_issue": status == STATUS_APPROVED,
        "content_hash": content_hash,
        "context": context,
        "latest": latest,
        "approved_by_name": _approved_by_name(valid) if status == STATUS_APPROVED else "",
        "approved_on_label": _approved_on_label(valid) if status == STATUS_APPROVED else "",
        "revoked_by_name": _revoked_by_name(revocation) if status == STATUS_UNAPPROVED else "",
        "revoked_at_label": _revoked_at_label(revocation) if status == STATUS_UNAPPROVED else "",
        "issue_message": counselor_certificate_issue_message(status, submitted_count),
    }


def _approver_may_approve(user, counselor, training_enrollment=None) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or user.role == UserRole.ADMIN:
        return True
    if user.role != UserRole.SUPERVISOR:
        return False
    if training_enrollment is not None:
        return supervisor_can_access_cohort(user, training_enrollment.cohort)
    return user_can_browse_counselor_journals(user, counselor)


@transaction.atomic
def approve_training_certificate(
    counselor, approved_by, training_enrollment=None
) -> TrainingCertificateApproval:
    """현재 확인증 hash로 승인 행을 추가한다. 기존 행은 지우지 않는다.

    현재 hash의 최신 승인이 아직 유효하면 같은 hash 행을 다시 만들지 않는다.
    그 승인이 취소된 뒤에만 새 행을 추가한다.
    training_enrollment를 주면 그 수련 단위만 승인한다.
    """
    enrollment = _resolved_certificate_enrollment(counselor, training_enrollment)
    if not _approver_may_approve(approved_by, counselor, enrollment):
        raise PermissionDenied("수련확인증을 승인할 수 없습니다.")
    if submitted_case_count(counselor, enrollment) < 1:
        raise TrainingCertificateApprovalError(MESSAGE_APPROVAL_REQUIRES_SUBMITTED_CASE)
    context = build_counselor_training_certificate_context(counselor, enrollment)
    content_hash = calculate_training_certificate_hash(counselor, context)
    if enrollment is not None:
        existing = TrainingCertificateApproval.objects.filter(
            training_enrollment_id=enrollment.pk,
            content_hash__in=current_certificate_hashes(counselor, context),
        )
    else:
        existing = TrainingCertificateApproval.objects.filter(
            counselor_id=counselor.pk,
            content_hash=content_hash,
        )
    existing = existing.order_by("-approved_at", "-pk")
    if connection.features.has_select_for_update:
        existing = existing.select_for_update()
    current = existing.first()
    if current is not None and _revocation_of(current) is None:
        return current
    if enrollment is None:
        from .training_enrollment import (
            ActiveTrainingEnrollmentError,
            require_active_training_enrollment,
        )

        try:
            enrollment = require_active_training_enrollment(counselor)
        except ActiveTrainingEnrollmentError as exc:
            raise TrainingCertificateApprovalError(str(exc)) from exc
    return TrainingCertificateApproval.objects.create(
        counselor=counselor,
        training_enrollment=enrollment,
        approved_by=approved_by,
        approved_role=getattr(approved_by, "role", "") or "",
        content_hash=content_hash,
    )


_REVOCATION_UNAVAILABLE = "취소할 수 있는 유효한 승인이 없습니다."


def _locked_certificate_approval(counselor, enrollment, context):
    content_hash = calculate_training_certificate_hash(counselor, context)
    if enrollment is not None:
        candidates = TrainingCertificateApproval.objects.filter(
            training_enrollment_id=enrollment.pk,
            content_hash__in=current_certificate_hashes(counselor, context),
        )
    else:
        candidates = TrainingCertificateApproval.objects.filter(
            counselor_id=counselor.pk,
            content_hash=content_hash,
        )
    candidates = candidates.order_by("-approved_at", "-pk")
    if connection.features.has_select_for_update:
        candidates = candidates.select_for_update()
    return candidates.first()


@transaction.atomic
def revoke_training_certificate(
    counselor, revoked_by, reason: str = "", training_enrollment=None
):
    """현재 hash의 최신 미취소 승인만 취소한다.

    승인 행의 hash·승인자·승인일시는 바꾸지 않는다.
    이미 취소된 최신 승인이나 과거 hash는 취소하지 않고,
    더 오래된 동일 hash 승인으로 넘어가지도 않는다.
    다른 Enrollment의 승인도 취소하지 않는다.
    행 잠금이 되는 DB에서는 그 최신 승인을 잠근 뒤 취소 이력을 한 번만 만든다.
    """
    enrollment = _resolved_certificate_enrollment(counselor, training_enrollment)
    if not _approver_may_approve(revoked_by, counselor, enrollment):
        raise PermissionDenied("수련확인증 승인을 취소할 수 없습니다.")
    recognized = build_counselor_training_certificate_context(
        counselor, enrollment, supervision_credit="recognized"
    )
    duration = build_counselor_training_certificate_context(
        counselor, enrollment, supervision_credit="duration"
    )
    candidate = _locked_certificate_approval(counselor, enrollment, recognized)
    if candidate is not None and _revocation_of(candidate) is not None:
        raise TrainingCertificateRevocationError(_REVOCATION_UNAVAILABLE)
    if candidate is None:
        candidate = _locked_certificate_approval(counselor, enrollment, duration)
    if candidate is None or _revocation_of(candidate) is not None:
        raise TrainingCertificateRevocationError(_REVOCATION_UNAVAILABLE)
    try:
        return TrainingCertificateApprovalRevocation.objects.create(
            approval=candidate,
            revoked_by=revoked_by,
            revoked_role=getattr(revoked_by, "role", "") or "",
            reason=(reason or "").strip(),
        )
    except IntegrityError as exc:
        raise TrainingCertificateRevocationError(_REVOCATION_UNAVAILABLE) from exc
