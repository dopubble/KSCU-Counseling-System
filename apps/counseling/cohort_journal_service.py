"""동일 기수 상담일지 조회 — 수퍼바이저·관리자 열람용."""

from __future__ import annotations

from collections import defaultdict

from apps.accounts.models import CounselorProfile
from apps.sessions_app.models import CounselingJournal, InitialCounselingRecord


def get_counselor_cohort(counselor) -> int | None:
    if counselor is None or not getattr(counselor, "pk", None):
        return None
    return CounselorProfile.objects.filter(user_id=counselor.pk).values_list(
        "cohort", flat=True
    ).first()


def get_cohort_peer_journals_by_session(
    cohort: int,
    *,
    max_session: int,
) -> dict[int, list[CounselingJournal]]:
    """[Deprecated 이름] 담당 기수 상담일지 — allowed_cohorts=[cohort] 로 위임."""
    return get_cohort_journals_for_supervision(
        allowed_cohorts=[cohort],
        max_session=max_session,
    )


def get_cohort_journals_for_supervision(
    *,
    allowed_cohorts: list[int] | None,
    max_session: int,
) -> dict[int, list[CounselingJournal]]:
    """
    완료(비초안) 상담일지를 회차별로 반환.
    allowed_cohorts=None 이면 전체 기수(ADMIN).
    """
    if max_session < 1:
        return {}

    qs = CounselingJournal.objects.filter(
        is_draft=False,
        session_number__gte=1,
        session_number__lte=max_session,
        counselor__counselor_profile__cohort__isnull=False,
    )
    if allowed_cohorts is not None:
        if not allowed_cohorts:
            return {}
        qs = qs.filter(counselor__counselor_profile__cohort__in=allowed_cohorts)

    by_session: dict[int, list[CounselingJournal]] = defaultdict(list)
    journals = qs.select_related(
        "case",
        "case__client",
        "case__application",
        "counselor",
        "counselor__counselor_profile",
    ).order_by("session_number", "counselor__name", "case__case_number")

    for journal in journals:
        by_session[journal.session_number].append(journal)
    return dict(by_session)


def get_cohort_initial_records_for_supervision(
    *,
    allowed_cohorts: list[int] | None,
) -> list[InitialCounselingRecord]:
    """완료(비초안) 초기상담 기록지 목록."""
    qs = InitialCounselingRecord.objects.filter(
        is_draft=False,
        counselor__counselor_profile__cohort__isnull=False,
    )
    if allowed_cohorts is not None:
        if not allowed_cohorts:
            return []
        qs = qs.filter(counselor__counselor_profile__cohort__in=allowed_cohorts)

    return list(
        qs.select_related(
            "case",
            "case__client",
            "case__application",
            "counselor",
            "counselor__counselor_profile",
        ).order_by("counselor__name", "case__case_number")
    )
