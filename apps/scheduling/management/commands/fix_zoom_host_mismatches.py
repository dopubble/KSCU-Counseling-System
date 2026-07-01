"""버퍼 알고리즘 기준 Zoom 호스트 불일치만 선택 재생성 (전체 recreate 대비 API 호출 최소)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from apps.scheduling.services import fix_mismatched_zoom_host_assignments
from apps.scheduling.utils import ZoomNotConfiguredError
from apps.scheduling.zoom_hosts import get_zoom_licensed_user_emails

KST = ZoneInfo("Asia/Seoul")


class Command(BaseCommand):
    help = (
        "확정 비대면 예약 중 DB zoom_host_email ≠ 30분 버퍼 배정 알고리즘인 건만 재생성합니다.\n"
        "예) python manage.py fix_zoom_host_mismatches\n"
        "예) python manage.py fix_zoom_host_mismatches --apply\n"
        "예) python manage.py fix_zoom_host_mismatches --apply --all"
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
        parser.add_argument(
            "--from-date",
            default="",
            help="YYYY-MM-DD (KST) — 이 날짜 00:00 이후 예약만 (기본: 오늘)",
        )
        parser.add_argument(
            "--to-date",
            default="",
            help="YYYY-MM-DD (KST) — 이 날짜 다음날 00:00 미만까지",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="과거 예약 포함 (기본은 --from-date=오늘 이후만)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="최대 수정 건수 (0=제한 없음)",
        )
        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            help="일부 실패·한도 초과 시에도 종료 코드 0",
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

        scheduled_from = None
        scheduled_to = None
        if not options["all"]:
            from_text = (options.get("from_date") or "").strip()
            if from_text:
                day = datetime.strptime(from_text, "%Y-%m-%d").date()
            else:
                day = timezone.localtime(timezone.now(), KST).date()
            scheduled_from = datetime.combine(day, datetime.min.time(), tzinfo=KST)
            self.stdout.write(f"대상: {day} 00:00 KST 이후 예약")

        to_text = (options.get("to_date") or "").strip()
        if to_text:
            day = datetime.strptime(to_text, "%Y-%m-%d").date()
            scheduled_to = datetime.combine(day, datetime.min.time(), tzinfo=KST) + timedelta(
                days=1
            )
            self.stdout.write(f"~ {to_text} 23:59 KST까지")

        limit = options.get("limit") or None
        if limit is not None and limit <= 0:
            limit = None

        dry_run = not options["apply"]
        try:
            fixed, skipped, messages = fix_mismatched_zoom_host_assignments(
                dry_run=dry_run,
                scheduled_from=scheduled_from,
                scheduled_to=scheduled_to,
                limit=limit,
                stop_on_rate_limit=True,
            )
        except ZoomNotConfiguredError as exc:
            raise CommandError(str(exc)) from exc

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(f"{prefix}수정 {fixed}건, 건너뜀 {skipped}건")
        )
        for line in messages[:100]:
            if dry_run or line.startswith("[would fix]"):
                self.stdout.write(line)
            elif line.startswith("[rate limit]"):
                self.stdout.write(self.style.WARNING(line))
            else:
                self.stdout.write(self.style.ERROR(line))
        if len(messages) > 100:
            self.stdout.write(f"... 외 {len(messages) - 100}건")

        if dry_run:
            self.stdout.write(
                "실제 반영: python manage.py fix_zoom_host_mismatches --apply\n"
                "핀 재적용: python manage.py ops_production_fixup --apply --continue-on-error"
            )
            return

        error_msgs = [
            m
            for m in messages
            if not m.startswith("[would fix]") and not m.startswith("[rate limit]")
        ]
        if error_msgs and not options["continue_on_error"]:
            raise CommandError(f"Zoom 호스트 수정 실패 {len(error_msgs)}건")
