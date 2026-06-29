"""
동의서 FileField 전용 스토리지.

- 저장: CONSENT_AWS_* 설정 시 비공개 S3, 없으면 default(기존 MEDIA_ROOT)
- 조회/삭제: S3 전환 후에도 default 에 남은 레거시 경로를 자동 fallback
"""

from __future__ import annotations

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage, storages
from django.utils.deconstruct import deconstructible
from django.utils.functional import LazyObject


def _legacy_default_storage() -> Storage:
    return storages["default"]


def _build_private_s3_storage(*, s3_options: dict) -> Storage:
    from storages.backends.s3 import S3Storage

    return S3Storage(**s3_options)


def consent_s3_configured() -> bool:
    return bool(getattr(settings, "CONSENT_AWS_STORAGE_BUCKET_NAME", ""))


@deconstructible
class ConsentMediaStorage(Storage):
    """
    동의서 전용 듀얼 스토리지.

    save → primary (S3 또는 filesystem)
    open/exists/delete → primary 우선, 없으면 legacy default
    """

    def __init__(self, s3_options: dict | None = None):
        self._s3_options = s3_options

    def _primary(self) -> Storage:
        if self._s3_options:
            return _build_private_s3_storage(s3_options=self._s3_options)
        return _legacy_default_storage()

    def _backends(self) -> list[Storage]:
        primary = self._primary()
        legacy = _legacy_default_storage()
        if primary is legacy:
            return [primary]
        return [primary, legacy]

    def _save(self, name, content):
        return self._primary()._save(name, content)

    def open(self, name, mode="rb"):
        for backend in self._backends():
            if backend.exists(name):
                return backend.open(name, mode)
        raise FileNotFoundError(f"Consent file not found: {name}")

    def exists(self, name):
        return any(backend.exists(name) for backend in self._backends())

    def delete(self, name):
        for backend in self._backends():
            if backend.exists(name):
                backend.delete(name)

    def size(self, name):
        for backend in self._backends():
            if backend.exists(name):
                return backend.size(name)
        raise FileNotFoundError(name)

    def get_available_name(self, name, max_length=None):
        return self._primary().get_available_name(name, max_length=max_length)

    def generate_filename(self, filename):
        return self._primary().generate_filename(filename)

    def path(self, name):
        backend = self._primary()
        if isinstance(backend, FileSystemStorage):
            return backend.path(name)
        raise NotImplementedError("S3 storage has no local path")


class _ConsentStorageLazy(LazyObject):
    def _setup(self):
        self._wrapped = storages["consent"]


consent_file_storage = _ConsentStorageLazy()
