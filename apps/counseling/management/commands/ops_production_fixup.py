"""운영 DB 수정: 김장서율 삭제, 이명란 비대면 전환, Zoom 호스트 불일치 수정."""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.counseling.ops_fixup import apply_ops_production_fixup_june2026


class Command(BaseCommand):
    help = (
        "운영 수정 일괄 적용: 김장서율 삭제, 이명란 비대면 전환, Zoom 호스트 불일치 수정.\n"
        "예) python manage.py ops_production_fixup --apply"
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="실제 DB 반영")
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 --apply 허용",
        )
        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            help="오류가 있어도 exit 0",
        )

    def handle(self, *args, **options):
        if options["apply"] and not options["allow_local"]:
            engine = connection.settings_dict.get("ENGINE", "")
            if "sqlite" in engine:
                raise CommandError(
                    "로컬 SQLite에서는 --allow-local 없이 실행할 수 없습니다."
                )

        lines = apply_ops_production_fixup_june2026(dry_run=not options["apply"])
        prefix = "[dry-run] " if not options["apply"] else ""
        errors = 0

        self.stdout.write(self.style.NOTICE(f"{prefix}=== ops_production_fixup ==="))
        for line in lines:
            if line.status == "error":
                errors += 1
                style = self.style.ERROR
            elif line.status in ("ok", "dry_run"):
                style = self.style.SUCCESS
            else:
                style = self.style.WARNING
            self.stdout.write(style(f"{line.task}: {line.status} - {line.detail}"))

        if not options["apply"]:
            self.stdout.write("실제 반영: python manage.py ops_production_fixup --apply")

        if errors and not options["continue_on_error"]:
            raise CommandError(f"ops_production_fixup 실패 {errors}건")
