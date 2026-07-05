"""사례발표 게시판 — 다운로드 시 암호 ZIP 생성."""

from __future__ import annotations

import io
from pathlib import Path

import pyzipper


def read_uploaded_file_bytes(file_field) -> bytes:
    file_field.open("rb")
    try:
        return file_field.read()
    finally:
        file_field.close()


def encrypted_zip_filename(original_filename: str) -> str:
    stem = Path(original_filename).name or "download"
    return f"{stem}.zip"


def build_password_protected_zip(
    file_bytes: bytes,
    *,
    inner_filename: str,
    password: str,
) -> bytes:
    """원본 파일을 사용자가 지정한 암호로 AES ZIP에 담아 반환."""
    archive_name = Path(inner_filename).name or "file"
    buffer = io.BytesIO()
    with pyzipper.AESZipFile(
        buffer,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.writestr(archive_name, file_bytes)
    return buffer.getvalue()
