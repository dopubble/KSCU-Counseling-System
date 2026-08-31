"""상담일지 상담 구분 스냅샷 · 회기별 예약 일시 기본값."""

import importlib
from datetime import timedelta

from django.apps import apps as global_apps
from django.test import Client as HttpClient, TestCase
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
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import CounselingJournal

DATETIME_LOCAL_FORMAT = "%Y-%m-%dT%H:%M"


def _create_client(name: str) -> User:
    return User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.CLIENT,
        status=UserStatus.ACTIVE,
    )


def _create_counselor(name: str) -> User:
    user = User.objects.create_user(
        email=f"{name}@example.com",
        password="pass12345",
        name=name,
        role=UserRole.COUNSELOR,
        status=UserStatus.ACTIVE,
    )
    CounselorProfile.objects.update_or_create(
        user=user,
        defaults={"cohort": 1, "is_approved": True},
    )
    return user


def _create_case(counselor, client, case_number, counseling_types) -> Case:
    application = CounselingApplication.objects.create(
        client=client,
        counseling_types=counseling_types,
        reason="test",
        counseling_method=CounselingMethod.IN_PERSON,
        status=ApplicationStatus.IN_PROGRESS,
    )
    return Case.objects.create(
        application=application,
        client=client,
        counselor=counselor,
        case_number=case_number,
        status=CaseStatus.ACTIVE,
        counseling_method=CounselingMethod.IN_PERSON,
        total_sessions=8,
        remaining_sessions=8,
    )


def _create_appointment(case, session_number, scheduled_at) -> Appointment:
    return Appointment.objects.create(
        case=case,
        counselor=case.counselor,
        client=case.client,
        scheduled_at=scheduled_at,
        status=AppointmentStatus.CONFIRMED,
        session_number=session_number,
    )


def _journal_payload(**overrides) -> dict:
    payload = {
        "session_number": 1,
        "session_datetime": timezone.localtime().strftime(DATETIME_LOCAL_FORMAT),
        "counseling_content": "내용",
        "counselor_observation": "관찰",
        "clinical_assessment": "평가",
        "follow_up_plan": "계획",
    }
    payload.update(overrides)
    return payload


class JournalSessionCategorySnapshotTests(TestCase):
    """상담 구분 — 신청 정보 전체를 서버 측에서 스냅샷 저장."""

    def setUp(self):
        self.counselor = _create_counselor("상담사구분")
        self.http = HttpClient()
        self.http.force_login(self.counselor)
        self.base_at = timezone.localtime().replace(
            hour=13, minute=0, second=0, microsecond=0
        ) + timedelta(days=2)

    def _open_create(self, case, session_number=1):
        url = reverse("counselor:journal_create", kwargs={"pk": case.pk})
        return self.http.get(f"{url}?session={session_number}")

    def _post_create(self, case, **overrides):
        return self.http.post(
            reverse("counselor:journal_create", kwargs={"pk": case.pk}),
            _journal_payload(**overrides),
        )

    def test_single_category_is_displayed_and_saved_as_list(self):
        case = _create_case(
            self.counselor, _create_client("내담자단일"), "CASE-SC-1", ["진로상담"]
        )
        response = self._open_create(case)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["session_categories"], ["진로상담"])
        self.assertContains(response, "진로상담")

        self.assertEqual(self._post_create(case).status_code, 302)
        journal = CounselingJournal.objects.get(case=case, session_number=1)
        self.assertEqual(journal.session_categories, ["진로상담"])
        self.assertEqual(journal.session_category_display, "진로상담")

    def test_multiple_categories_are_all_displayed_and_saved(self):
        case = _create_case(
            self.counselor,
            _create_client("내담자복수"),
            "CASE-SC-2",
            ["진로상담", "대인관계", "부부관계"],
        )
        response = self._open_create(case)
        self.assertEqual(
            response.context["session_categories"],
            ["진로상담", "대인관계", "부부관계"],
        )
        for category in ("진로상담", "대인관계", "부부관계"):
            self.assertContains(response, category)

        self.assertEqual(self._post_create(case).status_code, 302)
        journal = CounselingJournal.objects.get(case=case, session_number=1)
        self.assertEqual(
            journal.session_categories, ["진로상담", "대인관계", "부부관계"]
        )
        self.assertEqual(
            journal.session_category_display, "진로상담, 대인관계, 부부관계"
        )

    def test_missing_category_saves_empty_list_and_shows_placeholder(self):
        case = _create_case(
            self.counselor, _create_client("내담자구분없음"), "CASE-SC-3", []
        )
        response = self._open_create(case)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["session_categories"], [])
        self.assertContains(response, "—")

        self.assertEqual(self._post_create(case).status_code, 302)
        journal = CounselingJournal.objects.get(case=case, session_number=1)
        self.assertEqual(journal.session_categories, [])
        self.assertEqual(journal.session_category, "")
        self.assertEqual(journal.session_category_display, "")

    def test_tampered_post_cannot_change_category(self):
        case = _create_case(
            self.counselor, _create_client("내담자변조"), "CASE-SC-4", ["대인관계"]
        )
        response = self._post_create(
            case,
            session_category="위기개입",
            session_categories='["위기개입"]',
        )
        self.assertEqual(response.status_code, 302)
        journal = CounselingJournal.objects.get(case=case, session_number=1)
        self.assertEqual(journal.session_categories, ["대인관계"])
        self.assertEqual(journal.session_category, "대인관계")

    def test_application_change_after_save_does_not_alter_journal(self):
        case = _create_case(
            self.counselor, _create_client("내담자변경"), "CASE-SC-5", ["자녀관계"]
        )
        self.assertEqual(self._post_create(case).status_code, 302)

        application = case.application
        application.counseling_types = ["부부관계", "개인성격"]
        application.save(update_fields=["counseling_types"])

        journal = CounselingJournal.objects.get(case=case, session_number=1)
        self.assertEqual(journal.session_categories, ["자녀관계"])

        response = self.http.get(
            reverse(
                "counselor:journal_edit",
                kwargs={"pk": case.pk, "session_number": 1},
            )
        )
        self.assertEqual(response.context["session_categories"], ["자녀관계"])

    def test_legacy_single_value_journal_displays_via_fallback(self):
        case = _create_case(
            self.counselor, _create_client("내담자레거시"), "CASE-SC-6", ["진로상담"]
        )
        journal = CounselingJournal.objects.create(
            case=case,
            counselor=self.counselor,
            session_number=1,
            session_category="개인상담",
            session_categories=[],
            session_datetime=self.base_at,
            subjective="S",
            objective="O",
            assessment="A",
            plan="P",
            is_draft=False,
        )
        self.assertEqual(journal.session_category_list, ["개인상담"])
        self.assertEqual(journal.session_category_display, "개인상담")

        response = self.http.get(
            reverse(
                "counselor:journal_detail",
                kwargs={"pk": case.pk, "session_number": 1},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "개인상담")

        edit = self.http.get(
            reverse(
                "counselor:journal_edit",
                kwargs={"pk": case.pk, "session_number": 1},
            )
        )
        self.assertEqual(edit.context["session_categories"], ["개인상담"])

    def test_backfill_migration_converts_single_value(self):
        case = _create_case(
            self.counselor, _create_client("내담자이관"), "CASE-SC-7", ["진로상담"]
        )
        legacy = CounselingJournal.objects.create(
            case=case,
            counselor=self.counselor,
            session_number=1,
            session_category="개인성격",
            session_categories=[],
            is_draft=False,
        )
        already_migrated = CounselingJournal.objects.create(
            case=case,
            counselor=self.counselor,
            session_number=2,
            session_category="진로상담",
            session_categories=["진로상담", "대인관계"],
            is_draft=False,
        )
        migration = importlib.import_module(
            "apps.sessions_app.migrations.0010_backfill_journal_session_categories"
        )
        migration.forwards(global_apps, None)

        legacy.refresh_from_db()
        already_migrated.refresh_from_db()
        self.assertEqual(legacy.session_categories, ["개인성격"])
        self.assertEqual(
            already_migrated.session_categories, ["진로상담", "대인관계"]
        )


class JournalSessionDatetimeTests(TestCase):
    """상담 일시 — 회기별 예약 기본값과 상담사 입력값 보호."""

    def setUp(self):
        self.counselor = _create_counselor("상담사일시")
        self.client_user = _create_client("내담자일시")
        self.case = _create_case(
            self.counselor, self.client_user, "CASE-SD-1", ["진로상담"]
        )
        self.scheduled_at = timezone.localtime().replace(
            hour=14, minute=30, second=0, microsecond=0
        ) + timedelta(days=3)
        _create_appointment(self.case, 1, self.scheduled_at)
        self.http = HttpClient()
        self.http.force_login(self.counselor)

    def _create_url(self, session_number=1):
        url = reverse("counselor:journal_create", kwargs={"pk": self.case.pk})
        return f"{url}?session={session_number}"

    def _local(self, value):
        return timezone.localtime(value).strftime(DATETIME_LOCAL_FORMAT)

    def test_new_journal_prefills_session_appointment_datetime(self):
        response = self.http.get(self._create_url(1))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(
            form["session_datetime"].value().strftime(DATETIME_LOCAL_FORMAT),
            self._local(self.scheduled_at),
        )
        self.assertContains(response, self._local(self.scheduled_at))

    def test_new_journal_without_appointment_keeps_datetime_empty(self):
        case = _create_case(
            self.counselor, _create_client("내담자예약없음"), "CASE-SD-2", ["대인관계"]
        )
        url = reverse("counselor:journal_create", kwargs={"pk": case.pk})
        response = self.http.get(f"{url}?session=1")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["form"]["session_datetime"].value())

    def test_new_journal_uses_appointment_of_requested_session_only(self):
        session2_at = self.scheduled_at + timedelta(days=7)
        _create_appointment(self.case, 2, session2_at)
        response = self.http.get(self._create_url(2))
        self.assertEqual(
            response.context["form"]["session_datetime"]
            .value()
            .strftime(DATETIME_LOCAL_FORMAT),
            self._local(session2_at),
        )

    def test_new_journal_ignores_other_case_appointment(self):
        other_case = _create_case(
            self.counselor, _create_client("내담자다른사례"), "CASE-SD-3", ["부부관계"]
        )
        _create_appointment(other_case, 1, self.scheduled_at + timedelta(days=1))
        response = self.http.get(self._create_url(1))
        self.assertEqual(
            response.context["form"]["session_datetime"]
            .value()
            .strftime(DATETIME_LOCAL_FORMAT),
            self._local(self.scheduled_at),
        )

    def test_counselor_can_override_datetime_on_save(self):
        chosen_at = timezone.localtime(self.scheduled_at) + timedelta(days=1, hours=2)
        response = self.http.post(
            reverse("counselor:journal_create", kwargs={"pk": self.case.pk}),
            _journal_payload(
                session_datetime=chosen_at.strftime(DATETIME_LOCAL_FORMAT)
            ),
        )
        self.assertEqual(response.status_code, 302)
        journal = CounselingJournal.objects.get(case=self.case, session_number=1)
        self.assertEqual(
            self._local(journal.session_datetime),
            chosen_at.strftime(DATETIME_LOCAL_FORMAT),
        )

    def test_invalid_post_keeps_submitted_datetime(self):
        chosen_at = timezone.localtime(self.scheduled_at) + timedelta(days=2)
        response = self.http.post(
            reverse("counselor:journal_create", kwargs={"pk": self.case.pk}),
            _journal_payload(
                session_datetime=chosen_at.strftime(DATETIME_LOCAL_FORMAT),
                counseling_content="",
            ),
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertTrue(form.errors)
        self.assertEqual(
            form["session_datetime"].value(),
            chosen_at.strftime(DATETIME_LOCAL_FORMAT),
        )
        self.assertEqual(response.context["session_categories"], ["진로상담"])


class JournalEditTests(TestCase):
    """수정 화면 — 저장된 상담 구분·일시 유지."""

    def setUp(self):
        self.counselor = _create_counselor("상담사수정")
        self.client_user = _create_client("내담자수정")
        self.case = _create_case(
            self.counselor, self.client_user, "CASE-JE-1", ["진로상담"]
        )
        self.scheduled_at = timezone.localtime().replace(
            hour=10, minute=0, second=0, microsecond=0
        ) + timedelta(days=5)
        _create_appointment(self.case, 1, self.scheduled_at)
        self.saved_datetime = timezone.localtime().replace(
            hour=9, minute=15, second=0, microsecond=0
        ) - timedelta(days=10)
        self.journal = CounselingJournal.objects.create(
            case=self.case,
            counselor=self.counselor,
            session_number=1,
            session_category="위기개입",
            session_categories=["위기개입", "대인관계"],
            session_datetime=self.saved_datetime,
            subjective="S",
            objective="O",
            assessment="A",
            plan="P",
            is_draft=False,
        )
        self.http = HttpClient()
        self.http.force_login(self.counselor)

    def _edit_url(self):
        return reverse(
            "counselor:journal_edit",
            kwargs={"pk": self.case.pk, "session_number": 1},
        )

    def test_edit_keeps_saved_categories_and_datetime(self):
        response = self.http.get(self._edit_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["session_categories"], ["위기개입", "대인관계"]
        )
        self.assertEqual(
            response.context["form"]["session_datetime"]
            .value()
            .strftime(DATETIME_LOCAL_FORMAT),
            timezone.localtime(self.saved_datetime).strftime(DATETIME_LOCAL_FORMAT),
        )
        self.assertNotContains(
            response,
            timezone.localtime(self.scheduled_at).strftime(DATETIME_LOCAL_FORMAT),
        )

    def test_edit_save_does_not_overwrite_category_snapshot(self):
        new_datetime = timezone.localtime(self.saved_datetime) + timedelta(hours=3)
        response = self.http.post(
            self._edit_url(),
            _journal_payload(
                session_datetime=new_datetime.strftime(DATETIME_LOCAL_FORMAT),
                session_category="진로상담",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.journal.refresh_from_db()
        self.assertEqual(self.journal.session_categories, ["위기개입", "대인관계"])
        self.assertEqual(self.journal.session_category, "위기개입")
        self.assertEqual(
            timezone.localtime(self.journal.session_datetime).strftime(
                DATETIME_LOCAL_FORMAT
            ),
            new_datetime.strftime(DATETIME_LOCAL_FORMAT),
        )
