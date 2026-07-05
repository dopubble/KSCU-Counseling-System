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
from urllib.parse import quote

import pikepdf
import pyzipper
from django.utils.text import get_valid_filename
from rustyzipper import compress_bytes

logger = logging.getLogger(__name__)

PDF_EXTENSIONS = {".pdf"}
HWP_EXTENSIONS = {".hwp", ".hwpx"}
DOC_CONVERT_EXTENSIONS = {".doc", ".docx"}
CONVERT_TO_PDF_EXTENSIONS = HWP_EXTENSIONS | DOC_CONVERT_EXTENSIONS


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


def ascii_attachment_filename(filename: str) -> str:
    """Content-Disposition filename= 파라미터용 ASCII 파일명."""
    safe_name = safe_download_basename(filename)
    ascii_name = safe_name.encode("ascii", "ignore").decode("ascii").strip("._")
    if not ascii_name:
        return "download" + (Path(safe_name).suffix or "")
    if not Path(ascii_name).suffix and Path(safe_name).suffix:
        return f"{ascii_name}{Path(safe_name).suffix}"
    return ascii_name


def attachment_content_disposition(filename: str) -> str:
    safe_name = safe_download_basename(filename)
    ascii_name = ascii_attachment_filename(filename)
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(safe_name)}"
    )


def _should_attempt_office_pdf_conversion(ext: str) -> bool:
    """Linux 서버에서는 HWP 변환이 거의 불가능하므로 doc/docx만 시도."""
    if ext in DOC_CONVERT_EXTENSIONS:
        return True
    if ext in HWP_EXTENSIONS:
        return os.name == "nt"
    return False


def _libreoffice_binary() -> str | None:
    for candidate in ("soffice", "libreoffice"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def convert_office_bytes_to_pdf(file_bytes: bytes, *, source_ext: str) -> bytes | None:
    """
    DOC/DOCX → PDF (LibreOffice). Windows에서만 HWP/HWPX 시도.
    """
    ext = (source_ext or "").lower().lstrip(".")
    if not ext:
        return None
    binary = _libreoffice_binary()
    if not binary:
        logger.info("LibreOffice not found; skipping office→PDF conversion.")
        return None

    try:
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
                logger.warning(
                    "LibreOffice produced invalid PDF ext=%s size=%s",
                    ext,
                    len(pdf_bytes),
                )
    except Exception:
        logger.exception("LibreOffice conversion crashed ext=%s", ext)
    return None


def _is_valid_pdf_bytes(pdf_bytes: bytes) -> bool:
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        return False
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            return len(pdf.pages) >= 1
    except Exception:
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
    except Exception:
        logger.warning("PDF encryption failed filename=%s", filename, exc_info=True)
        return None
    return ProtectedDownloadPayload(
        data=encrypted,
        filename=filename,
        content_type="application/pdf",
        delivery="pdf",
    )


def _compress_with_pyzipper(archive_name: str, file_bytes: bytes, password: str) -> bytes:
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


def build_password_protected_zip(
    file_bytes: bytes,
    *,
    inner_filename: str,
    password: str,
) -> bytes:
    archive_name = safe_download_basename(inner_filename)
    try:
        return compress_bytes([(archive_name, file_bytes)], password=password)
    except Exception:
        logger.warning(
            "rustyzipper failed inner=%s size=%s; trying pyzipper",
            archive_name,
            len(file_bytes),
            exc_info=True,
        )
        return _compress_with_pyzipper(archive_name, file_bytes, password)


def build_password_protected_download(
    file_bytes: bytes,
    *,
    inner_filename: str,
    password: str,
) -> ProtectedDownloadPayload:
    """
    다운로드 페이로드 생성.

    1) PDF → 암호화 PDF (.pdf)
    2) DOC/DOCX → (LibreOffice) PDF 변환 → 암호화 PDF (.pdf)
    3) HWP/HWPX 및 변환 불가 → AES ZIP
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

    if _should_attempt_office_pdf_conversion(ext):
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
    zip_data = build_password_protected_zip(
        file_bytes,
        inner_filename=inner_filename,
        password=password,
    )
    return ProtectedDownloadPayload(
        data=zip_data,
        filename=zip_name,
        content_type="application/zip",
        delivery="zip",
    )
