"""Zoom 없이 확정된 예약에 Zoom 회의 일괄 생성."""

from django.core.management.base import BaseCommand, CommandError

from apps.scheduling.services import backfill_missing_zoom_meetings
from apps.scheduling.utils import ZoomNotConfiguredError


class Command(BaseCommand):
    help = (
        "확정되었지만 Zoom 회의가 없는 비대면 예약에 join URL을 생성합니다.\n"
        "대면 상담은 제외됩니다.\n\n"
        "예시:\n"
        "  python manage.py backfill_appointment_zoom --dry-run\n"
        "  python manage.py backfill_appointment_zoom --apply"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="DB 반영 (기본: dry-run)",
        )

    def handle(self, *args, **options):
        dry_run = not options["apply"]
        try:
            created, skipped, errors = backfill_missing_zoom_meetings(dry_run=dry_run)
        except ZoomNotConfiguredError as exc:
            raise CommandError(str(exc)) from exc

        label = "would create" if dry_run else "created"
        self.stdout.write(
            self.style.SUCCESS(
                f"Zoom backfill: {label}={created}, skipped={skipped}, errors={len(errors)}"
            )
        )
        for line in errors[:20]:
            self.stdout.write(self.style.ERROR(line))
        if errors and not dry_run:
            raise CommandError(f"{len(errors)}건 Zoom 생성 실패")
