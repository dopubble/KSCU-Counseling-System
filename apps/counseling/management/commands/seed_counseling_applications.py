"""등록된 내담자에게 상담 신청(매칭대기)을 일괄 생성."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.counseling.emailing import send_new_application_notification
from apps.counseling.models import CounselingApplication
from apps.counseling.constants import DEFAULT_COUNSELING_TYPES, normalize_counseling_types
from apps.counseling.seed_applications import (
    DEFAULT_REASON,
    SeedApplicationRow,
    build_rows_for_all_active_clients,
    read_seed_rows,
    seed_application_rows,
)


class Command(BaseCommand):
    help = (
        "등록된 내담자 계정에 상담 신청을 생성합니다 (상태: 매칭대기).\n"
        "관리자·상담사 매칭 화면에서 일반 신청과 동일하게 처리됩니다.\n\n"
        "예시:\n"
        "  python manage.py seed_counseling_applications --all-clients\n"
        "  python manage.py seed_counseling_applications --clients-csv data/import/clients.csv\n"
        "  python manage.py seed_counseling_applications --email a@kcu.ac.kr --dry-run"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--all-clients",
            action="store_true",
            help="ACTIVE 상태의 모든 내담자에게 신청 생성",
        )
        parser.add_argument(
            "--clients-csv",
            dest="clients_csv",
            default="",
            help="내담자 이메일 CSV (email 필수, 상담유형·사유·희망일정 선택)",
        )
        parser.add_argument(
            "--email",
            action="append",
            dest="emails",
            default=[],
            help="개별 이메일 (여러 번 지정 가능)",
        )
        parser.add_argument(
            "--counseling-type",
            default=",".join(DEFAULT_COUNSELING_TYPES),
            help=f"기본 상담 유형 (쉼표 구분, 기본: {','.join(DEFAULT_COUNSELING_TYPES)})",
        )
        parser.add_argument(
            "--reason",
            default=DEFAULT_REASON,
            help="기본 상담 사유",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="기존 접수/매칭대기 신청이 있어도 새 신청 생성 (기본은 건너뜀)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="DB 저장 없이 처리 예상만 출력",
        )
        parser.add_argument(
            "--notify",
            action="store_true",
            help="생성된 각 신청에 대해 관리자 알림 이메일 발송",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 실행 허용 (개발·테스트용)",
        )

    def handle(self, *args, **options):
        if not options["allow_local"]:
            self._ensure_database_ready()

        rows = self._collect_rows(options)
        if not rows:
            raise CommandError("처리할 내담자가 없습니다.")

        summary = seed_application_rows(
            rows,
            skip_existing=not options["force"],
            dry_run=options["dry_run"],
        )

        if options["notify"] and not options["dry_run"] and summary.created:
            self._send_notifications(summary.results)

        for result in summary.results:
            style = self.style.SUCCESS
            if result.action in ("skipped",):
                style = self.style.WARNING
            elif result.action == "error":
                style = self.style.ERROR
            elif result.action == "would_create":
                style = self.style.NOTICE
            suffix = f" - {result.message}" if result.message else ""
            line = f"{result.line_no}행 " if result.line_no else ""
            self.stdout.write(style(f"[{result.action}] {line}{result.email}{suffix}"))

        prefix = "[dry-run] " if options["dry_run"] else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}완료: 생성 {summary.created}, "
                f"건너뜀 {summary.skipped}, 오류 {summary.errors}"
            )
        )

        if summary.errors:
            raise CommandError(f"{summary.errors}건 오류가 발생했습니다.")

    def _collect_rows(self, options) -> list[SeedApplicationRow]:
        rows: list[SeedApplicationRow] = []

        if options["all_clients"]:
            rows.extend(build_rows_for_all_active_clients())

        csv_path = (options.get("clients_csv") or "").strip()
        if csv_path:
            path = Path(csv_path)
            if not path.is_file():
                raise CommandError(f"CSV 파일을 찾을 수 없습니다: {path}")
            self.stdout.write(f"읽는 중: {path}")
            try:
                rows.extend(read_seed_rows(path))
            except ValueError as exc:
                raise CommandError(str(exc)) from exc

        default_types = normalize_counseling_types(options["counseling_type"])
        default_reason = options["reason"]
        for email in options.get("emails") or []:
            email = email.strip().lower()
            if email:
                rows.append(
                    SeedApplicationRow(
                        email=email,
                        counseling_types=default_types,
                        reason=default_reason,
                    )
                )

        if not any([options["all_clients"], csv_path, options.get("emails")]):
            raise CommandError(
                "--all-clients, --clients-csv, --email 중 하나 이상을 지정하세요."
            )

        if not rows:
            return rows

        # CSV/--all-clients 행에 기본값이 비어 있으면 CLI 기본값 적용
        for row in rows:
            if not row.counseling_types:
                row.counseling_types = default_types
            if not row.reason:
                row.reason = default_reason

        # 이메일 중복 제거 (먼저 나온 행 유지)
        seen: set[str] = set()
        deduped: list[SeedApplicationRow] = []
        for row in rows:
            if row.email in seen:
                continue
            seen.add(row.email)
            deduped.append(row)
        return deduped

    def _send_notifications(self, results) -> None:
        app_ids = [r.application_id for r in results if r.action == "created" and r.application_id]
        if not app_ids:
            return
        apps = CounselingApplication.objects.filter(pk__in=app_ids).select_related("client")
        sent = 0
        for application in apps:
            try:
                if send_new_application_notification(application):
                    sent += 1
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(
                        f"알림 메일 실패 ({application.client.email}): {exc}"
                    )
                )
        self.stdout.write(self.style.NOTICE(f"관리자 알림 메일 발송: {sent}건"))

    def _ensure_database_ready(self) -> None:
        db = settings.DATABASES["default"]
        engine = db.get("ENGINE", "")
        host = (db.get("HOST") or "").lower()

        if "sqlite" in engine:
            raise CommandError(
                "현재 로컬 SQLite(db.sqlite3)에 연결되어 있습니다.\n"
                "운영 DB에 접수하려면 Railway Public DATABASE_URL을 설정하세요.\n"
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
