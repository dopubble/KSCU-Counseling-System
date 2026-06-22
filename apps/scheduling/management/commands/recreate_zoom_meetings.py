"""확정 비대면 예약 Zoom 회의 전체 재생성 (계정 교체·호스트 재배정)."""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.scheduling.services import recreate_all_zoom_meetings
from apps.scheduling.utils import (
    ZoomAPIError,
    ZoomNotConfiguredError,
    is_zoom_configured,
    list_zoom_users,
    verify_zoom_licensed_users,
)
from apps.scheduling.zoom_hosts import get_zoom_licensed_user_emails


class Command(BaseCommand):
    help = (
        "확정 비대면 예약의 Zoom 회의를 Licensed 호스트 1/2에 재배정해 전부 재생성합니다.\n"
        "계정 교체 후 1회 실행하세요.\n"
        "예) python manage.py recreate_zoom_meetings\n"
        "예) python manage.py recreate_zoom_meetings --apply"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="실제 Zoom API 호출 및 DB 갱신",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 --apply 허용",
        )

    def handle(self, *args, **options):
        if options["apply"] and not options["allow_local"]:
            engine = connection.settings_dict.get("ENGINE", "")
            if "sqlite" in engine:
                raise CommandError(
                    "로컬 SQLite에서는 --allow-local 없이 실행할 수 없습니다."
                )

        licensed = get_zoom_licensed_user_emails()
        self.stdout.write("Licensed Zoom 사용자:")
        for index, email in enumerate(licensed, start=1):
            self.stdout.write(f"  host_{index:02d}: {email}")

        if options["apply"]:
            self._verify_zoom_account()

        dry_run = not options["apply"]
        try:
            recreated, skipped, messages = recreate_all_zoom_meetings(dry_run=dry_run)
        except ZoomNotConfiguredError as exc:
            raise CommandError(str(exc)) from exc

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}재생성 {recreated}건, 건너뜀 {skipped}건"
            )
        )
        for line in messages[:100]:
            if dry_run:
                self.stdout.write(line)
            else:
                self.stdout.write(self.style.ERROR(line))
        if len(messages) > 100:
            self.stdout.write(f"... 외 {len(messages) - 100}건")

        if dry_run:
            self.stdout.write("실제 반영: python manage.py recreate_zoom_meetings --apply")
        elif messages:
            raise CommandError(f"Zoom 재생성 실패 {len(messages)}건")

    def _verify_zoom_account(self) -> None:
        if not is_zoom_configured():
            raise CommandError(
                "ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET이 필요합니다.\n"
                "DATABASE_URL만 설정하면 안 됩니다. Railway Variables의 Zoom 키 3개를 "
                "같은 PowerShell 세션에 함께 넣어 주세요."
            )
        try:
            matched, missing = verify_zoom_licensed_users()
        except ZoomAPIError as exc:
            raise CommandError(str(exc)) from exc

        if missing:
            users = list_zoom_users()
            listed = "\n".join(
                f"  - {(user.get('email') or '').strip()} "
                f"({user.get('type', '')}, {user.get('status', '')})"
                for user in users[:30]
            )
            raise CommandError(
                "현재 Zoom API 계정에서 Licensed 사용자를 찾지 못했습니다:\n"
                + "\n".join(f"  - {email}" for email in missing)
                + "\n\n이 API 키가 새 줌 계정(sscukscu)의 Server-to-Server OAuth 앱인지 "
                "확인하세요. 로컬 .env의 예전 키가 쓰이면 이 오류가 납니다.\n"
                f"계정에 등록된 사용자(최대 30명):\n{listed or '  (없음)'}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Zoom 계정 확인 OK — Licensed 사용자 {len(matched)}명 매칭"
            )
        )
