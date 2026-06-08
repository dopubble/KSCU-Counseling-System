"""1회기 확정 예약 일시 일괄 조정 (6/15 시작 등)."""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.scheduling.auto_schedule_session1 import (
    SESSION1_JUNE15_SHIFT_DAYS,
    shift_session1_confirmed_schedule,
)


class Command(BaseCommand):
    help = (
        "1회기 확정 예약 10건 일시를 지정 일수만큼 미룹니다.\n"
        "기본: 6/9 확정분 → 6/15부터 (+6일), Zoom 일정 갱신 포함.\n\n"
        "예시:\n"
        "  python manage.py shift_session1_schedule\n"
        "  python manage.py shift_session1_schedule --apply"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="DB·Zoom에 실제 반영 (기본: dry-run)",
        )
        parser.add_argument(
            "--shift-days",
            type=int,
            default=SESSION1_JUNE15_SHIFT_DAYS,
            help=f"미룰 일수 (기본 {SESSION1_JUNE15_SHIFT_DAYS})",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 실행",
        )
        parser.add_argument(
            "--enforce-availability",
            action="store_true",
            help="상담사 가용시간 검사 (기본: 관리자 일괄 조정으로 생략)",
        )

    def handle(self, *args, **options):
        if options["apply"] and not options["allow_local"]:
            self._ensure_database_ready()

        dry_run = not options["apply"]
        results = shift_session1_confirmed_schedule(
            shift_days=options["shift_days"],
            dry_run=dry_run,
            skip_availability=not options["enforce_availability"],
        )

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(self.style.NOTICE(f"{prefix}=== 1회기 일정 조정 (+{options['shift_days']}일) ==="))
        self.stdout.write(f"{'내담자':<8} {'상담사':<8} {'변경 전':<18} {'변경 후':<18} {'상태'}")
        self.stdout.write("-" * 72)

        shifted = 0
        errors = 0
        for row in sorted(
            results,
            key=lambda r: r.new_at.isoformat() if r.new_at else "",
        ):
            old_label = row.old_at.strftime("%Y-%m-%d %H:%M") if row.old_at else "—"
            new_label = row.new_at.strftime("%Y-%m-%d %H:%M") if row.new_at else "—"
            if row.status == "shifted":
                shifted += 1
                style = self.style.SUCCESS
            elif row.status == "error":
                errors += 1
                style = self.style.ERROR
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
                f"{prefix}조정 {shifted}건, 오류 {errors}건, "
                f"기타 {len(results) - shifted - errors}건"
            )
        )
        if dry_run and shifted:
            self.stdout.write(
                self.style.WARNING("실제 반영: python manage.py shift_session1_schedule --apply")
            )
        if errors:
            raise CommandError(f"일정 조정 실패 {errors}건")

    def _ensure_database_ready(self):
        if settings.DEBUG and "sqlite" in connection.settings_dict.get("ENGINE", ""):
            raise CommandError(
                "로컬 SQLite입니다. 운영 DB 반영 시 DATABASE_URL 설정 후 실행하거나 "
                "--allow-local 을 사용하세요."
            )
        if not os.environ.get("DATABASE_URL") and "sqlite" in connection.settings_dict.get(
            "ENGINE", ""
        ):
            raise CommandError("DATABASE_URL이 없습니다. Railway 운영 DB URL을 설정해 주세요.")
