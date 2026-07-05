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

import pyzipper
from django.core.exceptions import SuspiciousFileOperation
from django.utils.text import get_valid_filename
from rustyzipper import EncryptionMethod, compress_bytes

logger = logging.getLogger(__name__)

PDF_EXTENSIONS = {".pdf"}
HWP_EXTENSIONS = {".hwp", ".hwpx"}
DOC_CONVERT_EXTENSIONS = {".doc", ".docx"}
CONVERT_TO_PDF_EXTENSIONS = HWP_EXTENSIONS | DOC_CONVERT_EXTENSIONS
_MIN_ZIP_BYTES = 22


@dataclass(frozen=True)
class ProtectedDownloadPayload:
    data: bytes
    filename: str
    content_type: str
    delivery: str  # "pdf" | "zip"


def safe_download_basename(original_filename: str) -> str:
    name = Path(original_filename or "").name
    if not name:
        return "download"
    try:
        return get_valid_filename(name) or "download"
    except SuspiciousFileOperation:
        return "download"


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


def _safe_listdir(directory: str | os.PathLike, *, limit: int = 40) -> dict:
    """디렉터리 목록(진단용). 실패 시 error 키 반환."""
    path = Path(directory)
    try:
        if not path.is_dir():
            return {"path": str(path), "is_dir": False, "entries": []}
        entries = sorted(path.iterdir(), key=lambda p: p.name)[:limit]
        return {
            "path": str(path),
            "is_dir": True,
            "entry_count": len(entries),
            "truncated": len(entries) >= limit,
            "entries": [p.name for p in entries],
        }
    except Exception as exc:
        return {"path": str(path), "error": f"{type(exc).__name__}: {exc}"}


def _collect_presentation_file_storage_diagnostics(file_field) -> dict:
    """Railway 스토리지 진단 스냅샷(비밀값 제외)."""
    from django.conf import settings

    storage = getattr(file_field, "storage", None) if file_field else None
    name = (getattr(file_field, "name", "") or "") if file_field else ""
    storage_class = type(storage).__name__ if storage else None
    storage_module = type(storage).__module__ if storage else None
    default_backend = (getattr(settings, "STORAGES", {}) or {}).get("default", {}).get(
        "BACKEND"
    )

    report: dict = {
        "media_root": str(getattr(settings, "MEDIA_ROOT", "")),
        "media_url": getattr(settings, "MEDIA_URL", ""),
        "media_storage_mode": getattr(settings, "MEDIA_STORAGE_MODE", None),
        "aws_storage_bucket_name_setting": getattr(
            settings, "AWS_STORAGE_BUCKET_NAME", None
        ),
        "default_storage_backend": default_backend,
        "storage_class": storage_class,
        "storage_module": storage_module,
        "db_file_name": name,
        "fieldfile_path_attr": None,
        "storage_path": None,
        "absolute_path_isfile": None,
        "absolute_path_getsize": None,
        "storage_exists": None,
        "storage_size": None,
        "parent_dir_listing": None,
        "media_root_listing": None,
        "presentation_board_root_listing": None,
        "s3": None,
    }

    if storage and name:
        try:
            report["storage_exists"] = storage.exists(name)
        except Exception as exc:
            report["storage_exists"] = f"ERROR {type(exc).__name__}: {exc}"

        try:
            report["storage_size"] = storage.size(name)
        except Exception as exc:
            report["storage_size"] = f"ERROR {type(exc).__name__}: {exc}"

    if storage and name and hasattr(storage, "path"):
        try:
            abs_path = storage.path(name)
            report["storage_path"] = abs_path
            report["absolute_path_isfile"] = os.path.isfile(abs_path)
            if os.path.isfile(abs_path):
                report["absolute_path_getsize"] = os.path.getsize(abs_path)
            parent = os.path.dirname(abs_path)
            report["parent_dir_listing"] = _safe_listdir(parent)
        except Exception as exc:
            report["storage_path"] = f"ERROR {type(exc).__name__}: {exc}"

    if file_field is not None:
        try:
            report["fieldfile_path_attr"] = file_field.path
        except Exception as exc:
            report["fieldfile_path_attr"] = f"ERROR {type(exc).__name__}: {exc}"

    media_root = getattr(settings, "MEDIA_ROOT", None)
    if media_root:
        report["media_root_listing"] = _safe_listdir(media_root)
        report["presentation_board_root_listing"] = _safe_listdir(
            Path(media_root) / "presentation_board"
        )

    if storage and "s3" in (storage_module or "").lower():
        s3_info: dict = {
            "bucket_name": getattr(storage, "bucket_name", None),
            "location_prefix": getattr(storage, "location", "") or "",
            "region_name": getattr(storage, "region_name", None),
            "endpoint_url": getattr(storage, "endpoint_url", None),
        }
        object_key = name
        if s3_info["location_prefix"] and object_key:
            object_key = f"{s3_info['location_prefix'].strip('/')}/{object_key.lstrip('/')}"
        s3_info["object_key"] = object_key
        try:
            s3_info["connection_ok"] = storage.connection is not None
        except Exception as exc:
            s3_info["connection_ok"] = f"ERROR {type(exc).__name__}: {exc}"
        if name:
            try:
                s3_info["head_object_ok"] = storage.exists(name)
            except Exception as exc:
                s3_info["head_object_ok"] = f"ERROR {type(exc).__name__}: {exc}"
        report["s3"] = s3_info

    return report


def _format_presentation_storage_diagnostic(report: dict) -> str:
    lines = [f"  {key}={value!r}" for key, value in report.items() if key != "s3"]
    if report.get("s3"):
        lines.append("  s3:")
        for key, value in report["s3"].items():
            lines.append(f"    {key}={value!r}")
    return "\n".join(lines)


def log_presentation_file_storage_diagnostics(
    file_field,
    *,
    display_filename: str = "",
    trigger: str = "unknown",
) -> None:
    """Railway 로그용 스토리지 진단 — logger.error로 강제 출력."""
    report = _collect_presentation_file_storage_diagnostics(file_field)
    logger.error(
        "PRESENTATION_FILE_STORAGE_DIAGNOSTIC trigger=%s display_filename=%s\n%s",
        trigger,
        display_filename,
        _format_presentation_storage_diagnostic(report),
    )


def read_uploaded_file_bytes(file_field, *, display_filename: str = "") -> bytes:
    """
    FieldFile에서 바이트를 읽는다.
    작성자 직접 다운로드(_presentation_file_response)와 동일하게
    file_field.open()을 우선 사용한다.
    """
    if not file_field or not getattr(file_field, "name", ""):
        log_presentation_file_storage_diagnostics(
            file_field,
            display_filename=display_filename,
            trigger="read_uploaded_file_bytes_no_name",
        )
        raise FileNotFoundError("presentation file is not attached")

    name = file_field.name
    last_error: Exception | None = None

    try:
        with file_field.open("rb") as fp:
            return fp.read()
    except Exception as exc:
        last_error = exc
        logger.warning(
            "FieldFile.open/read failed name=%s storage=%s",
            name,
            type(file_field.storage).__name__,
            exc_info=True,
        )

    storage = file_field.storage
    if hasattr(storage, "path"):
        full_path = None
        try:
            full_path = storage.path(name)
            with open(full_path, "rb") as fp:
                return fp.read()
        except Exception as exc:
            last_error = exc
            logger.exception(
                "Local path read failed name=%s path=%s",
                name,
                full_path,
            )

    try:
        with storage.open(name, "rb") as fp:
            return fp.read()
    except Exception as exc:
        last_error = exc
        logger.exception("Storage.open failed name=%s", name)

    log_presentation_file_storage_diagnostics(
        file_field,
        display_filename=display_filename,
        trigger="read_uploaded_file_bytes",
    )
    raise FileNotFoundError(f"presentation file unavailable: {name}") from last_error


def _should_attempt_office_pdf_conversion(ext: str) -> bool:
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
    ext = (source_ext or "").lower().lstrip(".")
    if not ext:
        return None
    binary = _libreoffice_binary()
    if not binary:
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
    except Exception:
        logger.exception("LibreOffice conversion crashed ext=%s", ext)
    return None


def _import_pikepdf():
    try:
        import pikepdf
    except ImportError:
        logger.warning("pikepdf is not installed; skipping PDF encryption.")
        return None
    return pikepdf


def _is_valid_pdf_bytes(pdf_bytes: bytes) -> bool:
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        return False
    pikepdf = _import_pikepdf()
    if pikepdf is None:
        return False
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            return len(pdf.pages) >= 1
    except Exception:
        return False


def encrypt_pdf_bytes(pdf_bytes: bytes, password: str) -> bytes:
    pikepdf = _import_pikepdf()
    if pikepdf is None:
        raise RuntimeError("pikepdf unavailable")
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


def _verify_password_protected_zip(
    zip_bytes: bytes,
    password: str,
    expected_name: str,
) -> None:
    if len(zip_bytes) <= _MIN_ZIP_BYTES:
        raise ValueError("ZIP payload is too small")
    with pyzipper.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        zf.setpassword(password.encode("utf-8"))
        names = zf.namelist()
        if expected_name not in names:
            raise ValueError(
                f"ZIP missing entry {expected_name!r}; got {names!r}"
            )
        payload = zf.read(expected_name)
        if not payload:
            raise ValueError(f"ZIP entry is empty: {expected_name}")


def _compress_with_pyzipper_zipcrypto(
    archive_name: str,
    file_bytes: bytes,
    password: str,
) -> bytes:
    buffer = io.BytesIO()
    with pyzipper.ZipFile(
        buffer,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
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
    """Windows 탐색기 호환 ZipCrypto ZIP (과제 다운로드와 동일 방식)."""
    archive_name = safe_download_basename(inner_filename)
    zip_bytes: bytes | None = None

    try:
        zip_bytes = compress_bytes(
            [(archive_name, file_bytes)],
            password=password,
            encryption=EncryptionMethod.ZIPCRYPTO,
            suppress_warning=True,
        )
    except Exception:
        logger.warning(
            "rustyzipper ZipCrypto failed inner=%s size=%s; trying pyzipper",
            archive_name,
            len(file_bytes),
            exc_info=True,
        )

    if zip_bytes is None:
        zip_bytes = _compress_with_pyzipper_zipcrypto(
            archive_name,
            file_bytes,
            password,
        )

    _verify_password_protected_zip(zip_bytes, password, archive_name)
    return zip_bytes


def build_password_protected_download(
    file_bytes: bytes,
    *,
    inner_filename: str,
    password: str,
) -> ProtectedDownloadPayload:
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
