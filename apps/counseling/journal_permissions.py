"""상담일지 열람·다운로드 권한 — 작성자 / ADMIN / 담당 SUPERVISOR."""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.accounts.models import SupervisorProfile, UserRole
from apps.counseling.cohort_journal_service import get_counselor_cohort

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.sessions_app.models import CounselingJournal


def user_is_platform_admin(user: "User") -> bool:
    if not user.is_authenticated:
        return False
    return bool(user.is_superuser or user.role == UserRole.ADMIN)


def get_supervisor_assigned_cohorts(user: "User") -> list[int]:
    if not user.is_authenticated or user.role != UserRole.SUPERVISOR:
        return []
    profile = SupervisorProfile.objects.filter(user_id=user.pk).first()
    if profile is None:
        return []
    raw = profile.assigned_cohorts or []
    cohorts: list[int] = []
    for value in raw:
        try:
            cohort = int(value)
        except (TypeError, ValueError):
            continue
        if cohort > 0 and cohort not in cohorts:
            cohorts.append(cohort)
    return sorted(cohorts)


def supervisor_can_access_cohort(user: "User", cohort: int | None) -> bool:
    if cohort is None:
        return False
    if user_is_platform_admin(user):
        return True
    if user.role != UserRole.SUPERVISOR:
        return False
    return cohort in get_supervisor_assigned_cohorts(user)


def user_can_browse_cohort_journals(user: "User") -> bool:
    """기수별 상담일지 목록(수퍼비전 열람) — 상담사(동기)는 불가."""
    if user_is_platform_admin(user):
        return True
    return bool(get_supervisor_assigned_cohorts(user))


def user_can_view_journal(user: "User", journal: "CounselingJournal") -> bool:
    if not user.is_authenticated:
        return False
    if journal.is_draft:
        return journal.case.counselor_id == user.id
    if journal.case.counselor_id == user.id:
        return True
    if user_is_platform_admin(user):
        return True
    if user.role == UserRole.SUPERVISOR:
        author_cohort = get_counselor_cohort(journal.counselor)
        return supervisor_can_access_cohort(user, author_cohort)
    return False


def user_can_download_journal_pdf(user: "User", journal: "CounselingJournal") -> bool:
    return user_can_view_journal(user, journal)


def supervision_cohorts_for_user(user: "User") -> list[int] | None:
    """열람 가능한 기수 목록. ADMIN은 None(전체)."""
    if user_is_platform_admin(user):
        return None
    assigned = get_supervisor_assigned_cohorts(user)
    return assigned or []
