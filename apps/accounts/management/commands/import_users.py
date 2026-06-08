"""CSV로 상담사·내담자 계정 일괄 등록."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.bulk_import import UserRole, import_user_rows, read_csv_rows


class Command(BaseCommand):
    help = (
        "CSV 파일로 상담사·내담자 User 계정을 일괄 등록합니다.\n"
        "필수 컬럼: email(이메일), name(이름), password(초기비밀번호)\n"
        "선택 컬럼: phone(연락처), department(소속학과), student_id(학번), "
        "birth_date(생년월일), gender(성별), role(역할 — 통합 파일용)\n\n"
        "예시:\n"
        "  python manage.py import_users --counselors data/import/counselors.csv\n"
        "  python manage.py import_users --clients data/import/clients.csv\n"
        "  python manage.py import_users --counselors c.csv --clients u.csv --dry-run"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--counselors",
            dest="counselors_csv",
            default="",
            help="상담사 CSV 경로 (role=상담사로 처리)",
        )
        parser.add_argument(
            "--clients",
            dest="clients_csv",
            default="",
            help="내담자 CSV 경로 (role=내담자로 처리)",
        )
        parser.add_argument(
            "--file",
            dest="combined_csv",
            default="",
            help="역할(role) 컬럼이 있는 통합 CSV",
        )
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help="이미 있는 이메일은 이름·연락처·비밀번호·프로필 갱신",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="DB 저장 없이 처리 예상만 출력",
        )
        parser.add_argument(
            "--counselor-pending",
            action="store_true",
            help="상담사 승인(is_approved) 없이 PENDING 상태로만 등록",
        )
        parser.add_argument(
            "--strict-passwords",
            action="store_true",
            help="Django 비밀번호 정책 검사 적용",
        )

    def handle(self, *args, **options):
        counselors_path = (options.get("counselors_csv") or "").strip()
        clients_path = (options.get("clients_csv") or "").strip()
        combined_path = (options.get("combined_csv") or "").strip()

        if not any([counselors_path, clients_path, combined_path]):
            raise CommandError(
                "--counselors, --clients, --file 중 하나 이상을 지정하세요."
            )

        all_rows = []

        if counselors_path:
            all_rows.extend(self._load_file(counselors_path, UserRole.COUNSELOR))
        if clients_path:
            all_rows.extend(self._load_file(clients_path, UserRole.CLIENT))
        if combined_path:
            all_rows.extend(self._load_file(combined_path, UserRole.CLIENT))

        if not all_rows:
            raise CommandError("등록할 데이터 행이 없습니다.")

        summary = import_user_rows(
            all_rows,
            update_existing=options["update_existing"],
            approve_counselors=not options["counselor_pending"],
            validate_passwords=options["strict_passwords"],
            dry_run=options["dry_run"],
        )

        for result in summary.results:
            style = self.style.SUCCESS
            if result.action == "skipped":
                style = self.style.WARNING
            elif result.action == "error":
                style = self.style.ERROR
            suffix = f" - {result.message}" if result.message else ""
            self.stdout.write(
                style(f"[{result.action}] {result.line_no}행 {result.email}{suffix}")
            )

        prefix = "[dry-run] " if options["dry_run"] else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}완료: 생성 {summary.created}, "
                f"갱신 {summary.updated}, 건너뜀 {summary.skipped}, "
                f"오류 {summary.errors}"
            )
        )

        if summary.errors:
            raise CommandError(f"{summary.errors}건 오류가 발생했습니다.")

    def _load_file(self, path_str: str, default_role: str):
        path = Path(path_str)
        if not path.is_file():
            raise CommandError(f"CSV 파일을 찾을 수 없습니다: {path}")
        self.stdout.write(f"읽는 중: {path} (기본 역할={default_role})")
        try:
            return read_csv_rows(path, default_role=default_role)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
