"""사례발표 게시판 — 다운로드 시 암호 ZIP 생성."""

from __future__ import annotations

import logging
from pathlib import Path

from django.utils.text import get_valid_filename
from rustyzipper import compress_bytes

logger = logging.getLogger(__name__)


def read_uploaded_file_bytes(file_field) -> bytes:
    with file_field.open("rb") as fp:
        return fp.read()


def safe_inner_archive_name(original_filename: str) -> str:
    name = get_valid_filename(Path(original_filename).name) or "download"
    return name


def encrypted_zip_filename(original_filename: str) -> str:
    stem = Path(safe_inner_archive_name(original_filename)).stem or "download"
    return f"{stem}.zip"


def build_password_protected_zip(
    file_bytes: bytes,
    *,
    inner_filename: str,
    password: str,
) -> bytes:
    """원본 파일을 사용자가 지정한 암호로 AES ZIP에 담아 반환."""
    archive_name = safe_inner_archive_name(inner_filename)
    try:
        return compress_bytes(
            [(archive_name, file_bytes)],
            password=password,
        )
    except Exception:
        logger.exception(
            "Failed to build password-protected ZIP inner=%s size=%s",
            archive_name,
            len(file_bytes),
        )
        raise
