"""내담자 상담 신청 — 주요 호소 문제 일괄 반영."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.counseling.client_complaint_seed import (
    CLIENT_COMPLAINT_SEEDS,
    MAX_REASON_LENGTH,
    normalize_reason,
)
from apps.counseling.client_complaint_update import update_client_complaints


class Command(BaseCommand):
    help = (
        "스프레드시트 기준 주요 호소 문제를 각 내담자 상담 신청(reason)에 반영합니다.\n"
        "데이터: apps/counseling/client_complaint_seed.py\n\n"
        "예시:\n"
        "  python manage.py update_client_complaints --dry-run\n"
        "  python manage.py update_client_complaints --apply\n"
        "  python manage.py update_client_complaints --apply --only-default-reason"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="DB에 실제 반영 (기본: dry-run)",
        )
        parser.add_argument(
            "--only-default-reason",
            action="store_true",
            help="관리자 일괄 접수 등 기본 문구인 신청만 덮어씀",
        )
        parser.add_argument(
            "--create-missing",
            action="store_true",
            help="상담 신청이 없는 내담자에게 신청을 생성하고 호소 문제를 저장",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 실행",
        )

    def handle(self, *args, **options):
        if options["apply"] and not options["allow_local"]:
            self._ensure_database_ready()

        over_limit = [
            (s.name, len(normalize_reason(s.reason)))
            for s in CLIENT_COMPLAINT_SEEDS
            if len(normalize_reason(s.reason)) > MAX_REASON_LENGTH
        ]
        if over_limit:
            raise CommandError(f"100자 초과 항목: {over_limit}")

        dry_run = not options["apply"]
        summary = update_client_complaints(
            dry_run=dry_run,
            only_default_reason=options["only_default_reason"],
            create_missing=options["create_missing"],
        )

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.NOTICE(f"{prefix}주요 호소 문제 반영 ({len(CLIENT_COMPLAINT_SEEDS)}명)")
        )
        self.stdout.write(f"{'이름':<8} {'이메일':<28} {'상태':<18} {'호소문제(앞 40자)'}")
        self.stdout.write("-" * 90)

        for row in summary.results:
            preview = (row.reason[:40] + "…") if len(row.reason) > 40 else row.reason
            line = f"{row.name:<8} {row.email:<28} {row.action:<18} {preview}"
            if row.action in ("updated", "would_update"):
                self.stdout.write(self.style.SUCCESS(line))
            elif row.action == "skipped":
                self.stdout.write(self.style.WARNING(f"{line} ({row.message})"))
            elif row.action in ("missing_user", "missing_application", "error"):
                self.stdout.write(self.style.ERROR(f"{line} ({row.message})"))
            elif row.action in ("created", "would_create"):
                self.stdout.write(self.style.SUCCESS(f"{line} ({row.message})"))
            else:
                self.stdout.write(line)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}반영 {summary.updated}건, "
                f"건너뜀 {summary.skipped}건, "
                f"계정없음 {summary.missing_user}건, "
                f"신청없음 {summary.missing_application}건, "
                f"오류 {summary.errors}건"
            )
        )
        if dry_run and summary.updated:
            self.stdout.write(
                self.style.WARNING(
                    "실제 반영: python manage.py update_client_complaints --apply"
                )
            )

    def _ensure_database_ready(self) -> None:
        db = settings.DATABASES["default"]
        engine = db.get("ENGINE", "")
        host = (db.get("HOST") or "").lower()

        if "sqlite" in engine:
            raise CommandError(
                "운영 DB 반영 시 Railway Public DATABASE_URL을 설정하세요.\n"
                "로컬 테스트: --allow-local"
            )
        if "internal" in host or host.endswith(".railway.internal"):
            raise CommandError("Public DATABASE_URL (*.proxy.rlwy.net)을 사용하세요.")

        try:
            connection.ensure_connection()
        except Exception as exc:
            raise CommandError(f"PostgreSQL 연결 실패: {exc}") from exc
