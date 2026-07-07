"""배포 전 안전 검사 — 에페메럴 미디어 등 재배포 시 데이터 유실 위험을 차단."""

from __future__ import annotations

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _on_railway() -> bool:
    return bool(
        os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("RAILWAY_SERVICE_ID")
    )


class Command(BaseCommand):
    help = (
        "Railway 배포 전 안전 검사. 에페메럴 미디어(Volume/S3 미설정)면 배포를 중단합니다.\n"
        "일상 배포는 migrate만 실행되며, Zoom·1회기 복구·계정 삭제는 자동 실행되지 않습니다."
    )

    def handle(self, *args, **options):
        if not _on_railway():
            self.stdout.write(
                self.style.NOTICE(
                    "check_deploy_safety: Railway가 아니므로 미디어 검사를 건너뜁니다."
                )
            )
            return

        mode = getattr(settings, "MEDIA_STORAGE_MODE", "ephemeral")
        media_root = getattr(settings, "MEDIA_ROOT", None)

        if mode == "ephemeral":
            raise CommandError(
                "Railway에서 MEDIA_ROOT(Volume) 또는 MEDIA_USE_S3+AWS_* 가 설정되지 않았습니다. "
                "재배포 시 업로드 파일이 유실됩니다. "
                "Volume 마운트(/data/media) + MEDIA_ROOT=/data/media 를 설정한 뒤 다시 배포하세요. "
                "docs/RAILWAY_DEPLOY.md §9.1 참고."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"check_deploy_safety: ok (media={mode}, MEDIA_ROOT={media_root})"
            )
        )
        self.stdout.write(
            "배포 시 자동 실행: migrate만. "
            "ops_production_fixup / repair_session1_confirmations / purge_client_accounts 는 수동 실행."
        )
