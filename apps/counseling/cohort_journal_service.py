"""동일 기수 상담일지 조회."""

from __future__ import annotations

from collections import defaultdict

from apps.sessions_app.models import CounselingJournal


def get_counselor_cohort(counselor) -> int | None:
    profile = getattr(counselor, "counselor_profile", None)
    if profile is None:
        return None
    return profile.cohort


def get_cohort_peer_journals_by_session(
    cohort: int,
    *,
    max_session: int,
) -> dict[int, list[CounselingJournal]]:
    """동일 기수·회차별 완료된(비초안) 상담일지."""
    if max_session < 1:
        return {}

    by_session: dict[int, list[CounselingJournal]] = defaultdict(list)
    journals = (
        CounselingJournal.objects.filter(
            is_draft=False,
            session_number__gte=1,
            session_number__lte=max_session,
            counselor__counselor_profile__cohort=cohort,
        )
        .select_related(
            "case",
            "case__client",
            "case__application",
            "counselor",
        )
        .order_by("session_number", "counselor__name", "case__case_number")
    )
    for journal in journals:
        by_session[journal.session_number].append(journal)
    return dict(by_session)
