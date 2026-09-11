from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
)
from apps.reports.views import build_cohort_confirmed_method_stats
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus


class CohortConfirmedMethodStatsTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self._seq = 0
        self.admin = User.objects.create_user(
            email="admin-stats@example.com",
            password="pass12345",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        self.counselor_c1 = self._create_counselor("c1@example.com", "상담사1기", 1)
        self.counselor_c2 = self._create_counselor("c2@example.com", "상담사2기", 2)
        self.client_user = User.objects.create_user(
            email="client-stats@example.com",
            password="pass12345",
            name="내담자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        self.http = Client()
        self.url = reverse("admin_panel:statistics")

    def _create_counselor(self, email, name, cohort):
        user = User.objects.create_user(
            email=email,
            password="pass12345",
            name=name,
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.update_or_create(
            user=user,
            defaults={"cohort": cohort, "is_approved": True},
        )
        return user

    def _create_case(self, counselor, method, case_number):
        application = CounselingApplication.objects.create(
            client=self.client_user,
            counseling_types=["개인상담"],
            reason="통계 테스트",
            counseling_method=method,
            status=ApplicationStatus.IN_PROGRESS,
        )
        return Case.objects.create(
            application=application,
            client=self.client_user,
            counselor=counselor,
            case_number=case_number,
            status=CaseStatus.ACTIVE,
            counseling_method=method,
        )

    def _create_appointment(self, counselor, case, status, minutes_offset=0):
        self._seq += 1
        return Appointment.objects.create(
            case=case,
            counselor=counselor,
            client=self.client_user,
            scheduled_at=self.now + timedelta(days=self._seq, minutes=minutes_offset),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=status,
            confirmed_at=(
                self.now if status == AppointmentStatus.CONFIRMED else None
            ),
        )

    def _row_for_cohort(self, rows, cohort):
        for row in rows:
            if row["cohort"] == cohort:
                return row
        self.fail(f"기수 {cohort} 행이 없습니다: {rows}")

    def test_confirmed_in_person_and_remote_are_counted_separately(self):
        case_in_person = self._create_case(
            self.counselor_c1, CounselingMethod.IN_PERSON, "STAT-IP-1"
        )
        case_remote = self._create_case(
            self.counselor_c1, CounselingMethod.REMOTE, "STAT-RM-1"
        )
        self._create_appointment(
            self.counselor_c1, case_in_person, AppointmentStatus.CONFIRMED
        )
        self._create_appointment(
            self.counselor_c1, case_remote, AppointmentStatus.CONFIRMED
        )

        rows, totals = build_cohort_confirmed_method_stats()
        row = self._row_for_cohort(rows, 1)
        self.assertEqual(row["in_person_count"], 1)
        self.assertEqual(row["remote_count"], 1)
        self.assertEqual(row["total_count"], 2)
        self.assertEqual(totals["in_person_count"], 1)
        self.assertEqual(totals["remote_count"], 1)
        self.assertEqual(totals["total_count"], 2)

    def test_cancelled_and_completed_are_excluded(self):
        case_in_person = self._create_case(
            self.counselor_c1, CounselingMethod.IN_PERSON, "STAT-EX-IP"
        )
        case_remote = self._create_case(
            self.counselor_c1, CounselingMethod.REMOTE, "STAT-EX-RM"
        )
        self._create_appointment(
            self.counselor_c1, case_in_person, AppointmentStatus.CANCELLED
        )
        self._create_appointment(
            self.counselor_c1, case_remote, AppointmentStatus.COMPLETED
        )
        self._create_appointment(
            self.counselor_c1, case_in_person, AppointmentStatus.PENDING
        )
        self._create_appointment(
            self.counselor_c1, case_remote, AppointmentStatus.SCHEDULED
        )

        rows, totals = build_cohort_confirmed_method_stats()
        self.assertEqual(rows, [])
        self.assertEqual(totals["in_person_count"], 0)
        self.assertEqual(totals["remote_count"], 0)
        self.assertEqual(totals["total_count"], 0)

    def test_cohorts_are_grouped_separately_and_totals_match(self):
        case_c1_ip = self._create_case(
            self.counselor_c1, CounselingMethod.IN_PERSON, "STAT-C1-IP"
        )
        case_c1_rm = self._create_case(
            self.counselor_c1, CounselingMethod.REMOTE, "STAT-C1-RM"
        )
        case_c2_ip = self._create_case(
            self.counselor_c2, CounselingMethod.IN_PERSON, "STAT-C2-IP"
        )
        case_c2_rm = self._create_case(
            self.counselor_c2, CounselingMethod.REMOTE, "STAT-C2-RM"
        )
        for _ in range(2):
            self._create_appointment(
                self.counselor_c1, case_c1_ip, AppointmentStatus.CONFIRMED
            )
        for _ in range(3):
            self._create_appointment(
                self.counselor_c1, case_c1_rm, AppointmentStatus.CONFIRMED
            )
        self._create_appointment(
            self.counselor_c2, case_c2_ip, AppointmentStatus.CONFIRMED
        )
        for _ in range(4):
            self._create_appointment(
                self.counselor_c2, case_c2_rm, AppointmentStatus.CONFIRMED
            )
        self._create_appointment(
            self.counselor_c1, case_c1_ip, AppointmentStatus.CANCELLED
        )
        self._create_appointment(
            self.counselor_c2, case_c2_rm, AppointmentStatus.COMPLETED
        )

        rows, totals = build_cohort_confirmed_method_stats()
        self.assertEqual(len(rows), 2)
        row1 = self._row_for_cohort(rows, 1)
        row2 = self._row_for_cohort(rows, 2)
        self.assertEqual(row1["in_person_count"], 2)
        self.assertEqual(row1["remote_count"], 3)
        self.assertEqual(row1["total_count"], 5)
        self.assertEqual(row1["total_count"], row1["in_person_count"] + row1["remote_count"])
        self.assertEqual(row2["in_person_count"], 1)
        self.assertEqual(row2["remote_count"], 4)
        self.assertEqual(row2["total_count"], 5)
        self.assertEqual(row2["total_count"], row2["in_person_count"] + row2["remote_count"])
        self.assertEqual(totals["in_person_count"], 3)
        self.assertEqual(totals["remote_count"], 7)
        self.assertEqual(totals["total_count"], 10)
        self.assertEqual(
            totals["total_count"],
            sum(row["total_count"] for row in rows),
        )
        self.assertEqual(
            totals["in_person_count"],
            sum(row["in_person_count"] for row in rows),
        )
        self.assertEqual(
            totals["remote_count"],
            sum(row["remote_count"] for row in rows),
        )

    def test_same_case_multiple_confirmed_appointments_count_separately(self):
        case = self._create_case(
            self.counselor_c1, CounselingMethod.IN_PERSON, "STAT-MULTI"
        )
        self._create_appointment(
            self.counselor_c1, case, AppointmentStatus.CONFIRMED
        )
        self._create_appointment(
            self.counselor_c1, case, AppointmentStatus.CONFIRMED
        )

        rows, totals = build_cohort_confirmed_method_stats()
        row = self._row_for_cohort(rows, 1)
        self.assertEqual(row["in_person_count"], 2)
        self.assertEqual(row["total_count"], 2)
        self.assertEqual(totals["total_count"], 2)

    def test_existing_type_and_status_stats_are_unchanged(self):
        extra_client = User.objects.create_user(
            email="client-extra@example.com",
            password="pass12345",
            name="추가내담자",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        CounselingApplication.objects.create(
            client=extra_client,
            counseling_types=["진로상담", "개인상담"],
            reason="기존 통계 유지 확인",
            counseling_method=CounselingMethod.IN_PERSON,
            status=ApplicationStatus.WAITING_MATCH,
        )
        case = self._create_case(
            self.counselor_c1, CounselingMethod.REMOTE, "STAT-EXISTING"
        )
        self._create_appointment(
            self.counselor_c1, case, AppointmentStatus.CONFIRMED
        )
        self._create_appointment(
            self.counselor_c1, case, AppointmentStatus.CANCELLED
        )

        self.http.login(email="admin-stats@example.com", password="pass12345")
        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 200)

        type_distribution = {
            item["counseling_type"]: item["count"]
            for item in response.context["type_distribution"]
        }
        self.assertEqual(type_distribution["개인상담"], 2)
        self.assertEqual(type_distribution["진로상담"], 1)

        status_distribution = {
            item["status"]: item["count"]
            for item in response.context["status_distribution"]
        }
        self.assertEqual(status_distribution[CaseStatus.ACTIVE], 1)

        self.assertContains(response, "상담 유형별 분포")
        self.assertContains(response, "사례 상태별 분포")
        self.assertContains(response, "기수별 대면/비대면 상담 확정 건수")
        self.assertContains(response, "1기")
        self.assertContains(response, "전체")
        self.assertContains(response, "1건")
