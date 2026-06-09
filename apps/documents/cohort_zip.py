"""과제 파일 읽기 — 스토리지·로컬 경로 fallback."""

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


def build_password_protected_zip(
    entries: list[tuple[str, bytes]],
    password: str,
) -> bytes:
    """AES 암호화 ZIP 바이트 반환."""
    buffer = io.BytesIO()
    with pyzipper.AESZipFile(
        buffer,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        for arcname, data in entries:
            zf.writestr(arcname, data)
    return buffer.getvalue()
