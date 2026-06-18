from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from apps.counseling.session1_bulk_import import force_client_session1_schedule


def _parse_local_dt(text: str) -> datetime:
    raw = (text or "").strip()
    if not raw:
        raise CommandError("--to 값이 비어 있습니다. 예: '2026-06-25 16:00'")
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise CommandError(
            f"날짜 형식이 올바르지 않습니다: {raw!r} (형식: YYYY-MM-DD HH:MM)"
        ) from exc


class Command(BaseCommand):
    help = (
        "관리자용: 활성 사례 1회기 일정을 강제 설정합니다(중복 취소·확정).\n"
        '예) python manage.py admin_force_session1 --client "김아름" '
        '--to "2026-06-25 16:00" --apply'
    )

    def add_arguments(self, parser):
        parser.add_argument("--client", required=True, help="내담자 이름(정확히)")
        parser.add_argument("--to", dest="to_dt", required=True, help="일시 YYYY-MM-DD HH:MM")
        parser.add_argument(
            "--force",
            action="store_true",
            help="상담사 가용시간 검사를 건너뜁니다.",
        )
        parser.add_argument("--apply", action="store_true", help="실제 DB 변경")
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

        client_name = (options["client"] or "").strip()
        to_dt = _parse_local_dt(options["to_dt"])
        tz = timezone.get_current_timezone()
        to_aware = timezone.make_aware(to_dt, tz)

        result = force_client_session1_schedule(
            client_name=client_name,
            scheduled_at=to_aware,
            dry_run=not options["apply"],
            skip_availability=options["force"] or True,
        )

        prefix = "[dry-run] " if not options["apply"] else ""
        if result.status == "error":
            raise CommandError(result.detail)
        if result.status == "ok":
            self.stdout.write(self.style.SUCCESS(f"{prefix}{result.detail}"))
        else:
            self.stdout.write(self.style.WARNING(f"{prefix}{result.detail}"))
        if not options["apply"]:
            self.stdout.write("실제 반영: --apply 추가")
