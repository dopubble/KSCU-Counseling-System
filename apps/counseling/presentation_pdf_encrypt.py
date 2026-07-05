"""사례발표 게시판 PDF 암호화 — 디스크 임시 파일 없이 메모리에서 처리."""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


def read_uploaded_file_bytes(file_field) -> bytes:
    if not file_field or not file_field.name:
        raise FileNotFoundError("첨부 파일이 없습니다.")
    try:
        with file_field.open("rb") as handle:
            return handle.read()
    except FileNotFoundError:
        raise
    except OSError as exc:
        logger.warning("Failed to read presentation file name=%s", file_field.name)
        raise FileNotFoundError("첨부 파일을 읽을 수 없습니다.") from exc


def encrypt_pdf_bytes(pdf_bytes: bytes, password: str) -> bytes:
    import pikepdf

    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        buffer = io.BytesIO()
        pdf.save(
            buffer,
            encryption=pikepdf.Encryption(
                user=password,
                owner=password,
                R=6,
                allow=pikepdf.Permissions(extract=False),
            ),
        )
        return buffer.getvalue()
