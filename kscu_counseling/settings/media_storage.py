"""
레거시 미디어(게시판·회기자료·종결보고서) 스토리지.

주의: AWS_* 만으로 전역 S3 를 켜지 않습니다.
      기존 운영 파일 경로를 깨지 않으려면 MEDIA_USE_S3=true 를 명시해야 합니다.
동의서는 consent_storage.py (CONSENT_AWS_*) 로 별도 관리합니다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

MediaStorageMode = Literal["s3", "volume", "ephemeral"]


def _set_setting(settings_module, name: str, value) -> None:
    if isinstance(settings_module, dict):
        settings_module[name] = value
    else:
        setattr(settings_module, name, value)


def _get_setting(settings_module, name: str, default=None):
    if isinstance(settings_module, dict):
        return settings_module.get(name, default)
    return getattr(settings_module, name, default)


def _s3_options_for_bucket(
    *,
    bucket: str,
    region: str,
    endpoint: str,
    location: str = "",
    access_key: str = "",
    secret_key: str = "",
    addressing_style: str | None = None,
) -> dict:
    opts = {
        "bucket_name": bucket,
        "region_name": region,
        "default_acl": None,
        "querystring_auth": False,
        "file_overwrite": False,
        "signature_version": "s3v4",
        "object_parameters": {"CacheControl": "private, no-store"},
    }
    if location:
        opts["location"] = location
    if access_key:
        opts["access_key"] = access_key
    if secret_key:
        opts["secret_key"] = secret_key
    if endpoint:
        opts["endpoint_url"] = endpoint
        if addressing_style:
            opts["addressing_style"] = addressing_style
    return opts


def _ensure_filesystem_storages(settings_module) -> None:
    storages = _get_setting(settings_module, "STORAGES")
    if storages:
        return
    _set_setting(
        settings_module,
        "STORAGES",
        {
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
            },
            "staticfiles": {
                "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
            },
        },
    )


def apply_legacy_media_storage(
    settings_module,
    *,
    env_str,
    on_railway: bool,
) -> MediaStorageMode:
    """
    게시판·회기자료 등 기존 FileField 의 default 스토리지.

    우선순위:
    1) MEDIA_USE_S3=true + AWS_* 3종 → 전역 S3 (마이그레이션 완료 후에만)
    2) MEDIA_ROOT → Railway Volume 등 파일시스템
    3) 기본 MEDIA_ROOT (에페메럴)
    """
    media_root = env_str("MEDIA_ROOT")
    if media_root:
        _set_setting(settings_module, "MEDIA_ROOT", Path(media_root))

    use_global_s3 = env_str("MEDIA_USE_S3", "").lower() in ("true", "1", "yes")
    aws_bucket = env_str("AWS_STORAGE_BUCKET_NAME")
    aws_key = env_str("AWS_ACCESS_KEY_ID")
    aws_secret = env_str("AWS_SECRET_ACCESS_KEY")

    if use_global_s3 and aws_bucket and aws_key and aws_secret:
        _apply_global_private_s3(settings_module, env_str=env_str, bucket=aws_bucket)
        if on_railway:
            print(
                f"[kscu] legacy media storage=S3 (global, opt-in) bucket={aws_bucket}",
                file=sys.stderr,
            )
        return "s3"

    _ensure_filesystem_storages(settings_module)

    if media_root:
        if on_railway:
            print(
                f"[kscu] legacy media storage=filesystem (volume) "
                f"MEDIA_ROOT={_get_setting(settings_module, 'MEDIA_ROOT')}",
                file=sys.stderr,
            )
        return "volume"

    if on_railway:
        print(
            f"[kscu] legacy media storage=filesystem (ephemeral) "
            f"MEDIA_ROOT={_get_setting(settings_module, 'MEDIA_ROOT')}",
            "— Volume 마운트 또는 추후 MEDIA_USE_S3 마이그레이션을 권장합니다.",
            file=sys.stderr,
        )
    return "ephemeral"


def _apply_global_private_s3(settings_module, *, env_str, bucket: str) -> None:
    region = env_str("AWS_S3_REGION_NAME", "ap-northeast-2")
    endpoint = env_str("AWS_S3_ENDPOINT_URL")

    _set_setting(settings_module, "AWS_ACCESS_KEY_ID", env_str("AWS_ACCESS_KEY_ID"))
    _set_setting(settings_module, "AWS_SECRET_ACCESS_KEY", env_str("AWS_SECRET_ACCESS_KEY"))
    _set_setting(settings_module, "AWS_STORAGE_BUCKET_NAME", bucket)
    _set_setting(settings_module, "AWS_S3_REGION_NAME", region)
    _set_setting(settings_module, "AWS_DEFAULT_ACL", None)
    _set_setting(settings_module, "AWS_QUERYSTRING_AUTH", False)
    _set_setting(settings_module, "AWS_S3_FILE_OVERWRITE", False)
    _set_setting(settings_module, "AWS_S3_SIGNATURE_VERSION", "s3v4")
    _set_setting(settings_module, "AWS_S3_OBJECT_PARAMETERS", {
        "CacheControl": "private, no-store",
    })

    if endpoint:
        _set_setting(settings_module, "AWS_S3_ENDPOINT_URL", endpoint)
        _set_setting(
            settings_module,
            "AWS_S3_ADDRESSING_STYLE",
            env_str("AWS_S3_ADDRESSING_STYLE", "auto"),
        )

    installed_apps = list(_get_setting(settings_module, "INSTALLED_APPS", ()))
    if "storages" not in installed_apps:
        _set_setting(settings_module, "INSTALLED_APPS", [*installed_apps, "storages"])

    s3_options = _s3_options_for_bucket(
        bucket=bucket,
        region=region,
        endpoint=endpoint,
        access_key=env_str("AWS_ACCESS_KEY_ID"),
        secret_key=env_str("AWS_SECRET_ACCESS_KEY"),
        addressing_style=env_str("AWS_S3_ADDRESSING_STYLE", "auto") if endpoint else None,
    )

    _set_setting(
        settings_module,
        "STORAGES",
        {
            "default": {
                "BACKEND": "storages.backends.s3.S3Storage",
                "OPTIONS": s3_options,
            },
            "staticfiles": {
                "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
            },
        },
    )
    _set_setting(settings_module, "MEDIA_URL", f"s3://{bucket}/")


# 하위 호환 alias
apply_media_storage = apply_legacy_media_storage
