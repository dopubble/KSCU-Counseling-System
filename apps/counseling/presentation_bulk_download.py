"""사례발표 게시판 — 선택 항목 ZIP 일괄 다운로드 (메모리 생성)."""

from __future__ import annotations

import io
import logging
from datetime import date

import pyzipper
from django.utils.text import get_valid_filename

from apps.counseling.models import CasePresentationPost
from apps.counseling.presentation_pdf_encrypt import read_uploaded_file_bytes

logger = logging.getLogger(__name__)


def zip_entry_name_for_post(post: CasePresentationPost, *, used_names: set[str]) -> str:
    author = get_valid_filename(post.author.name) or "author"
    filename = get_valid_filename(post.filename) or "report.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    base = f"{post.cohort}기_{author}_{filename}"
    candidate = base
    counter = 2
    while candidate in used_names:
        stem, _, ext = base.rpartition(".")
        candidate = f"{stem}_{counter}.{ext}" if ext else f"{base}_{counter}"
        counter += 1
    used_names.add(candidate)
    return candidate


def build_password_protected_zip(
    entries: list[tuple[str, bytes]],
    password: str,
) -> bytes:
    buffer = io.BytesIO()
    with pyzipper.AESZipFile(
        buffer,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as archive:
        archive.setpassword(password.encode("utf-8"))
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def build_presentation_posts_zip(posts, password: str) -> bytes:
    entries: list[tuple[str, bytes]] = []
    used_names: set[str] = set()
    for post in posts:
        if not post.file or not post.file.name:
            logger.warning("Skipping presentation post without file pk=%s", post.pk)
            continue
        try:
            raw_bytes = read_uploaded_file_bytes(post.file)
        except FileNotFoundError:
            logger.warning("Presentation post file missing on storage pk=%s", post.pk)
            continue
        entries.append((zip_entry_name_for_post(post, used_names=used_names), raw_bytes))
    if not entries:
        raise FileNotFoundError("다운로드할 첨부 파일을 찾을 수 없습니다.")
    return build_password_protected_zip(entries, password)


def bulk_zip_download_filename(*, cohort: int | None = None) -> str:
    label = f"{cohort}기" if cohort else "전체"
    today = date.today().strftime("%Y%m%d")
    return f"사례발표보고서_{label}_{today}.zip"
