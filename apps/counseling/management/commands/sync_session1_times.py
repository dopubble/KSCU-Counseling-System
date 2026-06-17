"""골드 스탠다드 JSON과 DB 1회기 확정 일시 동기화."""

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.counseling.session1_bulk_import import (
    load_session1_matches,
    sync_session1_times_from_roster,
)

DEFAULT_JSON = (
    Path(settings.BASE_DIR) / "data" / "import" / "session1_matches_bulk_202606.json"
)


class Command(BaseCommand):
    help = (
        "session1_matches_bulk JSON의 first_session과 DB 1회기 확정 일시를 맞춥니다.\n"
        "예시:\n"
        "  python manage.py sync_session1_times\n"
        "  python manage.py sync_session1_times --apply --counselor 천옥희"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "json_file",
            nargs="?",
            default=str(DEFAULT_JSON),
            help="매칭 JSON 경로",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="DB·Zoom에 실제 반영",
        )
        parser.add_argument(
            "--counselor",
            type=str,
            default="",
            help="특정 상담사만 (예: 천옥희)",
        )
        parser.add_argument(
            "--client",
            action="append",
            default=[],
            help="특정 내담자만 (여러 번 지정 가능)",
        )
        parser.add_argument(
            "--enforce-availability",
            action="store_true",
            help="상담사 가용시간 검사",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 --apply 허용",
        )

    def handle(self, *args, **options):
        if options["apply"] and not options["allow_local"]:
            self._ensure_database_ready()

        path = Path(options["json_file"])
        if not path.is_file():
            raise CommandError(f"JSON 파일을 찾을 수 없습니다: {path}")

        rows = load_session1_matches(path)
        client_names = frozenset(options["client"]) if options["client"] else None
        counselor = (options["counselor"] or "").strip() or None

        results = sync_session1_times_from_roster(
            rows,
            dry_run=not options["apply"],
            skip_availability=not options["enforce_availability"],
            counselor_name=counselor,
            client_names=client_names,
        )

        prefix = "[dry-run] " if not options["apply"] else ""
        self.stdout.write(self.style.NOTICE(f"{prefix}=== 1회기 일시 동기화 ==="))
        self.stdout.write(
            f"{'내담자':<8} {'상담사':<8} {'변경 전':<18} {'변경 후':<18} {'상태'}"
        )
        self.stdout.write("-" * 72)

        synced = errors = 0
        for row in sorted(
            results,
            key=lambda r: (r.counselor_name, r.client_name),
        ):
            old_label = row.old_at.strftime("%Y-%m-%d %H:%M") if row.old_at else "—"
            new_label = row.new_at.strftime("%Y-%m-%d %H:%M") if row.new_at else "—"
            if row.status in ("sync", "synced"):
                synced += 1
                style = self.style.SUCCESS
            elif row.status == "error":
                errors += 1
                style = self.style.ERROR
            elif row.status == "ok":
                style = self.style.SUCCESS
            else:
                style = self.style.WARNING
            self.stdout.write(
                style(
                    f"{row.client_name:<8} {row.counselor_name:<8} "
                    f"{old_label:<18} {new_label:<18} {row.detail or row.status}"
                )
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}변경 {synced}건, 오류 {errors}건, "
                f"기타 {len(results) - synced - errors}건"
            )
        )
        if not options["apply"] and synced:
            self.stdout.write(
                self.style.WARNING(
                    "실제 반영: python manage.py sync_session1_times --apply --allow-local"
                )
            )
        if errors:
            raise CommandError(f"동기화 실패 {errors}건")

    def _ensure_database_ready(self):
        if settings.DEBUG and "sqlite" in connection.settings_dict.get("ENGINE", ""):
            raise CommandError(
                "로컬 SQLite입니다. --allow-local 또는 운영 DATABASE_URL을 사용하세요."
            )
        if not os.environ.get("DATABASE_URL") and "sqlite" in connection.settings_dict.get(
            "ENGINE", ""
        ):
            raise CommandError("DATABASE_URL이 없습니다. Railway 운영 DB URL을 설정해 주세요.")