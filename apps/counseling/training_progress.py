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
제출은 이 상담사가 작성하고 본인 담당 사례에 연결된 게시글의 서로 다른 사례 수다.
사례가 비어 있는 기존 글은 제출로 세지 않는다.

공개사례발표회 참여는 본인이 댓글을 단 서로 다른 게시글 수다.

접수면접과 상담자 경험의 1회 인정시간은
TRAINING_CREDIT_MINUTES_PER_SESSION이다.
실제 예약 시간 DEFAULT_APPOINTMENT_DURATION_MINUTES와는 별도다.
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

from apps.accounts.models import CounselorProfile
from apps.counseling.models import (
    Case,
    CasePresentationComment,
    CasePresentationPost,
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


def _supervision_counts(counselor) -> tuple[int, int, int]:
    """취소 제외 총 회차, 완료 회차, 완료 중 출석 회차. 쿼리는 한 번이다."""
    profile = (
        CounselorProfile.objects.filter(user_id=counselor.pk)
        .only("id", "cohort")
        .first()
    )
    if profile is None or profile.cohort is None:
        return 0, 0, 0
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
    )
    return row["total"] or 0, row["completed"] or 0, row["attended"] or 0


def _presentation_participation_count(counselor) -> int:
    row = CasePresentationComment.objects.filter(author_id=counselor.pk).aggregate(
        total=Count("post_id", distinct=True)
    )
    return row["total"] or 0


def get_counselor_training_progress(counselor) -> dict:
    """로그인한 상담사의 수련영역별 부여량·이수량·제출현황."""
    empty_minutes = format_training_minutes(0)
    if counselor is None or not getattr(counselor, "pk", None):
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
            "case_report_required_count": 0,
            "case_report_submitted_count": 0,
            "public_presentation_participation_count": 0,
        }

    flag = IntegerField()
    row = (
        Case.objects.filter(counselor_id=counselor.pk)
        .annotate(
            completed_journal_count=_completed_journal_count(),
            intake_submitted=Exists(
                ConsentDocument.objects.filter(
                    application_id=OuterRef("application_id"),
                    doc_type=ConsentDocType.INTAKE,
                ).exclude(file="")
            ),
            termination_completed=Exists(
                TerminationCounselingRecord.objects.filter(
                    case_id=OuterRef("pk"),
                    is_draft=False,
                )
            ),
            case_report_submitted=Exists(
                CasePresentationPost.objects.filter(
                    case_id=OuterRef("pk"),
                    author_id=counselor.pk,
                )
            ),
        )
        .aggregate(
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
    intake_required = row["intake_required_count"]
    intake_completed = row["intake_completed_count"]
    assigned = row["counseling_assigned_count"]
    journal_completed = row["journal_completed_count"]
    intake_required_minutes = intake_required * TRAINING_CREDIT_MINUTES_PER_SESSION
    intake_completed_minutes = intake_completed * TRAINING_CREDIT_MINUTES_PER_SESSION
    assigned_minutes = assigned * TRAINING_CREDIT_MINUTES_PER_SESSION
    completed_minutes = journal_completed * TRAINING_CREDIT_MINUTES_PER_SESSION
    supervision_total, supervision_completed, supervision_attended = _supervision_counts(
        counselor
    )
    return {
        "intake_required_count": intake_required,
        "intake_completed_count": intake_completed,
        "intake_required_minutes": intake_required_minutes,
        "intake_completed_minutes": intake_completed_minutes,
        "intake_required_label": format_training_minutes(intake_required_minutes),
        "intake_completed_label": format_training_minutes(intake_completed_minutes),
        "counseling_assigned_count": assigned,
        "journal_completed_count": journal_completed,
        "counseling_assigned_minutes": assigned_minutes,
        "counseling_completed_minutes": completed_minutes,
        "counseling_assigned_label": format_training_minutes(assigned_minutes),
        "counseling_completed_label": format_training_minutes(completed_minutes),
        "counseling_progress_percent": _percent(journal_completed, assigned),
        "termination_required_count": row["termination_required_count"],
        "termination_completed_count": row["termination_completed_count"],
        "supervision_total_count": supervision_total,
        "supervision_completed_count": supervision_completed,
        "supervision_required_count": supervision_completed,
        "supervision_attended_count": supervision_attended,
        "supervision_attendance_percent": _percent(
            supervision_attended, supervision_completed
        ),
        "case_report_required_count": intake_required,
        "case_report_submitted_count": row["case_report_submitted_count"],
        "public_presentation_participation_count": _presentation_participation_count(
            counselor
        ),
    }
