"""기존 Zoom 회의 join_url·설정 일괄 동기화."""

from django.core.management.base import BaseCommand

from apps.scheduling.services import sync_existing_zoom_join_urls
from apps.scheduling.utils import ZoomNotConfiguredError


class Command(BaseCommand):
    help = (
        "확정된 예약의 Zoom 회의를 join_url 입장 방식으로 맞춥니다.\n"
        "  · Zoom 설정: join_before_host, waiting_room, 대체 호스트 제거\n"
        "  · DB: ZoomMeeting.join_url, Case.zoom_meeting_url 갱신\n\n"
        "예시:\n"
        "  python manage.py sync_zoom_join_urls --dry-run\n"
        "  python manage.py sync_zoom_join_urls"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="변경 없이 대상 건수만 집계",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        try:
            updated, skipped, failed, errors = sync_existing_zoom_join_urls(
                dry_run=dry_run
            )
        except ZoomNotConfiguredError as exc:
            self.stdout.write(self.style.WARNING(str(exc)))
            return

        label = "would update" if dry_run else "updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"Zoom join URL sync: {label}={updated}, skipped={skipped}, failed={failed}"
            )
        )
        for line in errors[:20]:
            self.stdout.write(self.style.ERROR(line))
        if len(errors) > 20:
            self.stdout.write(f"... and {len(errors) - 20} more errors")
