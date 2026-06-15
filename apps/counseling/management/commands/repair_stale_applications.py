"""이미 상담사 배정이 끝난 내담자의 중복 매칭대기 신청 정리."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.counseling.application_queries import stale_pending_applications
from apps.counseling.models import ApplicationStatus, CaseStatus


class Command(BaseCommand):
    help = (
        "레거시 명령 — 다른 건 추가 신청을 허용하므로 자동 정리 대상이 없습니다.\n\n"
        "예시:\n"
        "  python manage.py repair_stale_applications --dry-run\n"
        "  python manage.py repair_stale_applications"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="DB 변경 없이 대상만 출력",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 실행 허용",
        )

    def handle(self, *args, **options):
        if not options["allow_local"]:
            self._ensure_database_ready()

        stale_qs = stale_pending_applications()
        count = stale_qs.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS("정리할 중복 신청이 없습니다."))
            return

        self.stdout.write(f"중복 매칭대기 신청 {count}건")

        cancelled = 0
        for app in stale_qs:
            active_case = (
                app.client.client_cases.filter(
                    status=CaseStatus.ACTIVE,
                    counselor__isnull=False,
                )
                .select_related("counselor")
                .first()
            )
            counselor_name = active_case.counselor.name if active_case and active_case.counselor else "?"
            case_number = active_case.case_number if active_case else "?"
            line = (
                f"{app.client.name} ({app.client.email}) - "
                f"신청 {app.created_at:%Y-%m-%d} / "
                f"실제 배정: {counselor_name} ({case_number})"
            )
            if options["dry_run"]:
                self.stdout.write(self.style.NOTICE(f"[would_cancel] {line}"))
                cancelled += 1
                continue

            with transaction.atomic():
                app.status = ApplicationStatus.CANCELLED
                app.save(update_fields=["status", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"[cancelled] {line}"))
            cancelled += 1

        prefix = "[dry-run] " if options["dry_run"] else ""
        self.stdout.write(
            self.style.SUCCESS(f"{prefix}처리 완료: {cancelled}건")
        )

    def _ensure_database_ready(self) -> None:
        db = settings.DATABASES["default"]
        engine = db.get("ENGINE", "")
        host = (db.get("HOST") or "").lower()

        if "sqlite" in engine:
            raise CommandError(
                "운영 DB에서 실행하려면 Railway Public DATABASE_URL을 설정하세요.\n"
                "로컬 테스트: --allow-local"
            )

        if "internal" in host or host.endswith(".railway.internal"):
            raise CommandError("Public DATABASE_URL (*.proxy.rlwy.net)을 사용하세요.")

        try:
            connection.ensure_connection()
        except Exception as exc:
            raise CommandError(f"PostgreSQL 연결 실패: {exc}") from exc
