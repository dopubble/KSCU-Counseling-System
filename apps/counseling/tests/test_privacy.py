from django.test import SimpleTestCase

from apps.counseling.privacy import (
    mask_client_name,
    mask_client_summary_fields,
    mask_email,
    mask_phone,
)


class PrivacyMaskingTests(SimpleTestCase):
    def test_mask_client_name(self):
        self.assertEqual(mask_client_name("홍길동"), "홍*동")
        self.assertEqual(mask_client_name("김철"), "김*")
        self.assertEqual(mask_client_name("이"), "*")

    def test_mask_contact_fields(self):
        self.assertEqual(mask_phone("010-1234-5678"), "***-****-****")
        self.assertEqual(mask_email("user@example.com"), "**")

    def test_mask_client_summary_fields(self):
        masked = mask_client_summary_fields(
            {
                "client_name": "홍길동",
                "phone": "010-1234-5678",
                "email": "user@example.com",
                "gender": "남",
            }
        )
        self.assertEqual(masked["client_name"], "홍*동")
        self.assertEqual(masked["phone"], "***-****-****")
        self.assertEqual(masked["email"], "**")
        self.assertEqual(masked["gender"], "남")
