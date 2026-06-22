"""Zoom Server-to-Server OAuth 및 Licensed 사용자 이메일 확인."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.scheduling.utils import (
    ZoomAPIError,
    is_zoom_configured,
    list_zoom_users,
    verify_zoom_licensed_users,
)
from apps.scheduling.zoom_hosts import get_zoom_licensed_user_emails


class Command(BaseCommand):
    help = "Zoom API 키·Licensed 사용자 이메일이 같은 계정에 있는지 확인합니다."

    def handle(self, *args, **options):
        account_id = (settings.ZOOM_ACCOUNT_ID or "").strip()
        client_id = (settings.ZOOM_CLIENT_ID or "").strip()
        secret_set = bool((settings.ZOOM_CLIENT_SECRET or "").strip())

        self.stdout.write("Zoom 환경 변수:")
        self.stdout.write(f"  ZOOM_ACCOUNT_ID: {'설정됨' if account_id else '없음'}")
        self.stdout.write(f"  ZOOM_CLIENT_ID: {'설정됨' if client_id else '없음'}")
        self.stdout.write(f"  ZOOM_CLIENT_SECRET: {'설정됨' if secret_set else '없음'}")

        licensed = get_zoom_licensed_user_emails()
        self.stdout.write("Licensed 사용자 (ZOOM_LICENSED_USERS):")
        for index, email in enumerate(licensed, start=1):
            self.stdout.write(f"  host_{index:02d}: {email}")

        if not is_zoom_configured():
            raise CommandError(
                "Zoom API 키 3개가 모두 필요합니다. Railway Variables 값을 "
                "PowerShell $env:ZOOM_* 로 같은 세션에 설정한 뒤 다시 실행하세요."
            )

        try:
            users = list_zoom_users()
            matched, missing = verify_zoom_licensed_users()
        except ZoomAPIError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"\nAPI 계정 활성 사용자 {len(users)}명:"))
        for user in users:
            email = (user.get("email") or "").strip()
            user_type = user.get("type", "")
            status = user.get("status", "")
            marker = " *" if email.lower() in {e.lower() for e in licensed} else ""
            self.stdout.write(f"  - {email} ({user_type}, {status}){marker}")

        if missing:
            raise CommandError(
                "Licensed 이메일이 이 Zoom 계정에 없습니다:\n"
                + "\n".join(f"  - {email}" for email in missing)
                + "\n\n원인: (1) 예전 Zoom API 키를 쓰는 경우 (2) 이메일 오타 "
                "(3) 부계정이 아직 활성화·라이선스 미부여"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nOK — Licensed 사용자 {len(matched)}명 모두 이 API 계정에 있습니다."
            )
        )
