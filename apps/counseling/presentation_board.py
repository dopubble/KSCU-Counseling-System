"""기수 사례발표 게시판 — 권한·양식 파일."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied

from apps.accounts.models import User, UserRole
from apps.counseling.cohort_journal_service import get_counselor_cohort
from apps.counseling.models import CasePresentationComment, CasePresentationPost

PRESENTATION_BOARD_FORM_DIR = Path(settings.BASE_DIR) / "data" / "forms"

PRESENTATION_FORM_TEMPLATES = {
    "supervision_report": {
        "filename": "08. (한기상)전문상담사 사례발표보고서.hwp",
        "label": "수퍼비전(사례발표)보고서 양식",
    },
    "conceptualization": {
        "filename": "사례개념화 연습.hwpx",
        "label": "사례개념화보고서 양식",
    },
}


PRESENTATION_BOARD_COMMENT_CONTENT_TEMPLATE = """사례개념화 연습
     
     
호소문제
     
2. 촉발요인
     
3. 부정적 패턴
     
감정
     
사고 자동적 사고
     중간신념 
     핵심신념
     
행동
     
관계
     
4. 유발요인
     
5. 유지요인
     
6. 상담목표
     
7. 상담초점
     
8. 상담전략
     
9. 상담개입
     
10. 예후 및 장애물"""


def user_is_platform_staff(user: User) -> bool:
    return bool(user.is_authenticated and (user.is_superuser or user.role == UserRole.ADMIN))


def resolve_viewer_cohort(user: User, *, requested_cohort: int | None = None) -> int | None:
    """열람 대상 기수. 상담사는 본인 기수만, 관리자는 요청 기수 또는 전체 목록용."""
    if user_is_platform_staff(user):
        return requested_cohort
    if user.role != UserRole.COUNSELOR:
        return None
    return get_counselor_cohort(user)


def user_can_view_presentation_board(user: User, cohort: int) -> bool:
    if not user.is_authenticated:
        return False
    if user_is_platform_staff(user):
        return True
    if user.role != UserRole.COUNSELOR:
        return False
    return get_counselor_cohort(user) == cohort


def require_presentation_board_access(user: User, cohort: int) -> None:
    if not user_can_view_presentation_board(user, cohort):
        raise PermissionDenied("사례발표 게시판 열람 권한이 없습니다.")


def user_can_create_presentation_post(user: User, cohort: int) -> bool:
    return user_can_view_presentation_board(user, cohort)


def user_can_delete_presentation_post(user: User, post: CasePresentationPost) -> bool:
    if user_is_platform_staff(user):
        return True
    return post.author_id == user.pk


def user_can_comment_on_presentation_post(user: User, post: CasePresentationPost) -> bool:
    """발표자(게시글 작성자)는 본인 글에 개념화 댓글 불가."""
    if not user_can_view_presentation_board(user, post.cohort):
        return False
    if user_is_platform_staff(user):
        return True
    if post.author_id == user.pk:
        return False
    return user.role == UserRole.COUNSELOR


def user_can_delete_presentation_comment(user: User, comment: CasePresentationComment) -> bool:
    if user_is_platform_staff(user):
        return True
    return comment.author_id == user.pk


def get_presentation_form_path(template_key: str) -> Path:
    meta = PRESENTATION_FORM_TEMPLATES.get(template_key)
    if meta is None:
        raise PermissionDenied("양식을 찾을 수 없습니다.")
    path = PRESENTATION_BOARD_FORM_DIR / meta["filename"]
    if not path.is_file():
        raise PermissionDenied("양식 파일이 서버에 없습니다.")
    return path
