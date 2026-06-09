"""User.role 과 CounselorProfile / ClientProfile 정합성 유지."""

from __future__ import annotations

from apps.accounts.models import CounselorProfile, UserRole


def sync_user_role_profiles(user) -> None:
    """
    역할에 맞는 프로필만 남깁니다.
    - COUNSELOR: CounselorProfile 보장, ClientProfile 제거하지 않음(기존 데이터 보존)
    - CLIENT / ADMIN: CounselorProfile 제거
    """
    if user.role == UserRole.COUNSELOR:
        CounselorProfile.objects.get_or_create(user=user)
        return

    CounselorProfile.objects.filter(user=user).delete()


def remove_orphan_counselor_profiles() -> int:
    """role≠상담사 인데 CounselorProfile만 남은 고아 행 삭제."""
    deleted, _ = (
        CounselorProfile.objects.exclude(user__role=UserRole.COUNSELOR).delete()
    )
    return deleted
