"""
동의서 전용 비공개 스토리지 — 기존 미디어(게시판·회기자료)와 분리.

환경 변수 CONSENT_AWS_* 가 있으면 동의서만 S3에 저장합니다.
없으면 consent 백엔드는 default(기존 파일시스템)와 동일 — 운영 변경 없이 배포 가능.
"""

from __future__ import annotations

import sys

from kscu_counseling.settings.media_storage import (
    _get_setting,
    _set_setting,
    _s3_options_for_bucket,
)


def apply_consent_storage(
    settings_module,
    *,
    env_str,
    on_railway: bool,
) -> str:
    """
    STORAGES['consent'] 설정.

    Returns: 's3' | 'filesystem'
    """
    bucket = env_str("CONSENT_AWS_STORAGE_BUCKET_NAME")
    key = env_str("CONSENT_AWS_ACCESS_KEY_ID")
    secret = env_str("CONSENT_AWS_SECRET_ACCESS_KEY")

    _ensure_storages_shell(settings_module)

    storages = dict(_get_setting(settings_module, "STORAGES"))

    if bucket and key and secret:
        region = env_str("CONSENT_AWS_S3_REGION_NAME", "ap-northeast-2")
        endpoint = env_str("CONSENT_AWS_S3_ENDPOINT_URL")
        prefix = env_str("CONSENT_AWS_LOCATION", "consents").strip("/")

        _set_setting(settings_module, "CONSENT_AWS_STORAGE_BUCKET_NAME", bucket)
        _set_setting(settings_module, "CONSENT_AWS_ACCESS_KEY_ID", key)
        _set_setting(settings_module, "CONSENT_AWS_SECRET_ACCESS_KEY", secret)
        _set_setting(settings_module, "CONSENT_AWS_S3_REGION_NAME", region)
        if endpoint:
            _set_setting(settings_module, "CONSENT_AWS_S3_ENDPOINT_URL", endpoint)
            _set_setting(
                settings_module,
                "CONSENT_AWS_S3_ADDRESSING_STYLE",
                env_str("CONSENT_AWS_S3_ADDRESSING_STYLE", "auto"),
            )
        _set_setting(settings_module, "CONSENT_AWS_LOCATION", prefix)

        installed_apps = list(_get_setting(settings_module, "INSTALLED_APPS", ()))
        if "storages" not in installed_apps:
            _set_setting(settings_module, "INSTALLED_APPS", [*installed_apps, "storages"])

        s3_options = _s3_options_for_bucket(
            bucket=bucket,
            region=region,
            endpoint=endpoint,
            location=prefix,
            access_key=key,
            secret_key=secret,
            addressing_style=env_str("CONSENT_AWS_S3_ADDRESSING_STYLE", "auto") if endpoint else None,
        )
        storages["consent"] = {
            "BACKEND": "apps.documents.storage.ConsentMediaStorage",
            "OPTIONS": {"s3_options": s3_options},
        }
        mode = "s3"
    else:
        storages["consent"] = {
            "BACKEND": "apps.documents.storage.ConsentMediaStorage",
            "OPTIONS": {},
        }
        mode = "filesystem"

    _set_setting(settings_module, "STORAGES", storages)

    if on_railway:
        if mode == "s3":
            print(
                f"[kscu] consent storage=S3 (private, isolated) bucket={bucket}",
                file=sys.stderr,
            )
        else:
            print(
                "[kscu] consent storage=filesystem (same as legacy media — no env change required)",
                file=sys.stderr,
            )
    return mode


def _ensure_storages_shell(settings_module) -> None:
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
