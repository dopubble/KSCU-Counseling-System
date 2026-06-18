"""내담자 계정 및 연관 데이터 완전 삭제."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.db import transaction
from django.db.models import Q

from apps.accounts.models import User, UserRole
from apps.counseling.models import ApplicationStatus, Case, CounselingApplication


@dataclass(frozen=True)
class ClientPurgeTarget:
    name: str
    student_id: str = ""

    def label(self) -> str:
        sid = self.student_id.strip() or "—"
        return f"{self.name} (학번 {sid})"


# 2026-06 신규 신청(매칭 대기) 목록에서 제거 요청된 내담자
WAITING_MATCH_PURGE_JUNE2026: tuple[ClientPurgeTarget, ...] = (
    ClientPurgeTarget("학생테스트2"),
    ClientPurgeTarget("조은혜", "25111106"),
    ClientPurgeTarget("김지혜", "23132005"),
    ClientPurgeTarget("이정희", "22120052"),
    ClientPurgeTarget("김지민", "24111020"),
    ClientPurgeTarget("김장서울", "26111004"),
    ClientPurgeTarget("이예은", "21120065"),
    ClientPurgeTarget("도진실", "25113031"),
    ClientPurgeTarget("최우형", "26106031"),
    ClientPurgeTarget("윤새미", "25113507"),
)


@dataclass
class ClientPurgeMatch:
    target: ClientPurgeTarget
    user: User
    application_count: int
    case_count: int
    active_case_count: int


def _student_id_filter(student_id: str) -> Q:
    normalized = (student_id or "").strip()
    if normalized:
        return Q(client_profile__student_id=normalized)
    return Q(client_profile__student_id="") | Q(client_profile__student_id__isnull=True)


def find_client_users_for_purge(
    targets: Iterable[ClientPurgeTarget],
) -> tuple[list[ClientPurgeMatch], list[ClientPurgeTarget]]:
    """이름·학번으로 내담자 계정을 찾습니다. 동일 학번 없이 이름만 지정 시 복수 매칭 가능."""
    matches: list[ClientPurgeMatch] = []
    missing: list[ClientPurgeTarget] = []
    seen_user_ids: set = set()

    for target in targets:
        qs = (
            User.objects.filter(role=UserRole.CLIENT, name=target.name)
            .filter(_student_id_filter(target.student_id))
            .select_related("client_profile")
            .order_by("created_at")
        )
        users = list(qs)
        if not users:
            missing.append(target)
            continue

        for user in users:
            if user.pk in seen_user_ids:
                continue
            seen_user_ids.add(user.pk)
            cases = Case.objects.filter(client=user)
            matches.append(
                ClientPurgeMatch(
                    target=target,
                    user=user,
                    application_count=CounselingApplication.objects.filter(client=user).count(),
                    case_count=cases.count(),
                    active_case_count=cases.filter(status="ACTIVE").count(),
                )
            )

    return matches, missing


@dataclass
class ClientPurgeResult:
    deleted_users: int
    deleted_applications: int
    deleted_cases: int
    dry_run: bool


def purge_client_users(
    matches: Iterable[ClientPurgeMatch],
    *,
    dry_run: bool = True,
) -> ClientPurgeResult:
    """User 삭제(CASCADE)로 신청·사례·프로필 등 연관 데이터를 함께 제거합니다."""
    match_list = list(matches)
    app_total = sum(m.application_count for m in match_list)
    case_total = sum(m.case_count for m in match_list)

    if dry_run:
        return ClientPurgeResult(
            deleted_users=len(match_list),
            deleted_applications=app_total,
            deleted_cases=case_total,
            dry_run=True,
        )

    deleted_users = 0
    with transaction.atomic():
        for match in match_list:
            match.user.delete()
            deleted_users += 1

    return ClientPurgeResult(
        deleted_users=deleted_users,
        deleted_applications=app_total,
        deleted_cases=case_total,
        dry_run=False,
    )


def purge_waiting_match_clients_june2026(*, dry_run: bool = True) -> ClientPurgeResult:
    matches, missing = find_client_users_for_purge(WAITING_MATCH_PURGE_JUNE2026)
    if missing:
        labels = ", ".join(t.label() for t in missing)
        raise LookupError(f"DB에서 찾지 못한 내담자: {labels}")
    return purge_client_users(matches, dry_run=dry_run)
