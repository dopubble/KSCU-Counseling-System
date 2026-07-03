"""기수 사례발표 게시판 — 권한·양식 파일."""

from __future__ import annotations

import html
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import PermissionDenied
from django.utils.safestring import mark_safe

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
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


PRESENTATION_FILE_PASSWORD_MIN_LENGTH = 4
PRESENTATION_BOARD_LEGACY_FILE_DOWNLOAD_PASSWORD = "260706"
PRESENTATION_FILE_PASSWORD_NOTICE = (
    "동기가 올린 파일은 웹에서 암호 확인 후에만 다운로드할 수 있습니다. "
    "한글 파일 열람 암호(예: 260706)를 입력해 주세요."
)


def hash_presentation_file_password(raw_password: str) -> str:
    return make_password((raw_password or "").strip())


def verify_presentation_file_password(raw_password: str, stored_hash: str) -> bool:
    cleaned = (raw_password or "").strip()
    if len(cleaned) < PRESENTATION_FILE_PASSWORD_MIN_LENGTH:
        return False
    if stored_hash:
        return check_password(cleaned, stored_hash)
    return cleaned == PRESENTATION_BOARD_LEGACY_FILE_DOWNLOAD_PASSWORD


def user_can_download_presentation_file_without_password(user: User, author_id) -> bool:
    if user_is_platform_staff(user):
        return True
    return user.pk == author_id


def requires_presentation_file_password(user: User, author_id) -> bool:
    return not user_can_download_presentation_file_without_password(user, author_id)


_PRESENTATION_COMMENT_SECTION_RE = re.compile(
    r"^(\s*)("
    r"사례개념화 연습|"
    r"호소문제|"
    r"감정|"
    r"행동|"
    r"관계|"
    r"사고(?:\s+자동적 사고)?|"
    r"자동적 사고|"
    r"중간신념|"
    r"핵심신념|"
    r"\d+\.\s*.+"
    r")(\s*)$"
)


def count_presentation_comment_peers(cohort: int, *, exclude_author_id) -> int:
    """사례개념화 댓글 대상 동기 수(발표자 제외)."""
    return (
        CounselorProfile.objects.filter(
            cohort=cohort,
            is_approved=True,
            user__role=UserRole.COUNSELOR,
            user__status=UserStatus.ACTIVE,
        )
        .exclude(user_id=exclude_author_id)
        .count()
    )


def format_presentation_comment_content(text: str):
    """사례개념화 댓글 본문 — 항목 라벨 강조, 줄바꿈 유지."""
    if not text:
        return ""
    rendered_lines: list[str] = []
    for line in text.splitlines():
        match = _PRESENTATION_COMMENT_SECTION_RE.match(line)
        if match:
            indent, label, tail = match.groups()
            rendered_lines.append(
                f'{html.escape(indent)}'
                f'<span class="presentation-comment-section-label">{html.escape(label)}</span>'
                f"{html.escape(tail)}"
            )
        else:
            rendered_lines.append(html.escape(line))
    return mark_safe("<br>".join(rendered_lines))


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
