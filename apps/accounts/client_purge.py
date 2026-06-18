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
    ClientPurgeTarget("김장서율", "261110004"),
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


def _student_id_variants(student_id: str) -> tuple[str, ...]:
    """학번 표기 차이(261110004 vs 26111004) 대응."""
    normalized = (student_id or "").strip()
    if not normalized:
        return ("",)
    variants: list[str] = [normalized]
    if normalized.isdigit():
        variants.append(normalized.lstrip("0") or "0")
        if len(normalized) < 8:
            variants.append(normalized.zfill(8))
        if len(normalized) < 9:
            variants.append(normalized.zfill(9))
    deduped: list[str] = []
    for item in variants:
        if item not in deduped:
            deduped.append(item)
    return tuple(deduped)


def _users_for_purge_target(target: ClientPurgeTarget) -> list[User]:
    base_qs = User.objects.filter(role=UserRole.CLIENT, name=target.name).select_related(
        "client_profile"
    )
    if not (target.student_id or "").strip():
        return list(base_qs.order_by("created_at"))

    for sid in _student_id_variants(target.student_id):
        users = list(base_qs.filter(_student_id_filter(sid)).order_by("created_at"))
        if users:
            return users

    # 학번이 DB와 다를 때 이름이 유일하면 이름만으로 삭제
    by_name = list(base_qs.order_by("created_at"))
    if len(by_name) == 1:
        return by_name
    return []


def purge_clients_by_name(
    name: str,
    *,
    student_id_variants: tuple[str, ...] = (),
    dry_run: bool = True,
) -> ClientPurgeResult:
    """이름·학번 후보로 내담자를 찾아 완전 삭제."""
    variants = student_id_variants or ("",)
    users: list[User] = []
    seen: set = set()
    for sid in variants:
        for user in _users_for_purge_target(ClientPurgeTarget(name, sid)):
            if user.pk in seen:
                continue
            seen.add(user.pk)
            users.append(user)

    if not users:
        return ClientPurgeResult(0, 0, 0, dry_run)

    matches: list[ClientPurgeMatch] = []
    for user in users:
        cases = Case.objects.filter(client=user)
        matches.append(
            ClientPurgeMatch(
                target=ClientPurgeTarget(name, ""),
                user=user,
                application_count=CounselingApplication.objects.filter(client=user).count(),
                case_count=cases.count(),
                active_case_count=cases.filter(status="ACTIVE").count(),
            )
        )
    return purge_client_users(matches, dry_run=dry_run)


def find_client_users_for_purge(
    targets: Iterable[ClientPurgeTarget],
) -> tuple[list[ClientPurgeMatch], list[ClientPurgeTarget]]:
    """이름·학번으로 내담자 계정을 찾습니다. 동일 학번 없이 이름만 지정 시 복수 매칭 가능."""
    matches: list[ClientPurgeMatch] = []
    missing: list[ClientPurgeTarget] = []
    seen_user_ids: set = set()

    for target in targets:
        users = _users_for_purge_target(target)
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


def purge_waiting_match_clients_june2026(
    *,
    dry_run: bool = True,
    ignore_missing: bool = False,
) -> ClientPurgeResult:
    matches, missing = find_client_users_for_purge(WAITING_MATCH_PURGE_JUNE2026)
    if missing and not ignore_missing:
        labels = ", ".join(t.label() for t in missing)
        raise LookupError(f"DB에서 찾지 못한 내담자: {labels}")
    if not matches:
        return ClientPurgeResult(
            deleted_users=0,
            deleted_applications=0,
            deleted_cases=0,
            dry_run=dry_run,
        )
    return purge_client_users(matches, dry_run=dry_run)
