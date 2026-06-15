"""기존 Zoom 회의에 상담사 대체 호스트(alternative_hosts) 일괄 등록."""

from django.core.management.base import BaseCommand

from apps.scheduling.services import sync_zoom_alternative_hosts
from apps.scheduling.utils import ZoomNotConfiguredError, is_zoom_configured


class Command(BaseCommand):
    help = (
        "확정된 예약의 Zoom 회의에 담당 상담사 이메일을 대체 호스트로 등록합니다.\n"
        "상담사는 개인 Zoom 계정(동일 이메일)으로 join 링크 입장이 가능합니다.\n\n"
        "예시:\n"
        "  python manage.py sync_zoom_alternative_hosts --dry-run\n"
        "  python manage.py sync_zoom_alternative_hosts"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Zoom API 호출 없이 대상 건수만 확인",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if not is_zoom_configured():
            self.stdout.write(
                self.style.WARNING("Zoom API 미설정 — sync_zoom_alternative_hosts 건너뜀")
            )
            return

        try:
            updated, skipped, failed, errors = sync_zoom_alternative_hosts(
                dry_run=dry_run
            )
        except ZoomNotConfiguredError as exc:
            self.stdout.write(self.style.WARNING(str(exc)))
            return

        for line in errors:
            self.stdout.write(self.style.ERROR(line))

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}완료 — 갱신 {updated}건, 건너뜀 {skipped}건, 실패 {failed}건"
            )
        )
