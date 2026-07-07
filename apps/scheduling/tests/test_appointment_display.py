"""Admin·모델 표시용 KST 변환 — DB 값은 변경하지 않음."""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, override_settings

from apps.scheduling.availability import format_local_datetime


@override_settings(TIME_ZONE="Asia/Seoul", USE_TZ=True)
class AppointmentDisplayTests(SimpleTestCase):
    def test_format_local_datetime_converts_utc_to_kst_wall_clock(self):
        # 01:00 UTC = 10:00 KST (Admin에서 UTC strftime 시 05:00으로 보이던 케이스)
        utc = datetime(2026, 7, 7, 1, 0, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(format_local_datetime(utc), "2026-07-07 10:00")

    def test_format_local_datetime_none(self):
        self.assertEqual(format_local_datetime(None), "—")
