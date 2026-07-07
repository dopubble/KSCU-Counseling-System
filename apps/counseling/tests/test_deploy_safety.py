from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class CheckDeploySafetyTests(SimpleTestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_skips_off_railway(self):
        out = StringIO()
        call_command("check_deploy_safety", stdout=out)
        self.assertIn("건너뜁니다", out.getvalue())

    @override_settings(MEDIA_STORAGE_MODE="ephemeral", MEDIA_ROOT="/app/media")
    @patch.dict(
        "os.environ",
        {"RAILWAY_ENVIRONMENT": "production"},
        clear=True,
    )
    def test_blocks_ephemeral_on_railway(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("check_deploy_safety")
        self.assertIn("MEDIA_ROOT", str(ctx.exception))

    @override_settings(MEDIA_STORAGE_MODE="volume", MEDIA_ROOT="/data/media")
    @patch.dict(
        "os.environ",
        {"RAILWAY_ENVIRONMENT": "production"},
        clear=True,
    )
    def test_allows_volume_on_railway(self):
        out = StringIO()
        call_command("check_deploy_safety", stdout=out)
        self.assertIn("check_deploy_safety: ok", out.getvalue())
