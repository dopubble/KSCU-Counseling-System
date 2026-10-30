"""사례발표 case 연결 진단 명령 — 읽기 전용."""

from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import CounselorProfile, User, UserRole, UserStatus
from apps.counseling.models import CasePresentationPost


class AuditPresentationCaseLinksTests(TestCase):
    def test_command_reports_counts_without_changing_data(self):
        author = User.objects.create_user(
            email="audit-author@example.com",
            password="pass12345",
            name="진단작성",
            role=UserRole.COUNSELOR,
            status=UserStatus.ACTIVE,
        )
        CounselorProfile.objects.update_or_create(
            user=author, defaults={"cohort": 1, "is_approved": True}
        )
        CasePresentationPost.objects.create(
            cohort=1,
            author=author,
            case=None,
            title="미연결",
            file=SimpleUploadedFile("a.pdf", b"%PDF-1.4"),
        )
        before = CasePresentationPost.objects.count()
        out = StringIO()
        call_command("audit_presentation_case_links", stdout=out)
        body = out.getvalue()
        self.assertIn("posts_total=1", body)
        self.assertIn("posts_case_linked=0", body)
        self.assertIn("posts_case_null=1", body)
        self.assertIn("null_posts=1", body)
        self.assertIn("linked distinct Case + case=null", body)
        self.assertNotIn("포함되지 않습니다", body)
        self.assertEqual(CasePresentationPost.objects.count(), before)
        self.assertIsNone(CasePresentationPost.objects.get().case_id)
