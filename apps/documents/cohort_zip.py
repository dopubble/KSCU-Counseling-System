"""과제 파일 읽기·ZIP 일괄 압축."""

from __future__ import annotations

import io
import logging
import os
import re

import pyzipper

from django.core.files.storage import default_storage

from apps.documents.models import CounselorAssignmentSubmission

logger = logging.getLogger(__name__)

_INVALID_ZIP_PATH = re.compile(r'[<>:"|?*\x00\\/]')
_MIN_ZIP_BYTES = 22


def assignment_zip_arcname(assignment: CounselorAssignmentSubmission) -> str:
    """ZIP 내부 파일명 — 상담사·사례·회차·원본 파일명."""
    parts = [
        assignment.submitted_by.name,
        assignment.case.case_number,
        f"{assignment.session_number}회기",
        assignment.get_filename(),
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


def assignment_file_exists(assignment: CounselorAssignmentSubmission) -> bool:
    """다운로드 전 파일 존재 여부 (스토리지·로컬 경로)."""
    return read_assignment_file_bytes(assignment) is not None


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


def _verify_password_protected_zip(zip_bytes: bytes, password: str, expected_names: list[str]) -> None:
    """생성된 ZIP이 열리고 기대 파일명·내용이 있는지 검증."""
    if len(zip_bytes) <= _MIN_ZIP_BYTES:
        raise ValueError("ZIP payload is too small")

    verify_buf = io.BytesIO(zip_bytes)
    with pyzipper.AESZipFile(verify_buf, "r") as zf:
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
    """WinZip AES 암호화 ZIP 바이트 반환 (pyzipper 0.4+)."""
    if not password:
        raise ValueError("password is required")

    valid_entries = _dedupe_arcnames(entries)
    if not valid_entries:
        raise ValueError("no valid entries for ZIP")

    buffer = io.BytesIO()
    with pyzipper.AESZipFile(
        buffer,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
        encryption_kwargs={"nbits": 256},
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        for arcname, data in valid_entries:
            # pyzipper 0.4+: ZipInfo를 직접 writestr에 넘기면 AttributeError 발생.
            zf.writestr(arcname, data)

    buffer.seek(0)
    zip_bytes = buffer.getvalue()
    expected_names = [name for name, _ in valid_entries]
    _verify_password_protected_zip(zip_bytes, password, expected_names)
    return zip_bytes
