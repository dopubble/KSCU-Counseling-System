"""상담사 대시보드 필수서식 다운로드."""

import hashlib
from urllib.parse import quote

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.essential_forms import ESSENTIAL_FORMS, get_essential_form


def _user(email, name, role, *, status=UserStatus.ACTIVE):
    return User.objects.create_user(
        email=email,
        password="pass12345",
        name=name,
        role=role,
        status=status,
    )


class EssentialFormTests(TestCase):
    def setUp(self):
        self.counselor = _user(
            "forms-counselor@example.com", "서식상담사", UserRole.COUNSELOR
        )
        self.client_user = _user(
            "forms-client@example.com", "서식내담자", UserRole.CLIENT
        )
        self.admin = _user("forms-admin@example.com", "서식관리자", UserRole.ADMIN)
        self.supervisor = _user(
            "forms-supervisor@example.com", "서식수퍼바이저", UserRole.SUPERVISOR
        )

    def test_dashboard_shows_form_card_without_changing_existing_sections(self):
        http = Client()
        http.force_login(self.counselor)
        response = http.get(reverse("counselor:dashboard"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        start = html.index('id="counselor-essential-forms"')
        end = html.index('id="counselor-active-cases"')
        card = html[start:end]
        self.assertIn("상담 필수서식", card)
        self.assertIn("상담 진행에 필요한 서류를 다운로드할 수 있습니다.", card)
        self.assertNotIn("target=", card)
        for form in ESSENTIAL_FORMS:
            self.assertIn(form["label"], card)
            self.assertIn(
                reverse("counselor:essential_form_download", args=[form["key"]]),
                card,
            )
            self.assertIn(f'download="{form["download_name"]}"', card)
        self.assertIn("활성 사례", html)
        self.assertIn("수련 현황", html)
        self.assertIn("담당 활성 사례", html)
        self.assertLess(html.index("수련 현황"), start)
        self.assertLess(start, html.index("담당 활성 사례"))

    def test_counselor_download_matches_original_hwp(self):
        http = Client()
        http.force_login(self.counselor)
        for form in ESSENTIAL_FORMS:
            with self.subTest(form=form["key"]):
                response = http.get(
                    reverse("counselor:essential_form_download", args=[form["key"]])
                )
                self.assertEqual(response.status_code, 200)
                body = b"".join(response.streaming_content)
                original = get_essential_form(form["key"])["path"].read_bytes()
                self.assertEqual(body, original)
                self.assertEqual(
                    hashlib.sha256(body).hexdigest(),
                    hashlib.sha256(original).hexdigest(),
                )
                disposition = response["Content-Disposition"]
                self.assertIn("attachment", disposition)
                self.assertIn(quote(form["download_name"]), disposition)
                self.assertEqual(
                    response["Content-Type"], "application/octet-stream"
                )

    def test_admin_can_download(self):
        http = Client()
        http.force_login(self.admin)
        response = http.get(
            reverse("counselor:essential_form_download", args=["consent"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])

    def test_anonymous_client_and_supervisor_cannot_download(self):
        url = reverse("counselor:essential_form_download", args=["privacy"])
        anonymous = Client().get(url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("accounts:login"), anonymous["Location"])

        http = Client()
        http.force_login(self.client_user)
        self.assertEqual(http.get(url).status_code, 403)

        http.force_login(self.supervisor)
        self.assertEqual(http.get(url).status_code, 403)

    def test_unknown_form_and_post_are_rejected(self):
        http = Client()
        http.force_login(self.counselor)
        missing = http.get(
            reverse("counselor:essential_form_download", args=["missing"])
        )
        self.assertEqual(missing.status_code, 404)
        posted = http.post(
            reverse("counselor:essential_form_download", args=["intake"])
        )
        self.assertEqual(posted.status_code, 405)

    def test_pending_counselor_cannot_download(self):
        pending = _user(
            "forms-pending@example.com",
            "대기상담사",
            UserRole.COUNSELOR,
            status=UserStatus.PENDING,
        )
        http = Client()
        http.force_login(pending)
        response = http.get(
            reverse("counselor:essential_form_download", args=["intake"])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
