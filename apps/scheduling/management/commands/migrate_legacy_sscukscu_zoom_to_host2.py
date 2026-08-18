"""1회성 — legacy sscukscu@gmail.com Zoom → host_02(sedulife) 재생성."""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone
from zoneinfo import ZoneInfo

from apps.scheduling.legacy_sscukscu_host_migration import (
    ALLOWED_APPOINTMENT_IDS,
    LEGACY_HOST_EMAIL,
    SCHEDULED_FROM_KST,
    apply_migration_plan,
    build_migration_plan,
    format_dry_run_block,
    target_host_email,
)
from apps.scheduling.utils import ZoomNotConfiguredError
from apps.scheduling.zoom_hosts import get_zoom_licensed_user_emails, host_id_for_email

KST = ZoneInfo("Asia/Seoul")


class Command(BaseCommand):
    help = (
        "확정된 17건 legacy sscukscu@gmail.com Zoom을 host_02(sedulife)로 재생성합니다.\n"
        "예) /opt/venv/bin/python manage.py migrate_legacy_sscukscu_zoom_to_host2 --dry-run\n"
        "예) /opt/venv/bin/python manage.py migrate_legacy_sscukscu_zoom_to_host2 --apply"
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="READ-ONLY preflight (기본값)",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Zoom 재생성 및 DB 갱신 (17건 allowlist만)",
        )
        parser.add_argument(
            "--output-dir",
            default=".",
            help="--apply 백업 JSON/CSV 저장 경로 (기본: 현재 디렉터리)",
        )
        parser.add_argument(
            "--notify",
            action="store_true",
            help="join_url 변경 시 내담자·상담사 알림",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 --apply 허용",
        )

    def handle(self, *args, **options):
        dry_run = not options["apply"]

        if options["apply"] and not options["allow_local"]:
            engine = connection.settings_dict.get("ENGINE", "")
            if "sqlite" in engine:
                raise CommandError(
                    "로컬 SQLite에서는 --allow-local 없이 --apply를 사용할 수 없습니다."
                )

        target = target_host_email()
        if not target:
            raise CommandError(
                "host_02 이메일을 확인할 수 없습니다 (ZOOM_LICENSED_USERS)."
            )

        self.stdout.write("=== legacy sscukscu → host_02 Zoom migration ===")
        self.stdout.write(f"Licensed: {', '.join(get_zoom_licensed_user_emails())}")
        self.stdout.write(f"legacy host: {LEGACY_HOST_EMAIL}")
        self.stdout.write(f"target host: {target} ({host_id_for_email(target)})")
        self.stdout.write(
            f"scheduled_at cutoff: "
            f"{timezone.localtime(SCHEDULED_FROM_KST, KST)} (KST)"
        )
        self.stdout.write(f"allowlist: {len(ALLOWED_APPOINTMENT_IDS)} UUIDs")
        self.stdout.write("host_02 conflict check: disabled (not used)")
        self.stdout.write("")

        plan = build_migration_plan()

        for item in plan:
            self.stdout.write(format_dry_run_block(item))

        valid_count = sum(1 for item in plan if item.valid)
        invalid_count = len(plan) - valid_count

        self.stdout.write("---")
        self.stdout.write(f"TOTAL TARGET: {len(ALLOWED_APPOINTMENT_IDS)}")
        self.stdout.write(f"VALID TARGET: {valid_count}")
        self.stdout.write(f"INVALID TARGET: {invalid_count}")

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN - Zoom/DB 변경 없음.\n"
                    "실제 반영:\n"
                    "  /opt/venv/bin/python manage.py "
                    "migrate_legacy_sscukscu_zoom_to_host2 --apply"
                )
            )
            if invalid_count:
                self.stdout.write(
                    self.style.ERROR(
                        f"주의: INVALID TARGET {invalid_count}건 - "
                        "apply 전 DB 상태를 확인하세요."
                    )
                )
            return

        if valid_count == 0:
            raise CommandError("VALID TARGET 0건 - apply 중단.")

        output_dir = Path(options.get("output_dir") or ".")
        try:
            results = apply_migration_plan(
                plan,
                notify_link_change=bool(options.get("notify")),
                output_dir=output_dir,
            )
        except ZoomNotConfiguredError as exc:
            raise CommandError(str(exc)) from exc

        ok = [r for r in results if r.get("status") == "success"]
        partial = [
            r for r in results if r.get("status") == "success_old_delete_failed"
        ]
        errors = [r for r in results if r.get("status") == "error"]

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"적용 완료: 성공 {len(ok)}건, "
                f"구회의 삭제 실패 {len(partial)}건, 오류 {len(errors)}건"
            )
        )
        if results:
            self.stdout.write(f"백업 로그: {output_dir.resolve()}")

        for row in results:
            self.stdout.write(
                f"  {row.get('appointment_id')}: {row.get('status')} "
                f"old={row.get('old_zoom_meeting_id')} "
                f"new={row.get('new_zoom_meeting_id') or '-'}"
            )

        if errors:
            for row in errors:
                self.stdout.write(
                    self.style.ERROR(
                        f"  {row.get('appointment_id')}: {row.get('error_message')}"
                    )
                )
            raise CommandError(f"마이그레이션 실패 {len(errors)}건")
