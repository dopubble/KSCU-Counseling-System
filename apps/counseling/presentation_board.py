"""기수 사례발표 게시판 — 권한·양식 파일."""

from __future__ import annotations

import html
import re
from pathlib import Path

from django.conf import settings
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


PRESENTATION_FILE_PASSWORD_MIN_LENGTH = 4
PRESENTATION_FILE_PASSWORD_NOTICE = (
    "다운로드할 PDF에 설정할 암호를 입력해 주세요. "
    "입력한 암호가 적용된 PDF 파일로 저장됩니다. (4자 이상)"
)

PRESENTATION_BULK_ZIP_PASSWORD_NOTICE = (
    "ZIP 파일에 설정할 암호를 입력해 주세요. "
    "ZIP 안의 PDF 파일에는 별도 암호가 걸리지 않습니다. "
    "Windows 탐색기에서도 압축 해제할 수 있습니다. (4자 이상)"
)

PRESENTATION_COMMENT_ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".hwp", ".hwpx"}
PRESENTATION_COMMENT_ACCEPT_ATTR = ".pdf,.jpg,.jpeg,.hwp,.hwpx"
PRESENTATION_COMMENT_INVALID_TYPE_MESSAGE = (
    "PDF, JPG, HWP(한글) 파일만 업로드할 수 있습니다."
)
PRESENTATION_COMMENT_ATTACHMENT_LABEL = "파일 첨부"


def default_presentation_post_title(author_name: str) -> str:
    name = (author_name or "").strip() or "작성자"
    return f"[사례발표] {name}-수퍼비전보고서"


def user_can_download_presentation_file_without_password(user: User, author_id) -> bool:
    """모든 사용자가 암호 입력 모달을 거친 뒤 다운로드."""
    return False


def requires_presentation_file_password(user: User, author_id) -> bool:
    """게시글(수퍼비전 보고서) PDF — 다운로드 시 암호 설정."""
    return True


def requires_presentation_comment_file_password(user: User, author_id) -> bool:
    """댓글 첨부 파일 — 암호 없이 바로 다운로드."""
    return False


def presentation_comment_file_content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return "application/pdf"
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".hwp":
        return "application/x-hwp"
    if ext == ".hwpx":
        return "application/hwp+zip"
    return "application/octet-stream"


_PRESENTATION_COMMENT_SECTION_RE = re.compile(
    r"^(\s*)("
    r"\d+\.\s*.+|"
    r"\d+\)\s*.+|"
    r"\(\d+\)\s*.+|"
    r"-\s*.+"
    r")(\s*)$"
)


def count_presentation_comment_peers(cohort: int, *, exclude_author_id) -> int:
    """사례개념화 댓글 대상 동기 수(발표자 제외)."""
    return len(get_presentation_comment_peer_user_ids(cohort, exclude_author_id=exclude_author_id))


def get_presentation_comment_peer_user_ids(cohort: int, *, exclude_author_id) -> frozenset:
    """동기 제출 현황 집계 대상 상담사 ID(발표자 제외)."""
    return frozenset(
        CounselorProfile.objects.filter(
            cohort=cohort,
            is_approved=True,
            user__role=UserRole.COUNSELOR,
            user__status=UserStatus.ACTIVE,
        )
        .exclude(user_id=exclude_author_id)
        .values_list("user_id", flat=True)
    )


def count_presentation_peer_submissions(comments, *, peer_user_ids: frozenset) -> int:
    """동기 제출 현황 — 댓글 단 동기 수(발표자·수퍼바이저 제외)."""
    if not peer_user_ids:
        return 0
    return len({comment.author_id for comment in comments if comment.author_id in peer_user_ids})


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


def user_is_supervisor_viewer(user: User) -> bool:
    return bool(user.is_authenticated and user.role == UserRole.SUPERVISOR)


def user_can_browse_all_presentation_cohorts(user: User) -> bool:
    return user_is_platform_staff(user) or user_is_supervisor_viewer(user)


def resolve_viewer_cohort(user: User, *, requested_cohort: int | None = None) -> int | None:
    """열람 대상 기수. 상담사는 본인 기수, 수퍼바이저·관리자는 요청 기수(없으면 전체)."""
    if user_can_browse_all_presentation_cohorts(user):
        return requested_cohort
    if user.role != UserRole.COUNSELOR:
        return None
    return get_counselor_cohort(user)


def user_can_view_presentation_board(user: User, cohort: int) -> bool:
    if not user.is_authenticated:
        return False
    if user_can_browse_all_presentation_cohorts(user):
        return True
    if user.role != UserRole.COUNSELOR:
        return False
    return get_counselor_cohort(user) == cohort


def presentation_board_cohort_options() -> list[int]:
    return list(
        CounselorProfile.objects.exclude(cohort__isnull=True)
        .order_by("-cohort")
        .values_list("cohort", flat=True)
        .distinct()
    )


def require_presentation_board_access(user: User, cohort: int) -> None:
    if not user_can_view_presentation_board(user, cohort):
        raise PermissionDenied("사례발표 게시판 열람 권한이 없습니다.")


def user_can_create_presentation_post(user: User, cohort: int) -> bool:
    if user.role != UserRole.COUNSELOR:
        return False
    return user_can_view_presentation_board(user, cohort)


def user_can_delete_presentation_post(user: User, post: CasePresentationPost) -> bool:
    if user_is_platform_staff(user):
        return True
    return post.author_id == user.pk


def user_can_comment_on_presentation_post(user: User, post: CasePresentationPost) -> bool:
    """게시판 열람 권한이 있는 사용자는 댓글 작성 가능(발표자 포함)."""
    if not user_can_view_presentation_board(user, post.cohort):
        return False
    if user_is_platform_staff(user):
        return True
    if user.role in (UserRole.SUPERVISOR, UserRole.COUNSELOR):
        return True
    return False


def user_can_delete_presentation_comment(user: User, comment: CasePresentationComment) -> bool:
    if user_is_platform_staff(user):
        return True
    return comment.author_id == user.pk


def user_can_edit_presentation_comment(user: User, comment: CasePresentationComment) -> bool:
    """댓글 작성자만 본인 댓글을 수정할 수 있습니다."""
    return comment.author_id == user.pk


def get_presentation_form_path(template_key: str) -> Path:
    meta = PRESENTATION_FORM_TEMPLATES.get(template_key)
    if meta is None:
        raise PermissionDenied("양식을 찾을 수 없습니다.")
    path = PRESENTATION_BOARD_FORM_DIR / meta["filename"]
    if not path.is_file():
        raise PermissionDenied("양식 파일이 서버에 없습니다.")
    return path
