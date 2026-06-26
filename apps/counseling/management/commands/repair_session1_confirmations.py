"""골드 스탠다드 JSON 기준 1회기 미확정·일시 불일치 예약 복구."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.counseling.session1_bulk_import import (
    load_session1_matches,
    repair_session1_confirmations_from_roster,
)

DEFAULT_JSON = (
    Path(settings.BASE_DIR) / "data" / "import" / "session1_matches_bulk_202606.json"
)


class Command(BaseCommand):
    help = (
        "1회기 로스터와 DB를 대조해 PENDING/SCHEDULED·미확정 예약을 CONFIRMED로 복구합니다.\n"
        "확정(CONFIRMED) 예약 일시는 기본적으로 변경하지 않습니다 (--reschedule-confirmed).\n"
        "예시:\n"
        "  python manage.py repair_session1_confirmations\n"
        "  python manage.py repair_session1_confirmations --apply --client 이현옥"
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
            help="특정 상담사만 (예: 신영화)",
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
        parser.add_argument(
            "--fail-on-error",
            action="store_true",
            help="오류가 있으면 exit code 1",
        )
        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            help="예외·오류가 있어도 exit code 0 (Railway preDeploy용)",
        )
        parser.add_argument(
            "--reschedule-confirmed",
            action="store_true",
            help="확정(CONFIRMED) 1회기 일시가 로스터와 다를 때 자동 변경 (기본: 변경 안 함)",
        )

    def handle(self, *args, **options):
        try:
            self._run(options)
        except Exception as exc:
            if options["continue_on_error"]:
                self.stderr.write(self.style.ERROR(f"repair_session1_confirmations 중단: {exc}"))
                return
            raise

    def _run(self, options):
        if options["apply"] and not options["allow_local"]:
            self._ensure_database_ready()

        path = Path(options["json_file"])
        if not path.is_file():
            raise CommandError(f"JSON 파일을 찾을 수 없습니다: {path}")

        rows = load_session1_matches(path)
        client_names = frozenset(options["client"]) if options["client"] else None
        counselor = (options["counselor"] or "").strip() or None

        results = repair_session1_confirmations_from_roster(
            rows,
            dry_run=not options["apply"],
            skip_availability=not options["enforce_availability"],
            counselor_name=counselor,
            client_names=client_names,
            reschedule_confirmed=bool(options["reschedule_confirmed"]),
        )

        prefix = "[dry-run] " if not options["apply"] else ""
        self.stdout.write(self.style.NOTICE(f"{prefix}=== 1회기 확정 복구 ==="))
        self.stdout.write(f"{'내담자':<10} {'상담사':<10} {'상태':<16} {'내용'}")
        self.stdout.write("-" * 72)

        repaired = errors = ok = 0
        for row in sorted(results, key=lambda r: (r.counselor_name, r.client_name)):
            if row.status in ("confirmed", "created", "rescheduled"):
                repaired += 1
                style = self.style.SUCCESS
            elif row.status == "error" or row.status == "calendar_missing":
                errors += 1
                style = self.style.ERROR
            elif row.status == "ok":
                ok += 1
                style = self.style.SUCCESS
            else:
                style = self.style.WARNING
            self.stdout.write(
                style(
                    f"{row.client_name:<10} {row.counselor_name:<10} "
                    f"{row.status:<16} {row.detail}"
                )
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}복구 {repaired}건, 일치 {ok}건, 오류 {errors}건, "
                f"기타 {len(results) - repaired - ok - errors}건"
            )
        )
        if not options["apply"] and repaired:
            self.stdout.write(
                self.style.WARNING(
                    "실제 반영: python manage.py repair_session1_confirmations --apply"
                )
            )
        if errors and options["fail_on_error"]:
            raise CommandError(f"복구 실패 {errors}건")

    def _ensure_database_ready(self):
        engine = connection.settings_dict.get("ENGINE", "")
        if "sqlite" not in engine:
            return
        raise CommandError(
            "SQLite DB입니다. Railway Web 서비스 Variables에 "
            "DATABASE_URL=${{Postgres.DATABASE_URL}} 연결을 확인하세요. "
            "로컬 테스트: --allow-local"
        )
