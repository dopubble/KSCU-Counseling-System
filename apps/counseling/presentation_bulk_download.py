"""사례발표 게시판 — 선택 항목 ZIP 일괄 다운로드 (메모리 생성)."""

from __future__ import annotations

import io
import logging
import os
from datetime import date

import pyzipper
from django.utils.text import get_valid_filename
from pyzipper.zipfile import _MASK_USE_DATA_DESCRIPTOR
from pyzipper.zipfile_aes import BaseZipEncrypter

from apps.counseling.models import CasePresentationPost
from apps.counseling.presentation_pdf_encrypt import read_uploaded_file_bytes

logger = logging.getLogger(__name__)


class CRCZipEncrypter(BaseZipEncrypter):
    """PKWARE ZipCrypto — Windows 탐색기 기본 압축 해제와 호환."""

    encryption_header_length = 12
    _crctable: list[int] | None = None

    def __init__(self, pwd: bytes):
        if not pwd:
            raise RuntimeError("ZipCrypto encryption requires a password.")
        self.pwd = pwd
        self._zinfo = None
        self._init_keys()

    @classmethod
    def _get_crctable(cls) -> list[int]:
        if cls._crctable is None:

            def _gen_crc(crc: int) -> int:
                for _ in range(8):
                    if crc & 1:
                        crc = (crc >> 1) ^ 0xEDB88320
                    else:
                        crc >>= 1
                return crc

            cls._crctable = [_gen_crc(i) for i in range(256)]
        return cls._crctable

    def _crc32(self, ch: int, crc: int) -> int:
        return (crc >> 8) ^ self._get_crctable()[(crc ^ ch) & 0xFF]

    def _update_keys(self, ch: int) -> None:
        self.key0 = self._crc32(ch, self.key0)
        self.key1 = (self.key1 + (self.key0 & 0xFF)) & 0xFFFFFFFF
        self.key1 = (self.key1 * 134775813 + 1) & 0xFFFFFFFF
        self.key2 = self._crc32(self.key1 >> 24, self.key2)

    def _init_keys(self) -> None:
        self.key0 = 305419896
        self.key1 = 591751049
        self.key2 = 878082192
        for byte in self.pwd:
            self._update_keys(byte)

    def update_zipinfo(self, zinfo) -> None:
        self._zinfo = zinfo
        zinfo.flag_bits |= _MASK_USE_DATA_DESCRIPTOR
        zinfo._raw_time = zinfo.get_dostime()

    def encryption_header(self) -> bytes:
        if self._zinfo is None:
            raise RuntimeError("ZipCrypto encrypter missing zip entry metadata.")
        self._init_keys()
        check_byte = (self._zinfo._raw_time >> 8) & 0xFF
        return self.encrypt(os.urandom(11) + bytes([check_byte]))

    def encrypt(self, data: bytes) -> bytes:
        encrypted = bytearray()
        for plain in data:
            keystream = self.key2 | 2
            cipher = plain ^ (((keystream * (keystream ^ 1)) >> 8) & 0xFF)
            self._update_keys(plain)
            encrypted.append(cipher)
        return bytes(encrypted)

    def finalize_zipinfo(self, zinfo) -> None:
        return None

    def flush(self) -> bytes:
        return b""


class ZipCryptoZipFile(pyzipper.ZipFile):
    """ZipCrypto 쓰기 지원 pyzipper.ZipFile."""

    def get_encrypter(self):
        return CRCZipEncrypter(self.pwd)


def zip_entry_name_for_post(post: CasePresentationPost, *, used_names: set[str]) -> str:
    author = get_valid_filename(post.author.name) or "author"
    filename = get_valid_filename(post.filename) or "report.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    post_id = str(post.pk).split("-", 1)[0]
    base = f"{post.cohort}기_{author}_{post_id}_{filename}"
    candidate = base
    counter = 2
    while candidate in used_names:
        stem, _, ext = base.rpartition(".")
        candidate = f"{stem}_{counter}.{ext}" if ext else f"{base}_{counter}"
        counter += 1
    used_names.add(candidate)
    return candidate


def build_password_protected_zip(
    entries: list[tuple[str, bytes]],
    password: str,
) -> bytes:
    buffer = io.BytesIO()
    with ZipCryptoZipFile(
        buffer,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
    ) as archive:
        archive.setpassword(password.encode("utf-8"))
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def build_presentation_posts_zip(posts, password: str) -> bytes:
    entries: list[tuple[str, bytes]] = []
    used_names: set[str] = set()
    missing_labels: list[str] = []
    for post in posts:
        label = f"{post.author.name} — {post.title}"
        if not post.file or not post.file.name:
            logger.warning("Skipping presentation post without file pk=%s", post.pk)
            missing_labels.append(label)
            continue
        try:
            raw_bytes = read_uploaded_file_bytes(post.file)
        except FileNotFoundError:
            logger.warning("Presentation post file missing on storage pk=%s", post.pk)
            missing_labels.append(label)
            continue
        entries.append((zip_entry_name_for_post(post, used_names=used_names), raw_bytes))
    if missing_labels:
        raise FileNotFoundError(
            "다음 게시글의 첨부 파일을 찾을 수 없습니다: " + "; ".join(missing_labels)
        )
    if not entries:
        raise FileNotFoundError("다운로드할 첨부 파일을 찾을 수 없습니다.")
    return build_password_protected_zip(entries, password)


def bulk_zip_download_filename(*, cohort: int | None = None) -> str:
    label = f"{cohort}기" if cohort else "전체"
    today = date.today().strftime("%Y%m%d")
    return f"사례발표보고서_{label}_{today}.zip"
