"""상담사 수련현황 집계.

작성완료 상담일지와 완료 회기는 서로 다른 기준이다.
작성완료는 상담사의 is_draft=False 일지 전체이고,
완료 회기는 status=COMPLETED 예약만이다.
미작성은 완료 예약 중 그 예약에 연결된 작성완료 일지가 없는 건수다.
appointment가 없는 일지는 완료 회기 수에서 빼거나 더하지 않는다.

네 값은 상담사 행에 스칼라 서브쿼리를 붙인 SQL 한 번으로 구한다.
예약과 일지를 조인해 세지 않으므로 일지가 여러 건이어도 예약이 중복 집계되지 않는다.
"""

from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce

from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import CounselingJournal


def _scalar_count(queryset):
    """조건에 맞는 행 수를 한 칸으로 반환한다. 없으면 0이다."""
    return Coalesce(
        Subquery(
            queryset.order_by()
            .values("counselor_id")
            .annotate(total=Count("pk"))
            .values("total")[:1],
            output_field=IntegerField(),
        ),
        Value(0),
    )


def get_counselor_training_progress(counselor) -> dict:
    """로그인한 상담사의 상담일지 작성 현황.

    journal_completed_count: 이 상담사의 작성완료(is_draft=False) 일지 수.
        예약이 없는 일지와 종결 사례의 일지도 포함한다.
    completed_appointment_count: 이 상담사의 COMPLETED 예약 수.
        일지 유무와 무관하다.
    journal_missing_count: COMPLETED 예약 중 연결된 작성완료 일지가 없는 수.
        예약-일지 조인이 아니라 예약별 EXISTS로 판별한다.
    journal_draft_count: 이 상담사의 임시저장(is_draft=True) 일지 수.
    """
    empty = {
        "journal_completed_count": 0,
        "completed_appointment_count": 0,
        "journal_missing_count": 0,
        "journal_draft_count": 0,
    }
    if counselor is None or not getattr(counselor, "pk", None):
        return empty

    completed_journal_for_appointment = CounselingJournal.objects.filter(
        appointment_id=OuterRef("pk"),
        is_draft=False,
    )
    row = (
        get_user_model()
        .objects.filter(pk=counselor.pk)
        .annotate(
            journal_completed_count=_scalar_count(
                CounselingJournal.objects.filter(
                    counselor_id=OuterRef("pk"),
                    is_draft=False,
                )
            ),
            journal_draft_count=_scalar_count(
                CounselingJournal.objects.filter(
                    counselor_id=OuterRef("pk"),
                    is_draft=True,
                )
            ),
            completed_appointment_count=_scalar_count(
                Appointment.objects.filter(
                    counselor_id=OuterRef("pk"),
                    status=AppointmentStatus.COMPLETED,
                )
            ),
            journal_missing_count=_scalar_count(
                Appointment.objects.filter(
                    counselor_id=OuterRef("pk"),
                    status=AppointmentStatus.COMPLETED,
                )
                .annotate(
                    has_completed_journal=Exists(completed_journal_for_appointment)
                )
                .filter(has_completed_journal=False)
            ),
        )
        .values(
            "journal_completed_count",
            "journal_draft_count",
            "completed_appointment_count",
            "journal_missing_count",
        )
        .get()
    )
    return {
        "journal_completed_count": row["journal_completed_count"],
        "completed_appointment_count": row["completed_appointment_count"],
        "journal_missing_count": row["journal_missing_count"],
        "journal_draft_count": row["journal_draft_count"],
    }
