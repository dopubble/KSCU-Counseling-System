"""미래 확정 비대면 — 동시간대 동일 zoom_host_email 중복 일괄 재배정 (locked 포함)."""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone
from zoneinfo import ZoneInfo

from apps.scheduling.duplicate_zoom_host_fix import fix_duplicate_future_zoom_hosts
from apps.scheduling.utils import ZoomNotConfiguredError
from apps.scheduling.zoom_hosts import get_zoom_licensed_user_emails, host_id_for_email

KST = ZoneInfo("Asia/Seoul")


class Command(BaseCommand):
    help = (
        "오늘 이후 확정 비대면 예약에서 30분 버퍼 겹침 + 동일 zoom_host_email 중복을 찾아 "
        "알고리즘 기대 호스트로 Zoom 재생성합니다 (locked 무시).\n"
        "예) python manage.py fix_duplicate_zoom_hosts\n"
        "예) python manage.py fix_duplicate_zoom_hosts --apply\n"
        "예) python manage.py fix_duplicate_zoom_hosts --apply --all-mismatches"
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
            help="YYYY-MM-DD (KST) 시작 — 기본: 오늘 00:00",
        )
        parser.add_argument(
            "--all-mismatches",
            action="store_true",
            help="중복 클러스터 외 미래 전체 stored≠기대값도 재배정",
        )
        parser.add_argument(
            "--notify",
            action="store_true",
            help="join_url 변경 시 내담자·상담사 알림",
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
                    "로컬 SQLite에서는 --allow-local 없이 --apply를 사용할 수 없습니다."
                )

        licensed = get_zoom_licensed_user_emails()
        self.stdout.write("Licensed Zoom 사용자:")
        for index, email in enumerate(licensed, start=1):
            self.stdout.write(f"  host_{index:02d}: {email}")

        scheduled_from = None
        from_text = (options.get("from_date") or "").strip()
        if from_text:
            day = datetime.strptime(from_text, "%Y-%m-%d").date()
            scheduled_from = datetime.combine(day, datetime.min.time(), tzinfo=KST)
            self.stdout.write(f"대상: {day} 00:00 KST 이후")
        else:
            day = timezone.localtime(timezone.now(), KST).date()
            self.stdout.write(f"대상: {day} 00:00 KST 이후 (오늘 포함 미래)")

        if options.get("all_mismatches"):
            self.stdout.write(
                self.style.WARNING(
                    "--all-mismatches: 중복 외 stored≠기대값 전건도 재배정합니다."
                )
            )

        limit = options.get("limit") or None
        if limit is not None and limit <= 0:
            limit = None

        dry_run = not options["apply"]
        try:
            fixed, skipped, cluster_count, messages, clusters = (
                fix_duplicate_future_zoom_hosts(
                    dry_run=dry_run,
                    scheduled_from=scheduled_from,
                    include_all_mismatches=bool(options.get("all_mismatches")),
                    notify_link_change=bool(options.get("notify")),
                    limit=limit,
                    stop_on_rate_limit=True,
                )
            )
        except ZoomNotConfiguredError as exc:
            raise CommandError(str(exc)) from exc

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write("")
        self.stdout.write(
            self.style.NOTICE(
                f"{prefix}중복 클러스터 {cluster_count}개, "
                f"재배정 {'예정' if dry_run else '완료'} {fixed}건, "
                f"건너뜀/미처리 {skipped}건"
            )
        )

        if clusters:
            self.stdout.write("")
            self.stdout.write("=== 중복 클러스터 ===")
            for group in clusters:
                when = timezone.localtime(group[0].scheduled_at).strftime(
                    "%Y-%m-%d %H:%M"
                )
                host = host_id_for_email(
                    (getattr(group[0].zoom_meeting, "zoom_host_email", None) or "")
                )
                names = ", ".join(
                    f"{a.client.name}(s{a.session_number})" for a in group
                )
                self.stdout.write(f"  {when} {host}: {names}")

        if messages:
            self.stdout.write("")
            for line in messages[:200]:
                if line.startswith("[error]"):
                    self.stdout.write(self.style.ERROR(line))
                elif line.startswith("[rate limit]"):
                    self.stdout.write(self.style.WARNING(line))
                elif line.startswith("[would fix]") or line.startswith("[cluster]"):
                    self.stdout.write(line)
                elif line.startswith("[fixed]"):
                    self.stdout.write(self.style.SUCCESS(line))
                else:
                    self.stdout.write(line)
            if len(messages) > 200:
                self.stdout.write(f"... 외 {len(messages) - 200}건")

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN — 실제 반영:\n"
                    "  python manage.py fix_duplicate_zoom_hosts --apply\n"
                    "검증:\n"
                    "  python manage.py audit_zoom_hosts"
                )
            )
            return

        errors = [m for m in messages if m.startswith("[error]")]
        rate_limits = [m for m in messages if m.startswith("[rate limit]")]
        if (errors or rate_limits) and not options["continue_on_error"]:
            detail = f"실패 {len(errors)}건"
            if rate_limits:
                detail += f", rate limit {len(rate_limits)}건"
            raise CommandError(detail)
