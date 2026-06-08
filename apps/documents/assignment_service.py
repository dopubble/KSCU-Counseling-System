"""상담사 과제 제출 upsert."""

from __future__ import annotations

from apps.documents.models import CounselorAssignmentSubmission


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
            ),
            True,
        )

    if existing.file:
        existing.file.delete(save=False)

    existing.title = title
    existing.note = note
    existing.file = file
    existing.submitted_by = counselor
    existing.save()
    return existing, False
