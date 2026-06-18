"""내담자 계정 및 연관 데이터 완전 삭제."""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.accounts.client_purge import (
    WAITING_MATCH_PURGE_JUNE2026,
    find_client_users_for_purge,
    purge_client_users,
)
from apps.counseling.models import ApplicationStatus, CounselingApplication


class Command(BaseCommand):
    help = (
        "내담자 계정을 DB에서 완전 삭제합니다(가입 이력 없음과 동일).\n"
        "기본 대상: 2026-06 매칭 대기 목록 제거 요청 11건.\n"
        "예) python manage.py purge_client_accounts\n"
        "예) python manage.py purge_client_accounts --apply\n"
        "예) python manage.py purge_client_accounts --name 조은혜 --student-id 25111106 --apply"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="실제 삭제 (없으면 대상만 출력)",
        )
        parser.add_argument(
            "--name",
            action="append",
            dest="names",
            metavar="NAME",
            help="삭제할 내담자 이름 (여러 번 지정 가능, --student-id 와 쌍으로 사용)",
        )
        parser.add_argument(
            "--student-id",
            action="append",
            dest="student_ids",
            metavar="ID",
            help="학번 (--name 과 같은 순서로 지정, 생략 시 빈 학번)",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 실행 허용",
        )
        parser.add_argument(
            "--ignore-missing",
            action="store_true",
            help="일부 대상이 없어도 찾은 계정만 처리",
        )

    def handle(self, *args, **options):
        if not options["allow_local"]:
            self._ensure_database_ready()

        from apps.accounts.client_purge import ClientPurgeTarget

        if options["names"]:
            names = options["names"]
            student_ids = options["student_ids"] or []
            if student_ids and len(student_ids) not in (1, len(names)):
                raise CommandError(
                    "--name 개수와 --student-id 개수가 맞지 않습니다. "
                    "학번을 하나만 주면 모든 이름에 동일 적용됩니다."
                )
            if len(student_ids) == 1 and len(names) > 1:
                student_ids = student_ids * len(names)
            elif not student_ids:
                student_ids = [""] * len(names)
            targets = tuple(
                ClientPurgeTarget(name=n, student_id=sid)
                for n, sid in zip(names, student_ids, strict=True)
            )
        else:
            targets = WAITING_MATCH_PURGE_JUNE2026

        matches, missing = find_client_users_for_purge(targets)
        if missing and not options["ignore_missing"]:
            labels = ", ".join(t.label() for t in missing)
            raise CommandError(f"DB에서 찾지 못한 내담자: {labels}")

        if not matches:
            self.stdout.write(self.style.WARNING("삭제할 내담자 계정이 없습니다."))
            return

        self.stdout.write(f"대상 {len(matches)}명:")
        for match in matches:
            user = match.user
            profile = getattr(user, "client_profile", None)
            sid = getattr(profile, "student_id", "") or "—"
            waiting_apps = CounselingApplication.objects.filter(
                client=user,
                status__in=[
                    ApplicationStatus.RECEIVED,
                    ApplicationStatus.WAITING_MATCH,
                ],
            ).count()
            line = (
                f"  - {user.name} (학번 {sid}) email={user.email} "
                f"신청 {match.application_count}건(매칭대기 {waiting_apps}) "
                f"사례 {match.case_count}건"
            )
            if match.active_case_count:
                line += f" [경고: 진행 중 사례 {match.active_case_count}건]"
            self.stdout.write(line)

        dry_run = not options["apply"]
        result = purge_client_users(matches, dry_run=dry_run)
        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}완료: 사용자 {result.deleted_users}명, "
                f"신청(예상) {result.deleted_applications}건, "
                f"사례(예상) {result.deleted_cases}건"
            )
        )
        if dry_run:
            self.stdout.write("실제 삭제: python manage.py purge_client_accounts --apply")

    def _ensure_database_ready(self) -> None:
        engine = connection.settings_dict.get("ENGINE", "")
        if "sqlite" not in engine:
            return
        raise CommandError(
            "로컬 SQLite에서는 기본적으로 실행하지 않습니다. "
            "운영 DB(Railway)에서 실행하거나 --allow-local 을 사용하세요."
        )
