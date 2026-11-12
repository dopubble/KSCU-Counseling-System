"""Lower(email) 고유 제약. 계정 행은 합치거나 수정하지 않는다."""

import importlib
from unittest.mock import patch

from django.apps import apps
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, TestCase

from apps.accounts.email_guard import EmailAlreadyRegistered, is_email_unique_violation
from apps.accounts.models import User, UserRole, UserStatus

abort_if_email_case_duplicates = importlib.import_module(
    "apps.accounts.migrations.0018_user_email_lower_uniq"
).abort_if_email_case_duplicates

PASSWORD = "Testpass123!"
BEFORE = "0017_alter_clientprofile_birth_date_and_more"
AFTER = "0018_user_email_lower_uniq"


def _migrate_to(target: str) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([("accounts", target)])
    connection.close()


class LowerEmailConstraintTests(TestCase):
    def test_case_variants_are_one_address_at_the_database(self):
        User.objects.create_user(
            email="Test@example.com",
            password=PASSWORD,
            name="기존",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        with patch(
            "apps.accounts.email_guard.email_is_registered",
            return_value=False,
        ):
            with self.assertRaises(EmailAlreadyRegistered):
                User.objects.create_user(
                    email="TEST@EXAMPLE.COM",
                    password=PASSWORD,
                    name="변형",
                    role=UserRole.CLIENT,
                    status=UserStatus.ACTIVE,
                )
        self.assertEqual(
            list(User.objects.filter(email__iexact="test@example.com").values_list("email", flat=True)),
            ["Test@example.com"],
        )

    def test_bulk_insert_of_case_variant_hits_named_constraint(self):
        User.objects.create_user(
            email="Kept@example.com",
            password=PASSWORD,
            name="유지",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                User.objects.bulk_create(
                    [
                        User(
                            email="kept@example.com",
                            name="다른행",
                            password="!",
                            role=UserRole.CLIENT,
                            status=UserStatus.ACTIVE,
                        )
                    ]
                )
        self.assertTrue(is_email_unique_violation(caught.exception))
        self.assertEqual(User.objects.filter(email__iexact="kept@example.com").count(), 1)
        kept = User.objects.get(email="Kept@example.com")
        kept.phone = "010-0000-1111"
        kept.save()
        kept.refresh_from_db()
        self.assertEqual(kept.email, "Kept@example.com")
        self.assertEqual(kept.phone, "010-0000-1111")

    def test_guard_allows_distinct_emails(self):
        User.objects.create_user(
            email="one@example.com",
            password=PASSWORD,
            name="하나",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        User.objects.create_user(
            email="two@example.com",
            password=PASSWORD,
            name="둘",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        abort_if_email_case_duplicates(apps, None)
        self.assertEqual(User.objects.count(), 2)


class LowerEmailConstraintMigrationTests(TransactionTestCase):
    def tearDown(self):
        _migrate_to(AFTER)
        super().tearDown()

    def test_guard_stops_and_rollback_keeps_existing_emails(self):
        original = User.objects.create_user(
            email="Member@example.com",
            password=PASSWORD,
            name="회원",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        original_email = original.email

        _migrate_to(BEFORE)
        original.refresh_from_db()
        self.assertEqual(original.email, original_email)

        User.objects.bulk_create(
            [
                User(
                    email="member@example.com",
                    name="대소문자",
                    password="!",
                    role=UserRole.CLIENT,
                    status=UserStatus.ACTIVE,
                )
            ]
        )
        emails_before = set(User.objects.values_list("email", flat=True))
        with self.assertRaises(RuntimeError):
            abort_if_email_case_duplicates(apps, None)
        self.assertEqual(set(User.objects.values_list("email", flat=True)), emails_before)

        User.objects.filter(email="member@example.com").delete()
        self.assertTrue(User.objects.filter(email=original_email).exists())

        _migrate_to(AFTER)
        original.refresh_from_db()
        self.assertEqual(original.email, original_email)
        self.assertEqual(User.objects.filter(email__iexact="member@example.com").count(), 1)
