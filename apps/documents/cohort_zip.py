"""동기 기수 과제 ZIP 생성 (AES 암호화)."""

from __future__ import annotations

import io
import re

import pyzipper

from apps.documents.models import CounselorAssignmentSubmission

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
    if not assignment.file:
        return None
    try:
        assignment.file.open("rb")
        try:
            data = assignment.file.read()
        finally:
            assignment.file.close()
    except OSError:
        return None
    return data if data else None


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
