"""특정 사례·회기 Zoom 호스트 강제 지정 (운영 수동 실행용)."""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.counseling.ops_fixup import force_appointment_zoom_host


class Command(BaseCommand):
    help = (
        "확정 비대면 예약의 Zoom 호스트를 강제 지정합니다.\n"
        "예) python manage.py pin_case_zoom_host --case CASE-2026-0025 --session 2 --host host_02 --apply"
    )

    def add_arguments(self, parser):
        parser.add_argument("--case", required=True, help="사례 번호 (예: CASE-2026-0025)")
        parser.add_argument("--session", type=int, required=True, help="회차 번호")
        parser.add_argument("--host", required=True, help="host_01 또는 host_02")
        parser.add_argument("--client-name", default="", help="내담자 이름 (선택)")
        parser.add_argument("--client-email", default="", help="내담자 이메일 (선택)")
        parser.add_argument("--counselor-name", default="", help="상담사 이름 (선택)")
        parser.add_argument("--apply", action="store_true", help="실제 반영")

    def handle(self, *args, **options):
        if options["apply"]:
            engine = connection.settings_dict.get("ENGINE", "")
            if "sqlite" in engine:
                raise CommandError("로컬 SQLite에서는 --apply를 사용할 수 없습니다.")

        from apps.counseling.models import Case

        case = Case.objects.filter(case_number=options["case"]).select_related(
            "client", "counselor"
        ).first()
        if not case:
            raise CommandError(f"사례 없음: {options['case']}")

        client_name = options["client_name"] or case.client.name
        client_email = options["client_email"] or case.client.email
        counselor_name = options["counselor_name"] or (
            case.counselor.name if case.counselor else ""
        )

        result = force_appointment_zoom_host(
            client_name=client_name,
            client_email=client_email,
            counselor_name=counselor_name,
            scheduled_label="",
            host_id=options["host"],
            session_number=options["session"],
            case_number=options["case"],
            dry_run=not options["apply"],
        )
        self.stdout.write(f"{result.task}: {result.status} — {result.detail}")
        if result.status == "error":
            raise CommandError(result.detail)
