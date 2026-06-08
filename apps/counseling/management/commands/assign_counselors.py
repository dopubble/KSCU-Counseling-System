"""CSV로 내담자–상담사 매칭 일괄 배정."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.counseling.bulk_assign import assign_counselor_rows, read_assign_rows


class Command(BaseCommand):
    help = (
        "CSV 파일로 내담자 이메일–상담사명 매칭을 일괄 배정합니다.\n"
        "필수 컬럼: email(내담자 이메일), counselor(상담자/상담사명)\n"
        "선택 컬럼: counselor_email (동명이인 상담사 구분용)\n\n"
        "예시:\n"
        "  python manage.py assign_counselors data/import/client_counselor_assignments.csv\n"
        "  python manage.py assign_counselors data/import/match.csv --dry-run\n"
        "  python manage.py assign_counselors data/import/match.csv --create-application"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            help="매칭 CSV 경로 (email, counselor 컬럼)",
        )
        parser.add_argument(
            "--total-sessions",
            type=int,
            default=10,
            help="신규 사례 생성 시 총 회기 수 (기본 10)",
        )
        parser.add_argument(
            "--create-application",
            action="store_true",
            help="상담 신청이 없으면 매칭대기 신청을 자동 생성 후 배정",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="이미 같은 상담사가 배정된 경우에도 다시 처리",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="DB 저장 없이 처리 예상만 출력",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 실행 허용 (개발·테스트용)",
        )

    def handle(self, *args, **options):
        if not options["allow_local"]:
            self._ensure_database_ready()

        path = Path(options["csv_file"])
        if not path.is_file():
            raise CommandError(f"CSV 파일을 찾을 수 없습니다: {path}")

        self.stdout.write(f"읽는 중: {path}")
        try:
            rows = read_assign_rows(path)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        if not rows:
            raise CommandError("처리할 행이 없습니다.")

        summary = assign_counselor_rows(
            rows,
            total_sessions=options["total_sessions"],
            create_missing_application=options["create_application"],
            skip_same=not options["force"],
            dry_run=options["dry_run"],
        )

        for result in summary.results:
            style = self.style.SUCCESS
            if result.action in ("skipped",):
                style = self.style.WARNING
            elif result.action == "error":
                style = self.style.ERROR
            elif result.action.startswith("would_"):
                style = self.style.NOTICE
            suffix = f" - {result.message}" if result.message else ""
            self.stdout.write(
                style(f"[{result.action}] {result.line_no}행 {result.email}{suffix}")
            )

        prefix = "[dry-run] " if options["dry_run"] else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}완료: 신규 배정 {summary.assigned}, "
                f"변경 {summary.reassigned}, 건너뜀 {summary.skipped}, "
                f"오류 {summary.errors}"
            )
        )

        if summary.errors:
            raise CommandError(f"{summary.errors}건 오류가 발생했습니다.")

    def _ensure_database_ready(self) -> None:
        db = settings.DATABASES["default"]
        engine = db.get("ENGINE", "")
        host = (db.get("HOST") or "").lower()

        if "sqlite" in engine:
            raise CommandError(
                "현재 로컬 SQLite(db.sqlite3)에 연결되어 있습니다.\n"
                "운영 DB에 배정하려면 Railway Public DATABASE_URL을 설정하세요.\n"
                "  $env:DATABASE_URL = \"postgresql://...@xxxx.rlwy.net:포트/railway\"\n"
                "  $env:DJANGO_SETTINGS_MODULE = \"kscu_counseling.settings.production\"\n"
                "로컬 테스트만 하려면 --allow-local 을 추가하세요."
            )

        if "internal" in host or host.endswith(".railway.internal"):
            raise CommandError(
                "DATABASE_URL에 postgres.railway.internal 이 들어 있습니다.\n"
                "Railway → PostgreSQL → Public Network URL (*.proxy.rlwy.net)을 사용하세요."
            )

        try:
            connection.ensure_connection()
        except Exception as exc:
            raise CommandError(f"PostgreSQL 연결 실패: {exc}") from exc
