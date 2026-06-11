"""과제 파일 읽기·ZIP 일괄 압축."""

from __future__ import annotations

import io
import logging
import os
import re

import pyzipper
from rustyzipper import EncryptionMethod, compress_bytes

from django.core.files.storage import default_storage

from apps.documents.models import CounselorAssignmentSubmission

logger = logging.getLogger(__name__)

_INVALID_ZIP_PATH = re.compile(r'[<>:"|?*\x00\\/]')
_MIN_ZIP_BYTES = 22


def _ensure_filename_extension(filename: str, storage_name: str) -> str:
    """ZIP 내부 파일명에 확장자(.hwp/.pdf 등)가 없으면 스토리지 경로에서 보완."""
    if os.path.splitext(filename)[1]:
        return filename
    ext = os.path.splitext(storage_name.replace("\\", "/"))[1].lower()
    if ext in {".pdf", ".hwp", ".doc", ".docx", ".jpg", ".jpeg"}:
        return f"{filename}{ext}"
    return filename


def assignment_zip_arcname(assignment: CounselorAssignmentSubmission) -> str:
    """ZIP 내부 파일명 — 상담사·사례·회차·원본 파일명."""
    original = assignment.get_filename() or f"assignment_{assignment.pk}"
    if assignment.file and assignment.file.name:
        original = _ensure_filename_extension(original, assignment.file.name)
    parts = [
        assignment.submitted_by.name,
        assignment.case.case_number,
        f"{assignment.session_number}회기",
        original,
    ]
    arcname = "_".join(str(part) for part in parts if part)
    arcname = _INVALID_ZIP_PATH.sub("_", arcname.replace("/", "_").replace("\\", "_"))
    return arcname or f"assignment_{assignment.pk}"


def read_assignment_file_bytes(assignment: CounselorAssignmentSubmission) -> bytes | None:
    """과제 파일 바이트. 없거나 읽기 실패 시 None."""
    if not assignment.file or not assignment.file.name:
        return None

    name = assignment.file.name
    storage = assignment.file.storage or default_storage

    try:
        if storage.exists(name):
            with storage.open(name, "rb") as fh:
                data = fh.read()
            if data:
                return data
    except Exception:
        logger.exception("Storage read failed for assignment file: %s", name)

    if hasattr(storage, "path"):
        try:
            full_path = storage.path(name)
            if os.path.isfile(full_path):
                with open(full_path, "rb") as fh:
                    data = fh.read()
                if data:
                    return data
        except Exception:
            logger.exception("Local path read failed for assignment file: %s", name)

    try:
        with assignment.file.open("rb") as fh:
            data = fh.read()
        if data:
            return data
    except Exception:
        logger.exception("FieldFile read failed for assignment file: %s", name)

    logger.warning("Assignment file unavailable: %s", name)
    return None


def _dedupe_arcnames(entries: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    seen: dict[str, int] = {}
    unique: list[tuple[str, bytes]] = []
    for arcname, data in entries:
        if not data:
            continue
        name = arcname
        if name in seen:
            seen[name] += 1
            stem, ext = os.path.splitext(name)
            name = f"{stem}_{seen[name]}{ext}"
        else:
            seen[name] = 1
        unique.append((name, data))
    return unique


def collect_assignment_zip_entries(
    assignments: list[CounselorAssignmentSubmission],
) -> tuple[list[tuple[str, bytes]], list[CounselorAssignmentSubmission]]:
    """ZIP에 넣을 (arcname, bytes) 목록과 읽기 실패 과제를 반환."""
    entries: list[tuple[str, bytes]] = []
    missing: list[CounselorAssignmentSubmission] = []
    for assignment in assignments:
        data = read_assignment_file_bytes(assignment)
        if not data:
            missing.append(assignment)
            logger.warning(
                "Skipping assignment %s in ZIP — file missing: %s",
                assignment.pk,
                getattr(assignment.file, "name", ""),
            )
            continue
        entries.append((assignment_zip_arcname(assignment), data))
    return _dedupe_arcnames(entries), missing


def _verify_password_protected_zip(
    zip_bytes: bytes,
    password: str,
    expected_names: list[str],
) -> None:
    """ZipCrypto ZIP 검증 — Windows 탐색기 호환 형식."""
    if len(zip_bytes) <= _MIN_ZIP_BYTES:
        raise ValueError("ZIP payload is too small")

    verify_buf = io.BytesIO(zip_bytes)
    with pyzipper.ZipFile(verify_buf, "r") as zf:
        zf.setpassword(password.encode("utf-8"))
        names = zf.namelist()
        if not names:
            raise ValueError("ZIP contains no entries")
        if len(names) != len(expected_names):
            raise ValueError(
                f"ZIP entry count mismatch: expected {len(expected_names)}, got {len(names)}"
            )
        for arcname in expected_names:
            payload = zf.read(arcname)
            if not payload:
                raise ValueError(f"ZIP entry is empty: {arcname}")


def build_password_protected_zip(
    entries: list[tuple[str, bytes]],
    password: str,
) -> bytes:
    """ZipCrypto 암호 ZIP — Windows 탐색기에서 압축 해제 가능."""
    if not password:
        raise ValueError("password is required")

    valid_entries = _dedupe_arcnames(entries)
    if not valid_entries:
        raise ValueError("no valid entries for ZIP")

    zip_bytes = compress_bytes(
        valid_entries,
        password=password,
        encryption=EncryptionMethod.ZIPCRYPTO,
        suppress_warning=True,
    )
    expected_names = [name for name, _ in valid_entries]
    _verify_password_protected_zip(zip_bytes, password, expected_names)
    return zip_bytes
