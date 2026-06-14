"""골드 스탠다드 JSON 대비 DB 1회기 매칭 전수 검증."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.counseling.session1_bulk_import import (
    format_verification_report_markdown,
    load_session1_matches,
    verify_session1_roster,
)

DEFAULT_JSON = (
    Path(settings.BASE_DIR) / "data" / "import" / "session1_matches_bulk_202606.json"
)


class Command(BaseCommand):
    help = "JSON 골드 스탠다드와 DB 활성 배정·1회기 예약을 전수 대조합니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "json_file",
            nargs="?",
            default=str(DEFAULT_JSON),
            help=f"매칭 JSON 경로 (기본: {DEFAULT_JSON.name})",
        )
        parser.add_argument(
            "--fail-on-error",
            action="store_true",
            help="불일치 시 exit code 1",
        )

    def handle(self, *args, **options):
        path = Path(options["json_file"])
        if not path.is_file():
            raise CommandError(f"JSON 파일을 찾을 수 없습니다: {path}")

        try:
            rows = load_session1_matches(path)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        report = verify_session1_roster(rows)
        self.stdout.write(format_verification_report_markdown(rows, report))

        if options["fail_on_error"] and not report.ok:
            raise CommandError("전수 검증 실패")
