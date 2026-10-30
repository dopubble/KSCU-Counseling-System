"""상담사 수련현황 집계.

대시보드와 이후 수련확인서가 같은 값을 쓴다.

접수면접은 담당 사례 1건당 접수면접지 1건이다.
완료는 해당 사례 신청에 접수면접지(ConsentDocument, INTAKE) 파일이 있는 경우다.
초기상담기록지 작성 여부는 쓰지 않는다.

상담자 경험의 부여량은 담당 사례 Case.total_sessions 합계다.
조기종결이어도 배정 회기를 빼지 않는다.
완료는 CounselingJournal.is_draft=False 수다.
예약 COMPLETED는 쓰지 않는다.

종결기록지 총량은 담당 사례 수다.
진행 중인 사례도 최종적으로 종결기록지 1건이 필요하므로 포함한다.
완료는 TerminationCounselingRecord가 있고 is_draft=False인 경우다.

수퍼비전 총 회차는 상담사 기수에 등록된 회차 중 취소가 아닌 회차다.
예정 회차도 총 회차에 포함한다.
출석률은 완료 회차 중 출석 수다.
예정·취소 회차는 출석률 분모에서 뺀다.

사례보고서는 담당 사례 1건당 1건이다.
제출은 (A) 본인 담당 사례에 연결된 게시글의 distinct Case 수와
(B) case가 null인 레거시 게시글 수(글 1개=1건)의 합이다.
합산 결과는 required_count(담당 사례 수)를 넘지 않는다.
동일 사례에 연결된 게시글이 여러 개여도 distinct Case 기준으로 1건이다.

공개사례발표회 참여는 본인이 댓글을 단 서로 다른 게시글 수다.

접수면접과 상담자 경험의 1회 인정시간은
TRAINING_CREDIT_MINUTES_PER_SESSION이다.
실제 예약 시간 DEFAULT_APPOINTMENT_DURATION_MINUTES와는 별도다.

수련시간(표기용)은 접수면접 완료분 + 상담자 경험 완료분 + 수퍼비전 출석 인정분의 합이다.
"""

from django.db.models import (
    Case as SqlCase,
    Count,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from apps.accounts.models import CounselorProfile, UserRole
from apps.counseling.models import (
    Case,
    CasePresentationComment,
    CasePresentationPost,
    SupervisionAttendance,
    SupervisionAttendanceStatus,
    SupervisionSession,
    SupervisionSessionStatus,
)
from apps.documents.models import ConsentDocType, ConsentDocument
from apps.sessions_app.models import CounselingJournal, TerminationCounselingRecord

# 수련현황·수련확인서의 1회 인정시간.
# 실제 상담 예약은 DEFAULT_APPOINTMENT_DURATION_MINUTES(50분)를 유지한다.
TRAINING_CREDIT_MINUTES_PER_SESSION = 60


def format_training_minutes(total_minutes: int) -> str:
    """분을 '2시간 30분' 형식으로 나타낸다. 0분은 붙이지 않는다."""
    minutes = max(int(total_minutes or 0), 0)
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}시간 {remainder}분"
    if hours:
        return f"{hours}시간"
    return f"{remainder}분"


def _percent(completed: int, required: int) -> int:
    if required <= 0:
        return 0
    percent = (completed * 100 + required // 2) // required
    return min(percent, 100)


def _completed_journal_count():
    completed = (
        CounselingJournal.objects.filter(
            case_id=OuterRef("pk"),
            counselor_id=OuterRef("counselor_id"),
            is_draft=False,
        )
        .order_by()
        .values("case_id")
        .annotate(total=Count("pk"))
        .values("total")[:1]
    )
    return Coalesce(Subquery(completed, output_field=IntegerField()), Value(0))


def _intake_submitted_exists():
    return Exists(
        ConsentDocument.objects.filter(
            application_id=OuterRef("application_id"),
            doc_type=ConsentDocType.INTAKE,
        ).exclude(file="")
    )


def _termination_completed_exists():
    return Exists(
        TerminationCounselingRecord.objects.filter(
            case_id=OuterRef("pk"),
            is_draft=False,
        )
    )


def _case_report_submitted_exists(counselor_id):
    return Exists(
        CasePresentationPost.objects.filter(
            case_id=OuterRef("pk"),
            author_id=counselor_id,
        )
    )


def _legacy_null_case_report_count(counselor_id) -> int:
    """case=NULL 레거시 사례발표 게시글 수. 글 1개 = 제출 1건."""
    return CasePresentationPost.objects.filter(
        author_id=counselor_id,
        case__isnull=True,
    ).count()


def _cap_case_report_submitted(raw_submitted: int, required: int) -> int:
    if required <= 0:
        return 0
    return min(max(int(raw_submitted or 0), 0), int(required))


def _case_report_submitted_total(*, linked_distinct: int, legacy_null: int, required: int) -> int:
    return _cap_case_report_submitted(
        int(linked_distinct or 0) + int(legacy_null or 0),
        required,
    )


def _empty_training_progress() -> dict:
    empty_minutes = format_training_minutes(0)
    return {
        "intake_required_count": 0,
        "intake_completed_count": 0,
        "intake_required_minutes": 0,
        "intake_completed_minutes": 0,
        "intake_required_label": empty_minutes,
        "intake_completed_label": empty_minutes,
        "counseling_assigned_count": 0,
        "journal_completed_count": 0,
        "counseling_assigned_minutes": 0,
        "counseling_completed_minutes": 0,
        "counseling_assigned_label": empty_minutes,
        "counseling_completed_label": empty_minutes,
        "counseling_progress_percent": 0,
        "termination_required_count": 0,
        "termination_completed_count": 0,
        "supervision_total_count": 0,
        "supervision_completed_count": 0,
        "supervision_required_count": 0,
        "supervision_attended_count": 0,
        "supervision_attendance_percent": 0,
        "supervision_attended_minutes": 0,
        "case_report_required_count": 0,
        "case_report_submitted_count": 0,
        "public_presentation_participation_count": 0,
        "training_completed_minutes": 0,
        "training_completed_label": empty_minutes,
    }


def _build_training_progress(
    *,
    intake_required: int,
    intake_completed: int,
    counseling_assigned: int,
    journal_completed: int,
    termination_required: int,
    termination_completed: int,
    supervision_total: int,
    supervision_completed: int,
    supervision_attended: int,
    supervision_attended_minutes: int,
    case_report_submitted: int,
    public_presentation_participation: int,
) -> dict:
    intake_required_minutes = intake_required * TRAINING_CREDIT_MINUTES_PER_SESSION
    intake_completed_minutes = intake_completed * TRAINING_CREDIT_MINUTES_PER_SESSION
    assigned_minutes = counseling_assigned * TRAINING_CREDIT_MINUTES_PER_SESSION
    completed_minutes = journal_completed * TRAINING_CREDIT_MINUTES_PER_SESSION
    attended_minutes = max(int(supervision_attended_minutes or 0), 0)
    training_completed_minutes = (
        intake_completed_minutes + completed_minutes + attended_minutes
    )
    return {
        "intake_required_count": intake_required,
        "intake_completed_count": intake_completed,
        "intake_required_minutes": intake_required_minutes,
        "intake_completed_minutes": intake_completed_minutes,
        "intake_required_label": format_training_minutes(intake_required_minutes),
        "intake_completed_label": format_training_minutes(intake_completed_minutes),
        "counseling_assigned_count": counseling_assigned,
        "journal_completed_count": journal_completed,
        "counseling_assigned_minutes": assigned_minutes,
        "counseling_completed_minutes": completed_minutes,
        "counseling_assigned_label": format_training_minutes(assigned_minutes),
        "counseling_completed_label": format_training_minutes(completed_minutes),
        "counseling_progress_percent": _percent(journal_completed, counseling_assigned),
        "termination_required_count": termination_required,
        "termination_completed_count": termination_completed,
        "supervision_total_count": supervision_total,
        "supervision_completed_count": supervision_completed,
        "supervision_required_count": supervision_completed,
        "supervision_attended_count": supervision_attended,
        "supervision_attendance_percent": _percent(
            supervision_attended, supervision_completed
        ),
        "supervision_attended_minutes": attended_minutes,
        "case_report_required_count": intake_required,
        "case_report_submitted_count": case_report_submitted,
        "public_presentation_participation_count": public_presentation_participation,
        "training_completed_minutes": training_completed_minutes,
        "training_completed_label": format_training_minutes(training_completed_minutes),
    }


def _case_metric_annotations(counselor_id):
    return {
        "completed_journal_count": _completed_journal_count(),
        "intake_submitted": _intake_submitted_exists(),
        "termination_completed": _termination_completed_exists(),
        "case_report_submitted": _case_report_submitted_exists(counselor_id),
    }


def _aggregate_case_metrics(queryset):
    flag = IntegerField()
    return queryset.aggregate(
        intake_required_count=Count("pk"),
        intake_completed_count=Coalesce(
            Sum(
                SqlCase(
                    When(intake_submitted=True, then=Value(1)),
                    default=Value(0),
                    output_field=flag,
                )
            ),
            Value(0),
        ),
        counseling_assigned_count=Coalesce(Sum("total_sessions"), Value(0)),
        journal_completed_count=Coalesce(Sum("completed_journal_count"), Value(0)),
        termination_required_count=Count("pk"),
        termination_completed_count=Coalesce(
            Sum(
                SqlCase(
                    When(termination_completed=True, then=Value(1)),
                    default=Value(0),
                    output_field=flag,
                )
            ),
            Value(0),
        ),
        case_report_submitted_count=Coalesce(
            Sum(
                SqlCase(
                    When(case_report_submitted=True, then=Value(1)),
                    default=Value(0),
                    output_field=flag,
                )
            ),
            Value(0),
        ),
    )


def _supervision_counts(counselor) -> tuple[int, int, int, int]:
    """취소 제외 총 회차, 완료 회차, 완료 중 출석 회차, 출석 인정분. 쿼리는 최대 두 번이다."""
    profile = (
        CounselorProfile.objects.filter(user_id=counselor.pk)
        .only("id", "cohort")
        .first()
    )
    if profile is None or profile.cohort is None:
        return 0, 0, 0, 0
    row = SupervisionSession.objects.filter(cohort=profile.cohort).aggregate(
        total=Count(
            "pk",
            distinct=True,
            filter=~Q(status=SupervisionSessionStatus.CANCELLED),
        ),
        completed=Count(
            "pk",
            distinct=True,
            filter=Q(status=SupervisionSessionStatus.COMPLETED),
        ),
        attended=Count(
            "pk",
            distinct=True,
            filter=Q(
                status=SupervisionSessionStatus.COMPLETED,
                attendances__counselor_id=profile.pk,
                attendances__status=SupervisionAttendanceStatus.PRESENT,
            ),
        ),
        attended_minutes=Coalesce(
            Sum(
                "duration_minutes",
                filter=Q(
                    status=SupervisionSessionStatus.COMPLETED,
                    attendances__counselor_id=profile.pk,
                    attendances__status=SupervisionAttendanceStatus.PRESENT,
                ),
            ),
            Value(0),
        ),
    )
    return (
        row["total"] or 0,
        row["completed"] or 0,
        row["attended"] or 0,
        row["attended_minutes"] or 0,
    )


def _presentation_participation_count(counselor) -> int:
    row = CasePresentationComment.objects.filter(author_id=counselor.pk).aggregate(
        total=Count("post_id", distinct=True)
    )
    return row["total"] or 0


def get_counselor_training_progress(counselor) -> dict:
    """로그인한 상담사의 수련영역별 부여량·이수량·제출현황."""
    if counselor is None or not getattr(counselor, "pk", None):
        return _empty_training_progress()

    row = _aggregate_case_metrics(
        Case.objects.filter(counselor_id=counselor.pk).annotate(
            **_case_metric_annotations(counselor.pk)
        )
    )
    (
        supervision_total,
        supervision_completed,
        supervision_attended,
        supervision_attended_minutes,
    ) = _supervision_counts(counselor)
    required = row["intake_required_count"]
    case_report_submitted = _case_report_submitted_total(
        linked_distinct=row["case_report_submitted_count"],
        legacy_null=_legacy_null_case_report_count(counselor.pk),
        required=required,
    )
    return _build_training_progress(
        intake_required=required,
        intake_completed=row["intake_completed_count"],
        counseling_assigned=row["counseling_assigned_count"],
        journal_completed=row["journal_completed_count"],
        termination_required=row["termination_required_count"],
        termination_completed=row["termination_completed_count"],
        supervision_total=supervision_total,
        supervision_completed=supervision_completed,
        supervision_attended=supervision_attended,
        supervision_attended_minutes=supervision_attended_minutes,
        case_report_submitted=case_report_submitted,
        public_presentation_participation=_presentation_participation_count(counselor),
    )


def _cohort_case_metrics_by_counselor(user_ids: list) -> dict:
    if not user_ids:
        return {}
    flag = IntegerField()
    rows = (
        Case.objects.filter(counselor_id__in=user_ids)
        .annotate(
            completed_journal_count=_completed_journal_count(),
            intake_submitted=_intake_submitted_exists(),
            termination_completed=_termination_completed_exists(),
            case_report_submitted=Exists(
                CasePresentationPost.objects.filter(
                    case_id=OuterRef("pk"),
                    author_id=OuterRef("counselor_id"),
                )
            ),
        )
        .values("counselor_id")
        .annotate(
            intake_required_count=Count("pk"),
            intake_completed_count=Coalesce(
                Sum(
                    SqlCase(
                        When(intake_submitted=True, then=Value(1)),
                        default=Value(0),
                        output_field=flag,
                    )
                ),
                Value(0),
            ),
            counseling_assigned_count=Coalesce(Sum("total_sessions"), Value(0)),
            journal_completed_count=Coalesce(Sum("completed_journal_count"), Value(0)),
            termination_required_count=Count("pk"),
            termination_completed_count=Coalesce(
                Sum(
                    SqlCase(
                        When(termination_completed=True, then=Value(1)),
                        default=Value(0),
                        output_field=flag,
                    )
                ),
                Value(0),
            ),
            case_report_submitted_count=Coalesce(
                Sum(
                    SqlCase(
                        When(case_report_submitted=True, then=Value(1)),
                        default=Value(0),
                        output_field=flag,
                    )
                ),
                Value(0),
            ),
        )
    )
    return {row["counselor_id"]: row for row in rows}


def _cohort_supervision_totals(cohort: int) -> tuple[int, int]:
    row = SupervisionSession.objects.filter(cohort=cohort).aggregate(
        total=Count(
            "pk",
            distinct=True,
            filter=~Q(status=SupervisionSessionStatus.CANCELLED),
        ),
        completed=Count(
            "pk",
            distinct=True,
            filter=Q(status=SupervisionSessionStatus.COMPLETED),
        ),
    )
    return row["total"] or 0, row["completed"] or 0


def _cohort_supervision_attendance_by_profile(cohort: int, profile_ids: list) -> dict:
    if not profile_ids:
        return {}
    rows = (
        SupervisionAttendance.objects.filter(
            counselor_id__in=profile_ids,
            status=SupervisionAttendanceStatus.PRESENT,
            session__cohort=cohort,
            session__status=SupervisionSessionStatus.COMPLETED,
        )
        .values("counselor_id")
        .annotate(
            attended=Count("session_id", distinct=True),
            attended_minutes=Coalesce(Sum("session__duration_minutes"), Value(0)),
        )
    )
    return {
        row["counselor_id"]: {
            "attended": row["attended"] or 0,
            "attended_minutes": row["attended_minutes"] or 0,
        }
        for row in rows
    }


def _cohort_presentation_participation_by_author(user_ids: list) -> dict:
    if not user_ids:
        return {}
    rows = (
        CasePresentationComment.objects.filter(author_id__in=user_ids)
        .values("author_id")
        .annotate(total=Count("post_id", distinct=True))
    )
    return {row["author_id"]: row["total"] or 0 for row in rows}


def _cohort_legacy_null_case_report_counts(user_ids: list) -> dict:
    """기수 상담사들의 case=NULL 레거시 게시글 수. 한 번의 group by로 집계한다."""
    if not user_ids:
        return {}
    rows = (
        CasePresentationPost.objects.filter(
            author_id__in=user_ids,
            case__isnull=True,
        )
        .values("author_id")
        .annotate(total=Count("pk"))
    )
    return {row["author_id"]: row["total"] or 0 for row in rows}


def get_cohort_counselors_training_progress(cohort: int) -> list[dict]:
    """기수 상담사 수련현황을 일괄 집계한다. N명에 대해 개인 helper를 반복하지 않는다."""
    try:
        cohort_number = int(cohort)
    except (TypeError, ValueError):
        return []
    if cohort_number <= 0:
        return []

    profiles = list(
        CounselorProfile.objects.filter(
            cohort=cohort_number,
            user__role=UserRole.COUNSELOR,
        )
        .select_related("user")
        .order_by("user__name", "user__email")
    )
    if not profiles:
        return []

    user_ids = [profile.user_id for profile in profiles]
    profile_ids = [profile.pk for profile in profiles]
    case_by_user = _cohort_case_metrics_by_counselor(user_ids)
    supervision_total, supervision_completed = _cohort_supervision_totals(cohort_number)
    attendance_by_profile = _cohort_supervision_attendance_by_profile(
        cohort_number, profile_ids
    )
    presentation_by_user = _cohort_presentation_participation_by_author(user_ids)
    legacy_null_by_user = _cohort_legacy_null_case_report_counts(user_ids)

    rows: list[dict] = []
    for profile in profiles:
        case_row = case_by_user.get(profile.user_id, {})
        attendance = attendance_by_profile.get(profile.pk, {})
        required = case_row.get("intake_required_count", 0) or 0
        case_report_submitted = _case_report_submitted_total(
            linked_distinct=case_row.get("case_report_submitted_count", 0) or 0,
            legacy_null=legacy_null_by_user.get(profile.user_id, 0),
            required=required,
        )
        progress = _build_training_progress(
            intake_required=required,
            intake_completed=case_row.get("intake_completed_count", 0) or 0,
            counseling_assigned=case_row.get("counseling_assigned_count", 0) or 0,
            journal_completed=case_row.get("journal_completed_count", 0) or 0,
            termination_required=case_row.get("termination_required_count", 0) or 0,
            termination_completed=case_row.get("termination_completed_count", 0) or 0,
            supervision_total=supervision_total,
            supervision_completed=supervision_completed,
            supervision_attended=attendance.get("attended", 0) or 0,
            supervision_attended_minutes=attendance.get("attended_minutes", 0) or 0,
            case_report_submitted=case_report_submitted,
            public_presentation_participation=presentation_by_user.get(
                profile.user_id, 0
            )
            or 0,
        )
        rows.append(
            {
                "profile": profile,
                "counselor": profile.user,
                "progress": progress,
            }
        )
    return rows
