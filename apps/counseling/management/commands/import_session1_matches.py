"""36건 1회기 매칭·예약 일괄 주입 (기존 매칭 삭제 후 재배정)."""

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from apps.counseling.session1_bulk_import import (
    Session1ImportError,
    format_verification_report_markdown,
    import_session1_matches,
    load_session1_matches,
    verify_session1_roster,
)

DEFAULT_JSON = (
    Path(settings.BASE_DIR) / "data" / "import" / "session1_matches_bulk_202606.json"
)


class Command(BaseCommand):
    help = (
        "JSON 매칭 데이터로 상담사–내담자 배정 및 1회기 확정 예약을 일괄 주입합니다.\n"
        "기본: 대상 36명의 기존 예약·과제·매칭을 삭제한 뒤 재생성.\n\n"
        "예시:\n"
        "  python manage.py import_session1_matches\n"
        "  python manage.py import_session1_matches --dry-run\n"
        "  python manage.py import_session1_matches --apply --allow-local\n"
        "  python manage.py import_session1_matches --apply --with-zoom"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "json_file",
            nargs="?",
            default=str(DEFAULT_JSON),
            help=f"매칭 JSON 경로 (기본: {DEFAULT_JSON.name})",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="DB에 실제 반영 (기본: dry-run)",
        )
        parser.add_argument(
            "--total-sessions",
            type=int,
            default=10,
            help="사례 총 회기 수 (기본 10)",
        )
        parser.add_argument(
            "--create-application",
            action="store_true",
            default=True,
            help="상담 신청 없으면 자동 생성 (기본: True)",
        )
        parser.add_argument(
            "--no-create-application",
            action="store_false",
            dest="create_application",
            help="상담 신청 없으면 오류",
        )
        parser.add_argument(
            "--with-zoom",
            action="store_true",
            help="1회기 확정 시 Zoom 회의 생성 (36건 API 호출 주의)",
        )
        parser.add_argument(
            "--skip-clear",
            action="store_true",
            help="기존 데이터 삭제 생략 (재실행·디버그용)",
        )
        parser.add_argument(
            "--no-full-reset",
            action="store_false",
            dest="full_reset",
            help="36명만 부분 삭제 (기본: 전체 활성 배정·예약 리셋)",
        )
        parser.set_defaults(full_reset=True)
        parser.add_argument(
            "--verify",
            action="store_true",
            help="주입 후 골드 스탠다드 대비 전수 검증",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 실행",
        )

    def handle(self, *args, **options):
        dry_run = not options["apply"]
        if options["apply"] and not options["allow_local"]:
            self._ensure_database_ready()

        path = Path(options["json_file"])
        if not path.is_file():
            raise CommandError(f"JSON 파일을 찾을 수 없습니다: {path}")

        self.stdout.write(f"읽는 중: {path}")
        try:
            rows = load_session1_matches(path)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"매칭 {len(rows)}건 로드 완료")

        try:
            summary = import_session1_matches(
                rows,
                total_sessions=options["total_sessions"],
                create_missing_application=options["create_application"],
                with_zoom=options["with_zoom"],
                dry_run=dry_run,
                skip_clear=options["skip_clear"],
                full_reset=options["full_reset"],
                verify=options["verify"],
            )
        except Session1ImportError as exc:
            raise CommandError(str(exc)) from exc

        prefix = "[dry-run] " if dry_run else ""
        cleared = summary.cleared

        self.stdout.write("")
        self.stdout.write(self.style.NOTICE(f"{prefix}=== 1단계: 기존 데이터 정리 ==="))
        if options["skip_clear"]:
            self.stdout.write("  (--skip-clear: 삭제 생략)")
        else:
            self.stdout.write(f"  사례 {cleared.cases_touched}건")
            self.stdout.write(f"  예약 삭제 {cleared.appointments_deleted}건")
            self.stdout.write(f"  일정변경요청 삭제 {cleared.schedule_requests_deleted}건")
            self.stdout.write(f"  회기자료 삭제 {cleared.session_materials_deleted}건")
            self.stdout.write(f"  신청 상태 초기화 {cleared.applications_reset}건")

        self.stdout.write("")
        self.stdout.write(self.style.NOTICE(f"{prefix}=== 2단계: 매칭·1회기 예약 ==="))
        self.stdout.write(f"{'내담자':<8} {'상담사':<8} {'1회기':<18} {'결과'}")
        self.stdout.write("-" * 60)

        for result in summary.results:
            dt_label = (
                timezone.localtime(result.first_session).strftime("%Y-%m-%d %H:%M")
                if result.first_session
                else "—"
            )
            if result.action in ("would_import", "assigned", "reassigned"):
                style = self.style.SUCCESS
            elif result.action == "error":
                style = self.style.ERROR
            else:
                style = self.style.WARNING
            self.stdout.write(
                style(
                    f"{result.client_name:<8} {result.counselor_name:<8} "
                    f"{dt_label:<18} {result.message or result.action}"
                )
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}신규 배정 {summary.assigned}, "
                f"재배정 {summary.reassigned}, "
                f"1회기 {summary.session1_confirmed}건, "
                f"오류 {summary.errors}건"
            )
        )

        if summary.errors:
            raise CommandError(f"{summary.errors}건 오류 — DB 변경 없음 (--apply 시 롤백됨)")

        if options["verify"] and not dry_run:
            report = verify_session1_roster(rows)
            self.stdout.write("")
            self.stdout.write(format_verification_report_markdown(rows, report))
            if not report.ok:
                raise CommandError("전수 검증 실패 — 위 리포트 참고")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "실제 반영: python manage.py import_session1_matches --apply"
                )
            )

    def _ensure_database_ready(self) -> None:
        if settings.DEBUG and "sqlite" in connection.settings_dict.get("ENGINE", ""):
            raise CommandError(
                "로컬 SQLite입니다. Railway Public DATABASE_URL 설정 후 실행하거나 "
                "--allow-local 을 사용하세요."
            )
        if not os.environ.get("DATABASE_URL") and "sqlite" in connection.settings_dict.get(
            "ENGINE", ""
        ):
            raise CommandError("DATABASE_URL이 없습니다.")

        try:
            connection.ensure_connection()
        except Exception as exc:
            raise CommandError(f"PostgreSQL 연결 실패: {exc}") from exc
