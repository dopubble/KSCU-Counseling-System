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
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.reports.views import build_cohort_confirmed_method_stats
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus


class EnrollmentCohortStatsTests(TestCase):
    """확정 상담 건수는 사례 Enrollment 기수로 집계한다."""

    def setUp(self):
        self.now = timezone.now()
        self._seq = 0
        self.admin = User.objects.create_user(
            email="admin-enroll-stats@example.com",
            password="pass12345",
            name="관리자",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        self.counselor = self._create_counselor("multi@example.com", "홍길동", 2)
        self.legacy = self._create_counselor("legacy@example.com", "단일상담", 7)
        self.matched = self._create_counselor("matched@example.com", "일치상담", 4)
        self.first = TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=1,
            status=TrainingEnrollmentStatus.COMPLETED,
        )
        self.second = TrainingEnrollment.objects.create(
            counselor=self.counselor,
            cohort=2,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        TrainingEnrollment.objects.create(
            counselor=self.matched,
            cohort=4,
            status=TrainingEnrollmentStatus.ACTIVE,
        )
        self.client_one = self._create_client("client-one@example.com", "내담1기")
        self.client_two = self._create_client("client-two@example.com", "내담2기")
        self.client_unknown = self._create_client("client-unknown@example.com", "내담미정")
        self.client_legacy = self._create_client("client-legacy@example.com", "내담레거시")
        self.client_matched = self._create_client("client-matched@example.com", "내담단일")
        self.case_one = self._create_case(
            self.counselor,
            self.client_one,
            CounselingMethod.IN_PERSON,
            "ENR-1",
            self.first,
        )
        self.case_two = self._create_case(
            self.counselor,
            self.client_two,
            CounselingMethod.REMOTE,
            "ENR-2",
            self.second,
        )
        self.case_unknown = self._create_case(
            self.counselor,
            self.client_unknown,
            CounselingMethod.IN_PERSON,
            "ENR-U",
            None,
        )
        self.case_legacy = self._create_case(
            self.legacy,
            self.client_legacy,
            CounselingMethod.REMOTE,
            "ENR-L",
            None,
        )
        self.case_matched = self._create_case(
            self.matched,
            self.client_matched,
            CounselingMethod.IN_PERSON,
            "ENR-M",
            None,
        )
        self.http = Client()

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

    def _create_client(self, email, name):
        return User.objects.create_user(
            email=email,
            password="pass12345",
            name=name,
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )

    def _create_case(self, counselor, client, method, case_number, enrollment):
        application = CounselingApplication.objects.create(
            client=client,
            counseling_types=["개인상담"],
            reason="기수 통계",
            counseling_method=method,
            status=ApplicationStatus.IN_PROGRESS,
        )
        return Case.objects.create(
            application=application,
            client=client,
            counselor=counselor,
            training_enrollment=enrollment,
            case_number=case_number,
            status=CaseStatus.ACTIVE,
            counseling_method=method,
        )

    def _create_appointment(self, counselor, client, case, status):
        self._seq += 1
        return Appointment.objects.create(
            case=case,
            counselor=counselor,
            client=client,
            scheduled_at=self.now + timedelta(days=self._seq),
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=status,
            confirmed_at=self.now if status == AppointmentStatus.CONFIRMED else None,
        )

    def _row(self, rows, cohort):
        for row in rows:
            if row["cohort"] == cohort:
                return row
        self.fail(f"기수 {cohort} 행이 없습니다: {rows}")

    def test_confirmed_counts_follow_case_enrollment_not_profile(self):
        self._create_appointment(
            self.counselor, self.client_one, self.case_one, AppointmentStatus.CONFIRMED
        )
        self._create_appointment(
            self.counselor, self.client_one, self.case_one, AppointmentStatus.CONFIRMED
        )
        self._create_appointment(
            self.counselor, self.client_one, self.case_one, AppointmentStatus.CANCELLED
        )
        self._create_appointment(
            self.counselor, self.client_one, self.case_one, AppointmentStatus.COMPLETED
        )
        self._create_appointment(
            self.counselor, self.client_two, self.case_two, AppointmentStatus.CONFIRMED
        )
        self._create_appointment(
            self.counselor, self.client_unknown, self.case_unknown, AppointmentStatus.CONFIRMED
        )
        self._create_appointment(
            self.legacy, self.client_legacy, self.case_legacy, AppointmentStatus.CONFIRMED
        )
        self._create_appointment(
            self.matched, self.client_matched, self.case_matched, AppointmentStatus.CONFIRMED
        )

        rows, totals = build_cohort_confirmed_method_stats()
        cohort_one = self._row(rows, 1)
        cohort_two = self._row(rows, 2)
        self.assertEqual(cohort_one["in_person_count"], 2)
        self.assertEqual(cohort_one["remote_count"], 0)
        self.assertEqual(cohort_one["total_count"], 2)
        self.assertEqual(cohort_two["in_person_count"], 0)
        self.assertEqual(cohort_two["remote_count"], 1)
        self.assertEqual(cohort_two["total_count"], 1)
        self.assertEqual(self._row(rows, 7)["total_count"], 1)
        self.assertEqual(self._row(rows, 4)["total_count"], 1)
        unknown = self._row(rows, None)
        self.assertEqual(unknown["total_count"], 1)
        confirmed = Appointment.objects.filter(status=AppointmentStatus.CONFIRMED).count()
        self.assertEqual(totals["total_count"], confirmed)
        self.assertEqual(sum(row["total_count"] for row in rows), confirmed)
        self.assertEqual(len({row["cohort"] for row in rows}), len(rows))

        self.http.login(email="admin-enroll-stats@example.com", password="pass12345")
        response = self.http.get(reverse("admin_panel:statistics"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1기")
        self.assertContains(response, "기수 미정")
        self.assertContains(response, "상담 유형별")

    def test_three_and_two_confirmed_appointments_stay_on_case_cohort(self):
        for _ in range(3):
            self._create_appointment(
                self.counselor, self.client_one, self.case_one, AppointmentStatus.CONFIRMED
            )
        for _ in range(2):
            self._create_appointment(
                self.counselor, self.client_two, self.case_two, AppointmentStatus.CONFIRMED
            )
        self._create_appointment(
            self.counselor, self.client_one, self.case_one, AppointmentStatus.CANCELLED
        )
        self._create_appointment(
            self.counselor, self.client_one, self.case_one, AppointmentStatus.COMPLETED
        )
        self._create_appointment(
            self.counselor, self.client_two, self.case_two, AppointmentStatus.PENDING
        )
        self._create_appointment(
            self.counselor, self.client_two, self.case_two, AppointmentStatus.SCHEDULED
        )

        rows, totals = build_cohort_confirmed_method_stats()
        self.assertEqual(self._row(rows, 1)["total_count"], 3)
        self.assertEqual(self._row(rows, 1)["in_person_count"], 3)
        self.assertEqual(self._row(rows, 2)["total_count"], 2)
        self.assertEqual(self._row(rows, 2)["remote_count"], 2)
        self.assertEqual(totals["total_count"], 5)
        self.assertEqual(sum(row["total_count"] for row in rows), 5)

    def test_counselor_roster_stays_on_current_profile_cohort(self):
        self.http.login(email="admin-enroll-stats@example.com", password="pass12345")
        url = reverse("admin_panel:counselor_list")
        current = self.http.get(url, {"cohort": "2"})
        self.assertContains(current, "홍길동")
        past = self.http.get(url, {"cohort": "1"})
        self.assertNotContains(past, "홍길동")

    def test_non_admin_cannot_open_statistics(self):
        self.http.login(email="multi@example.com", password="pass12345")
        response = self.http.get(reverse("admin_panel:statistics"))
        self.assertEqual(response.status_code, 403)
