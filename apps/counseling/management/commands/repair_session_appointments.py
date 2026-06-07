"""SessionScheduleChangeRequest만 있는 회기 → PENDING Appointment 동기화."""

from django.core.management.base import BaseCommand

from apps.counseling.models import Case
from apps.counseling.services import sync_orphan_session_requests
from apps.scheduling.models import Appointment, AppointmentStatus


class Command(BaseCommand):
    help = (
        "화면에 '예약 요청 중'으로 보이지만 Appointment가 없는 회기를 "
        "PENDING 예약으로 복구합니다."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--case",
            dest="case_number",
            default="",
            help="특정 사례번호만 처리 (예: CASE-2026-0001)",
        )

    def handle(self, *args, **options):
        case_number = (options.get("case_number") or "").strip()
        cases = Case.objects.all()
        if case_number:
            cases = cases.filter(case_number=case_number)

        if not cases.exists():
            self.stderr.write(self.style.ERROR("대상 사례가 없습니다."))
            return

        created = 0
        for case in cases:
            before = Appointment.objects.filter(
                case=case, status=AppointmentStatus.PENDING
            ).count()
            sync_orphan_session_requests(case)
            after = Appointment.objects.filter(
                case=case, status=AppointmentStatus.PENDING
            ).count()
            delta = after - before
            if delta:
                created += delta
                self.stdout.write(
                    f"{case.case_number}: PENDING 예약 {delta}건 복구"
                )

        if created:
            self.stdout.write(self.style.SUCCESS(f"총 {created}건 복구 완료."))
        else:
            self.stdout.write("복구할 orphan 예약 요청이 없습니다.")
