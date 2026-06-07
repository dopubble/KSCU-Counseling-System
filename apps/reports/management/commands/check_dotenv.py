"""
.env / Zoom 설정 로드 상태 확인 (비밀값은 출력하지 않음).

사용: python manage.py check_dotenv
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand
from dotenv import dotenv_values

from apps.scheduling.utils import is_zoom_configured


class Command(BaseCommand):
    help = ".env 파일 경로·로드 여부·Zoom 설정 인식 상태를 점검합니다."

    def handle(self, *args, **options):
        env_file = settings.BASE_DIR / ".env"
        zoom_keys = ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET")
        email_keys = ("EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "EMAIL_HOST")

        self.stdout.write("=== Django settings ===")
        self.stdout.write(f"DJANGO_SETTINGS_MODULE = {os.environ.get('DJANGO_SETTINGS_MODULE')}")
        self.stdout.write(f"BASE_DIR               = {settings.BASE_DIR}")
        self.stdout.write(f".env path               = {env_file}")
        self.stdout.write(f".env exists             = {env_file.is_file()}")

        dotenv_disabled = os.environ.get("PYTHON_DOTENV_DISABLED", "").lower() in (
            "1",
            "true",
            "yes",
        )
        self.stdout.write(f"PYTHON_DOTENV_DISABLED = {dotenv_disabled!r}")

        self.stdout.write(f"settings.DOTENV_FILE     = {getattr(settings, 'DOTENV_FILE', '?')}")
        self.stdout.write(f"settings.DOTENV_LOADED   = {getattr(settings, 'DOTENV_LOADED', '?')}")

        self.stdout.write("\n=== Zoom (값은 출력하지 않음, 길이만 표시) ===")
        for key in zoom_keys:
            os_val = os.environ.get(key)
            settings_val = getattr(settings, key, "")
            self.stdout.write(
                f"{key}:"
                f" os.environ len={len(os_val or '')}"
                f" | settings len={len(settings_val or '')}"
                f" | set={bool((settings_val or '').strip())}"
            )

        if env_file.is_file():
            from_file = dotenv_values(env_file, encoding="utf-8")
            self.stdout.write("\n=== .env 파일 직접 파싱 (dotenv_values) ===")
            for key in zoom_keys:
                v = (from_file.get(key) or "").strip()
                self.stdout.write(f"{key}: file len={len(v)} | set={bool(v)}")

        self.stdout.write("\n=== Email (값은 출력하지 않음, 길이만 표시) ===")
        for key in email_keys:
            settings_val = ""
            if key == "EMAIL_HOST_USER":
                settings_val = getattr(settings, "EMAIL_HOST_USER", "")
            elif key == "EMAIL_HOST_PASSWORD":
                settings_val = getattr(settings, "EMAIL_HOST_PASSWORD", "")
            elif key == "EMAIL_HOST":
                settings_val = getattr(settings, "EMAIL_HOST", "")
            os_val = os.environ.get(key)
            self.stdout.write(
                f"{key}: os.environ len={len(os_val or '')}"
                f" | settings len={len(settings_val or '')}"
                f" | set={bool((settings_val or '').strip())}"
            )

        if env_file.is_file():
            from_file = dotenv_values(env_file, encoding="utf-8")
            for key in email_keys:
                v = (from_file.get(key) or "").strip()
                self.stdout.write(f"{key} (.env file): len={len(v)} set={bool(v)}")

        self.stdout.write(f"EMAIL_BACKEND = {settings.EMAIL_BACKEND}")

        self.stdout.write("\n=== 앱 로직 ===")
        self.stdout.write(f"is_zoom_configured() = {is_zoom_configured()}")

        if not is_zoom_configured():
            self.stdout.write(
                self.style.WARNING(
                    "\nZoom이 비어 있습니다. .env 위치·변수명·서버 재시작을 확인하세요."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("\nZoom 설정이 Django에 로드되었습니다."))
