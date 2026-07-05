"""사례발표 게시판 — 다운로드 시 사용자 지정 암호로 파일 제공."""

from __future__ import annotations

import io
import logging
import os
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
        lo_profile = tmp_path / "lo-profile"
        lo_profile.mkdir()
        source_path = tmp_path / f"source.{ext}"
        source_path.write_bytes(file_bytes)
        env = {
            **os.environ,
            "HOME": str(tmp_path),
            "SAL_USE_VCLPLUGIN": "svp",
        }
        try:
            result = subprocess.run(
                [
                    binary,
                    f"-env:UserInstallation=file://{lo_profile.as_posix()}",
                    "--headless",
                    "--norestore",
                    "--nologo",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(tmp_path),
                    str(source_path),
                ],
                check=False,
                timeout=120,
                capture_output=True,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("LibreOffice conversion error ext=%s err=%s", ext, exc)
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
            pdf_bytes = pdf_path.read_bytes()
            if _is_valid_pdf_bytes(pdf_bytes):
                return pdf_bytes
            logger.warning("LibreOffice produced invalid PDF ext=%s size=%s", ext, len(pdf_bytes))
    return None


def _is_valid_pdf_bytes(pdf_bytes: bytes) -> bool:
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        return False
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            return len(pdf.pages) >= 1
    except pikepdf.PdfError:
        return False


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


def _try_build_encrypted_pdf_payload(
    pdf_bytes: bytes,
    *,
    filename: str,
    password: str,
) -> ProtectedDownloadPayload | None:
    if not _is_valid_pdf_bytes(pdf_bytes):
        return None
    try:
        encrypted = encrypt_pdf_bytes(pdf_bytes, password)
    except pikepdf.PdfError:
        logger.warning("PDF encryption failed filename=%s", filename, exc_info=True)
        return None
    return ProtectedDownloadPayload(
        data=encrypted,
        filename=filename,
        content_type="application/pdf",
        delivery="pdf",
    )


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
        payload = _try_build_encrypted_pdf_payload(
            file_bytes,
            filename=basename,
            password=password,
        )
        if payload:
            return payload

    if ext in CONVERT_TO_PDF_EXTENSIONS:
        pdf_bytes = convert_office_bytes_to_pdf(file_bytes, source_ext=ext)
        if pdf_bytes:
            stem = Path(basename).stem or "download"
            payload = _try_build_encrypted_pdf_payload(
                pdf_bytes,
                filename=f"{stem}.pdf",
                password=password,
            )
            if payload:
                return payload

    zip_name = f"{Path(basename).stem or 'download'}.zip"
    try:
        zip_data = build_password_protected_zip(
            file_bytes,
            inner_filename=inner_filename,
            password=password,
        )
    except Exception:
        logger.exception("ZIP fallback failed filename=%s", inner_filename)
        raise
    return ProtectedDownloadPayload(
        data=zip_data,
        filename=zip_name,
        content_type="application/zip",
        delivery="zip",
    )
