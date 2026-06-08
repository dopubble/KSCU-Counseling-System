"""내담자 상담 신청의 주요 호소 문제 일괄 반영."""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User, UserRole
from apps.counseling.client_complaint_seed import (
    CLIENT_COMPLAINT_SEEDS,
    EMAIL_ALIASES,
    LEGACY_TRUNCATED_REASONS,
    clean_reason,
)
from apps.counseling.constants import DEFAULT_COUNSELING_TYPES
from apps.counseling.models import ApplicationStatus, Case, CaseStatus, CounselingApplication
from apps.counseling.seed_applications import create_application_for_client


@dataclass
class ComplaintUpdateResult:
    name: str
    email: str
    action: str
    message: str = ""
    reason: str = ""


@dataclass
class ComplaintUpdateSummary:
    updated: int = 0
    skipped: int = 0
    missing_user: int = 0
    missing_application: int = 0
    errors: int = 0
    results: list[ComplaintUpdateResult] = field(default_factory=list)


def _emails_to_try(email: str) -> list[str]:
    lowered = email.strip().lower()
    candidates = [lowered]
    if lowered in EMAIL_ALIASES:
        candidates.append(EMAIL_ALIASES[lowered].lower())
    for alias, canonical in EMAIL_ALIASES.items():
        if canonical.lower() == lowered and alias.lower() not in candidates:
            candidates.append(alias.lower())
    return candidates


def _find_client_by_email(email: str) -> User | None:
    for candidate in _emails_to_try(email):
        client = User.objects.filter(
            email__iexact=candidate,
            role=UserRole.CLIENT,
        ).first()
        if client:
            return client
    return None


def _find_client_for_seed(seed) -> User | None:
    client = _find_client_by_email(seed.email)
    if client:
        return client
    return User.objects.filter(
        name=seed.name,
        role=UserRole.CLIENT,
    ).first()


def _target_applications_for_client(client) -> list[CounselingApplication]:
    """ACTIVE 사례에 연결된 신청을 우선 포함."""
    apps: list[CounselingApplication] = []
    seen: set = set()

    active_case = (
        Case.objects.filter(client=client, status=CaseStatus.ACTIVE)
        .select_related("application")
        .order_by("-opened_at")
        .first()
    )
    if active_case and active_case.application_id:
        app = active_case.application
        if app.status != ApplicationStatus.CANCELLED:
            apps.append(app)
            seen.add(app.pk)

    for app in CounselingApplication.objects.filter(client=client).exclude(
        status=ApplicationStatus.CANCELLED
    ).order_by("-created_at"):
        if app.pk not in seen:
            apps.append(app)
            seen.add(app.pk)

    return apps


def update_client_complaints(
    *,
    dry_run: bool = True,
    only_default_reason: bool = False,
    create_missing: bool = False,
) -> ComplaintUpdateSummary:
    """
    시드 목록의 주요 호소 문제를 각 내담자 상담 신청(reason)에 반영.

    only_default_reason=True 이면 '관리자 일괄 접수' 등 기본 문구만 덮어씀.
    """
    summary = ComplaintUpdateSummary()
    default_markers = (
        "관리자 일괄 접수",
        "내담자 사전 등록",
    )

    def _should_overwrite(reason: str) -> bool:
        text = clean_reason(reason)
        if not text:
            return True
        if any(marker in text for marker in default_markers):
            return True
        if text in LEGACY_TRUNCATED_REASONS:
            return True
        if only_default_reason:
            return False
        return True

    for seed in CLIENT_COMPLAINT_SEEDS:
        reason = clean_reason(seed.reason)

        client = _find_client_for_seed(seed)
        if not client:
            summary.missing_user += 1
            summary.results.append(
                ComplaintUpdateResult(
                    seed.name,
                    seed.email,
                    "missing_user",
                    "내담자 계정 없음",
                    reason,
                )
            )
            continue

        applications = _target_applications_for_client(client)
        if not applications:
            if create_missing and not dry_run:
                has_active_case = Case.objects.filter(
                    client=client,
                    status=CaseStatus.ACTIVE,
                ).exists()
                if has_active_case:
                    summary.skipped += 1
                    summary.results.append(
                        ComplaintUpdateResult(
                            seed.name,
                            seed.email,
                            "skipped",
                            "ACTIVE 사례 있으나 연결된 신청 없음 — 수동 확인",
                            reason,
                        )
                    )
                    continue
                try:
                    with transaction.atomic():
                        app = create_application_for_client(
                            client,
                            counseling_types=list(DEFAULT_COUNSELING_TYPES),
                            reason=reason,
                        )
                    summary.updated += 1
                    summary.results.append(
                        ComplaintUpdateResult(
                            seed.name,
                            seed.email,
                            "created",
                            "상담 신청 생성",
                            reason,
                        )
                    )
                    continue
                except Exception as exc:
                    summary.errors += 1
                    summary.results.append(
                        ComplaintUpdateResult(
                            seed.name,
                            seed.email,
                            "error",
                            str(exc),
                            reason,
                        )
                    )
                    continue
            if create_missing and dry_run:
                summary.updated += 1
                summary.results.append(
                    ComplaintUpdateResult(
                        seed.name,
                        seed.email,
                        "would_create",
                        "상담 신청 생성 예정",
                        reason,
                    )
                )
                continue

            summary.missing_application += 1
            summary.results.append(
                ComplaintUpdateResult(
                    seed.name,
                    seed.email,
                    "missing_application",
                    "상담 신청 없음",
                    reason,
                )
            )
            continue

        targets = applications
        if only_default_reason:
            targets = [
                app
                for app in applications
                if _should_overwrite(app.reason or "")
            ]
            if not targets:
                summary.skipped += 1
                summary.results.append(
                    ComplaintUpdateResult(
                        seed.name,
                        seed.email,
                        "skipped",
                        "이미 사용자 작성 호소 문제 있음",
                        reason,
                    )
                )
                continue

        if dry_run:
            summary.updated += len(targets)
            summary.results.append(
                ComplaintUpdateResult(
                    seed.name,
                    seed.email,
                    "would_update",
                    f"신청 {len(targets)}건",
                    reason,
                )
            )
            continue

        try:
            with transaction.atomic():
                now = timezone.now()
                for app in targets:
                    app.reason = reason
                    app.updated_at = now
                    app.save(update_fields=["reason", "updated_at"])
                # 사례에 연결되지 않은 신청이 있어도 동일 내담자 전체 신청에 반영
                CounselingApplication.objects.filter(
                    client=client,
                ).exclude(status=ApplicationStatus.CANCELLED).update(
                    reason=reason,
                    updated_at=now,
                )
            summary.updated += len(targets)
            summary.results.append(
                ComplaintUpdateResult(
                    seed.name,
                    seed.email,
                    "updated",
                    f"신청 {len(targets)}건",
                    reason,
                )
            )
        except Exception as exc:
            summary.errors += 1
            summary.results.append(
                ComplaintUpdateResult(
                    seed.name,
                    seed.email,
                    "error",
                    str(exc),
                    reason,
                )
            )

    return summary
