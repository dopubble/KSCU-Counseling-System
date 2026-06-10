"""상담사 과제 제출 upsert 및 기수 연동."""

from __future__ import annotations

import os
from collections import defaultdict

from django.core.exceptions import ValidationError

from apps.documents.models import CounselorAssignmentSubmission


def get_counselor_cohort(counselor) -> int | None:
    """로그인 상담사의 기수. 없으면 None."""
    profile = getattr(counselor, "counselor_profile", None)
    if profile is None:
        return None
    return profile.cohort


def require_counselor_cohort(counselor) -> int:
    cohort = get_counselor_cohort(counselor)
    if cohort is None:
        raise ValidationError(
            "기수 정보가 등록되지 않았습니다. 관리자에게 기수 배정을 요청해 주세요."
        )
    return cohort


def get_cohort_peer_assignments_by_session(
    cohort: int,
    *,
    max_session: int,
) -> dict[int, list[CounselorAssignmentSubmission]]:
    """동일 기수·1..max_session 회차 과제를 한 번의 쿼리로 조회."""
    if max_session < 1:
        return {}

    by_session: dict[int, list[CounselorAssignmentSubmission]] = defaultdict(list)
    peers = (
        CounselorAssignmentSubmission.objects.filter(
            cohort=cohort,
            session_number__gte=1,
            session_number__lte=max_session,
        )
        .select_related(
            "case",
            "case__client",
            "submitted_by",
        )
        .order_by("session_number", "submitted_by__name", "case__case_number")
    )
    for peer in peers:
        by_session[peer.session_number].append(peer)
    return dict(by_session)


def build_assignment_session_slots(case, assignments_qs=None):
    """
    사례의 1..total_sessions 회기별 과제 슬롯.
    각 슬롯: {session_number, session_label, assignment|None}
    """
    if assignments_qs is None:
        assignments_qs = CounselorAssignmentSubmission.objects.filter(case=case)
    by_session = {a.session_number: a for a in assignments_qs}
    total = max(getattr(case, "total_sessions", 0) or 0, 1)
    return [
        {
            "session_number": n,
            "session_label": f"{n}회기",
            "assignment": by_session.get(n),
        }
        for n in range(1, total + 1)
    ]


def upsert_counselor_assignment(
    *,
    case,
    counselor,
    session_number: int,
    title: str,
    note: str,
    file,
) -> tuple[CounselorAssignmentSubmission, bool]:
    """
    사례·회차당 하나의 과제만 유지. 기존 제출이 있으면 파일·메타데이터를 갱신.

    Returns:
        (instance, created) — created=False 이면 덮어쓰기(재제출).
    """
    cohort = require_counselor_cohort(counselor)

    existing = (
        CounselorAssignmentSubmission.objects.filter(
            case=case,
            session_number=session_number,
        )
        .first()
    )
    if existing is None:
        return (
            CounselorAssignmentSubmission.objects.create(
                case=case,
                session_number=session_number,
                title=title,
                note=note,
                file=file,
                submitted_by=counselor,
                cohort=cohort,
            ),
            True,
        )

    if existing.file:
        existing.file.delete(save=False)

    existing.title = title
    existing.note = note
    existing.file = file
    existing.submitted_by = counselor
    existing.cohort = cohort
    existing.save()
    return existing, False


def default_assignment_title(session_number: int, filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename))[0].strip()
    if base:
        return base[:200]
    return f"{session_number}회기 수련 과제"
