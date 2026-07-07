"""
CASE-2026-0005 / 2026-07-07 21:00 KST — 외부(hakyss) Zoom 수동 고정

Railway Shell:
  /opt/venv/bin/python manage.py shell

전체 복사 후 DRY_RUN=True 로 1회, 확인 후 False 로 재실행.

주의: 상담사·내담자 UI는 join_url(/j/)만 사용. start_url은 DB 보관용.
    상담사 호스트 권한은 Claim Host + counselor_host_key(또는 ZOOM_HOST_KEY).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction

from apps.counseling.models import CounselingMethod
from apps.scheduling.availability import format_local_datetime
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.zoom_links import (
    appointment_counselor_host_key,
    appointment_zoom_link_is_locked,
    resolve_appointment_zoom_counselor_url,
    resolve_appointment_zoom_join_url,
    sync_case_zoom_meeting_url,
)
from apps.sessions_app.models import ZoomMeeting

# ========== 대상 (CASE-2026-0005, 7/7 21:00) ==========
CASE_NUMBER = "CASE-2026-0005"
CLIENT_NAME = "성순의"  # 내담자 — DB 이름과 다르면 수정
SCHEDULED_KST = "2026-07-07 21:00"
SESSION_NUMBER = None  # 21:00이 1건이면 None

NEW_JOIN_URL = (
    "https://us06web.zoom.us/j/85999149111"
    "?pwd=npVXvEJ5BYzMmGgnbKaUJvZKT3I9SV.1"
)
NEW_MEETING_ID = "85999149111"
NEW_ZOOM_HOST_EMAIL = "hakyss@mail.kcu.ac"
# hakyss Zoom 웹 → 해당 회의 → 「시작」링크 복사 (로그인 상태). 없으면 아래 기본값 사용.
NEW_START_URL = "https://us06web.zoom.us/s/85999149111"
COUNSELOR_HOST_KEY = "877273"

DRY_RUN = True
# =====================================================

KST = ZoneInfo("Asia/Seoul")
target_local = datetime.strptime(SCHEDULED_KST, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
target_utc = target_local.astimezone(ZoneInfo("UTC"))
window = timedelta(minutes=15)

filters = {
    "case__case_number": CASE_NUMBER,
    "client__name": CLIENT_NAME,
    "status": AppointmentStatus.CONFIRMED,
    "case__counseling_method": CounselingMethod.REMOTE,
    "scheduled_at__gte": target_utc - window,
    "scheduled_at__lte": target_utc + window,
}
if SESSION_NUMBER is not None:
    filters["session_number"] = SESSION_NUMBER

candidates = list(
    Appointment.objects.filter(**filters)
    .select_related("case", "client", "counselor", "zoom_meeting")
    .order_by("session_number")
)

print(f"=== 후보 {len(candidates)}건 ===")
for apt in candidates:
    zm = getattr(apt, "zoom_meeting", None)
    print("-" * 50)
    print(f"  id={apt.pk} 회차={apt.session_number}")
    print(f"  상담사={apt.counselor.name} 내담자={apt.client.name}")
    print(f"  KST={format_local_datetime(apt.scheduled_at)}")
    if zm:
        print(f"  join={zm.join_url[:70]}...")
        print(f"  start={zm.start_url[:70] if zm.start_url else '(없음)'}")

if len(candidates) != 1:
    raise SystemExit("STOP: 후보 1건이 아닙니다. CLIENT_NAME / SESSION_NUMBER 확인.")

apt = candidates[0]
case = apt.case

if DRY_RUN:
    print("\n[DRY_RUN] 적용 예정:")
    print(f"  join_url         → {NEW_JOIN_URL[:80]}...")
    print(f"  start_url        → {NEW_START_URL}")
    print(f"  counselor_host_key → {COUNSELOR_HOST_KEY}")
    print(f"  zoom_host_email  → {NEW_ZOOM_HOST_EMAIL}")
    print("\nDRY_RUN=False 로 재실행하세요.")
    raise SystemExit(0)

with transaction.atomic():
    ZoomMeeting.objects.update_or_create(
        appointment=apt,
        defaults={
            "zoom_meeting_id": NEW_MEETING_ID,
            "join_url": NEW_JOIN_URL,
            "start_url": NEW_START_URL,
            "password": "",
            "zoom_host_email": NEW_ZOOM_HOST_EMAIL,
            "counselor_host_key": COUNSELOR_HOST_KEY,
        },
    )
    sync_case_zoom_meeting_url(apt, join_url=NEW_JOIN_URL)

apt.refresh_from_db()
case.refresh_from_db()
zm = apt.zoom_meeting

print("\n=== 적용 완료 ===")
print(f"  locked           : {appointment_zoom_link_is_locked(apt)}")
print(f"  내담자 join URL  : {resolve_appointment_zoom_join_url(apt, case)[:80]}...")
print(f"  상담사 입장 URL(join): {resolve_appointment_zoom_join_url(apt, case)[:80]}...")
print(f"  호스트 키(회기)  : {appointment_counselor_host_key(apt)}")
print("OK")
