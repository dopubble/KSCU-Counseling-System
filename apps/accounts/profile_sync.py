"""User.role 과 CounselorProfile / ClientProfile 정합성 유지."""

from __future__ import annotations

from apps.accounts.models import CounselorProfile, SupervisorProfile, UserRole


def sync_user_role_profiles(user) -> None:
    """
    역할에 맞는 프로필만 남깁니다.
    - COUNSELOR: CounselorProfile 보장
    - SUPERVISOR: SupervisorProfile 보장
    - CLIENT / ADMIN: CounselorProfile·SupervisorProfile 제거
    """
    if user.role == UserRole.COUNSELOR:
        CounselorProfile.objects.get_or_create(user=user)
        SupervisorProfile.objects.filter(user=user).delete()
        return

    if user.role == UserRole.SUPERVISOR:
        counselor = CounselorProfile.objects.filter(user=user).first()
        migrated_cohorts: list[int] = []
        if counselor and counselor.cohort:
            migrated_cohorts = [int(counselor.cohort)]

        sp, _ = SupervisorProfile.objects.get_or_create(user=user)
        if migrated_cohorts and not (sp.assigned_cohorts or []):
            sp.assigned_cohorts = migrated_cohorts
            sp.save(update_fields=["assigned_cohorts", "updated_at"])

        CounselorProfile.objects.filter(user=user).delete()
        return

    CounselorProfile.objects.filter(user=user).delete()
    SupervisorProfile.objects.filter(user=user).delete()


def remove_orphan_counselor_profiles() -> int:
    """role≠상담사 인데 CounselorProfile만 남은 고아 행 삭제."""
    deleted, _ = (
        CounselorProfile.objects.exclude(user__role=UserRole.COUNSELOR).delete()
    )
    return deleted


def remove_orphan_supervisor_profiles() -> int:
    """role≠수퍼바이저 인데 SupervisorProfile만 남은 고아 행 삭제."""
    deleted, _ = (
        SupervisorProfile.objects.exclude(user__role=UserRole.SUPERVISOR).delete()
    )
    return deleted
