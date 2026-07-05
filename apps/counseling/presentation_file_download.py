"""사례발표 게시판 — 다운로드 시 사용자 지정 암호로 파일 제공."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from django.utils.text import get_valid_filename
from rustyzipper import compress_bytes

logger = logging.getLogger(__name__)

PDF_EXTENSIONS = {".pdf"}
CONVERT_TO_PDF_EXTENSIONS = {".hwp", ".hwpx", ".doc", ".docx"}
ZIP_FALLBACK_EXTENSIONS = PDF_EXTENSIONS | CONVERT_TO_PDF_EXTENSIONS


@dataclass(frozen=True)
class ProtectedDownloadPayload:
    data: bytes
    filename: str
    content_type: str
    delivery: str  # "pdf" | "zip"


def read_uploaded_file_bytes(file_field) -> bytes:
    with file_field.open("rb") as fp:
        return fp.read()


def safe_download_basename(original_filename: str) -> str:
    return get_valid_filename(Path(original_filename).name) or "download"


def _libreoffice_binary() -> str | None:
    for candidate in ("soffice", "libreoffice"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def convert_office_bytes_to_pdf(file_bytes: bytes, *, source_ext: str) -> bytes | None:
    """
    HWP/HWPX/DOC/DOCX → PDF (LibreOffice 필요).
    Railway/Nixpacks에 libreoffice 패키지가 있어야 HWP 변환이 동작합니다.
    """
    ext = (source_ext or "").lower().lstrip(".")
    if not ext:
        return None
    binary = _libreoffice_binary()
    if not binary:
        logger.info("LibreOffice not found; skipping office→PDF conversion.")
        return None

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        source_path = tmp_path / f"source.{ext}"
        source_path.write_bytes(file_bytes)
        try:
            result = subprocess.run(
                [
                    binary,
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(tmp_path),
                    str(source_path),
                ],
                check=False,
                timeout=120,
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            logger.warning("LibreOffice conversion timed out ext=%s", ext)
            return None

        if result.returncode != 0:
            logger.warning(
                "LibreOffice conversion failed ext=%s code=%s stderr=%s",
                ext,
                result.returncode,
                result.stderr.decode(errors="replace")[:500],
            )
            return None

        pdf_path = tmp_path / "source.pdf"
        if pdf_path.is_file():
            return pdf_path.read_bytes()
    return None


def encrypt_pdf_bytes(pdf_bytes: bytes, password: str) -> bytes:
    """PDF 바이트에 사용자 암호 적용 (AES-256)."""
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        out = io.BytesIO()
        pdf.save(
            out,
            encryption=pikepdf.Encryption(
                user=password,
                owner=password,
                R=6,
                allow=pikepdf.Permissions(extract=False),
            ),
        )
        return out.getvalue()


def build_password_protected_zip(
    file_bytes: bytes,
    *,
    inner_filename: str,
    password: str,
) -> bytes:
    archive_name = safe_download_basename(inner_filename)
    return compress_bytes([(archive_name, file_bytes)], password=password)


def build_password_protected_download(
    file_bytes: bytes,
    *,
    inner_filename: str,
    password: str,
) -> ProtectedDownloadPayload:
    """
    다운로드 페이로드 생성.

    1) PDF → 암호화 PDF (.pdf)
    2) HWP/HWPX/DOC/DOCX → (LibreOffice) PDF 변환 → 암호화 PDF (.pdf)
    3) 변환 불가 → AES ZIP (최후 fallback, 한글 원본 확장자는 서버에서 직접 암호 불가)
    """
    basename = safe_download_basename(inner_filename)
    ext = Path(basename).suffix.lower()

    if ext == ".pdf":
        encrypted = encrypt_pdf_bytes(file_bytes, password)
        return ProtectedDownloadPayload(
            data=encrypted,
            filename=basename,
            content_type="application/pdf",
            delivery="pdf",
        )

    if ext in CONVERT_TO_PDF_EXTENSIONS:
        pdf_bytes = convert_office_bytes_to_pdf(file_bytes, source_ext=ext)
        if pdf_bytes:
            stem = Path(basename).stem or "download"
            return ProtectedDownloadPayload(
                data=encrypt_pdf_bytes(pdf_bytes, password),
                filename=f"{stem}.pdf",
                content_type="application/pdf",
                delivery="pdf",
            )

    zip_name = f"{Path(basename).stem or 'download'}.zip"
    return ProtectedDownloadPayload(
        data=build_password_protected_zip(
            file_bytes,
            inner_filename=inner_filename,
            password=password,
        ),
        filename=zip_name,
        content_type="application/zip",
        delivery="zip",
    )
