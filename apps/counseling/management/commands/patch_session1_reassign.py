"""특정 내담자 배정 해제 후 다른 상담사로 1회기 재배정."""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.counseling.models import CounselingMethod
from apps.counseling.session1_bulk_import import (
    Session1MatchRow,
    _build_name_index,
    _import_one_row,
    clear_matching_data,
    resolve_client_by_name,
)
from apps.accounts.models import UserRole


class Command(BaseCommand):
    help = (
        "지정 내담자의 기존 배정·1회기 예약을 정리한 뒤 새 상담사로 재배정합니다.\n"
        "다른 내담자/상담사 데이터는 건드리지 않습니다."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="DB 반영 (기본: dry-run)",
        )

    def handle(self, *args, **options):
        dry_run = not options["apply"]
        prefix = "[dry-run] " if dry_run else ""

        clear_names = ["구현정", "강윤정"]
        import_rows = [
            Session1MatchRow(
                counselor_name="양은영",
                client_name="구현정",
                first_session=timezone.make_aware(
                    datetime(2026, 7, 1, 10, 0),
                    timezone.get_current_timezone(),
                ),
                counseling_method=CounselingMethod.REMOTE,
            ),
            Session1MatchRow(
                counselor_name="최윤희",
                client_name="강윤정",
                first_session=timezone.make_aware(
                    datetime(2026, 7, 1, 15, 0),
                    timezone.get_current_timezone(),
                ),
                counseling_method=CounselingMethod.IN_PERSON,
            ),
        ]

        clients_to_clear = []
        for name in clear_names:
            user, err = resolve_client_by_name(name)
            if not user:
                raise CommandError(err)
            clients_to_clear.append(user)

        client_index = _build_name_index(UserRole.CLIENT)

        if dry_run:
            cleared = clear_matching_data(client_users=clients_to_clear, dry_run=True)
            self.stdout.write(
                f"{prefix}정리 대상: {', '.join(clear_names)} "
                f"(사례 {cleared.cases_touched}건, 예약 삭제 {cleared.appointments_deleted}건)"
            )
            for row in import_rows:
                result = _import_one_row(
                    row,
                    client_index=client_index,
                    total_sessions=10,
                    create_missing_application=True,
                    with_zoom=False,
                    dry_run=True,
                )
                self.stdout.write(f"{prefix}{result.client_name} → {result.counselor_name}: {result.message}")
            self.stdout.write(self.style.WARNING("적용: python manage.py patch_session1_reassign --apply"))
            return

        with transaction.atomic():
            cleared = clear_matching_data(client_users=clients_to_clear, dry_run=False)
            self.stdout.write(
                self.style.SUCCESS(
                    f"정리 완료: 사례 {cleared.cases_touched}건, "
                    f"예약 {cleared.appointments_deleted}건 삭제"
                )
            )
            for row in import_rows:
                result = _import_one_row(
                    row,
                    client_index=client_index,
                    total_sessions=10,
                    create_missing_application=True,
                    with_zoom=False,
                    dry_run=False,
                )
                if result.action == "error":
                    raise CommandError(f"{result.client_name}: {result.message}")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{result.client_name} → {result.counselor_name}: {result.message}"
                    )
                )
