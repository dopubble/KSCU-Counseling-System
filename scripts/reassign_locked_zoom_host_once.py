"""
잠금(locked) 확정 예약 1건을 지정 Zoom 호스트로 재생성 — Railway Shell 원타임.

사용 (배포 없이도 동작 — _create_zoom_meeting_for_appointment 직접 호출):
  python manage.py shell < scripts/reassign_locked_zoom_host_once.py

아래 TARGET_* 값만 수정하세요.
"""
from django.utils import timezone

from apps.counseling.models import CounselingMethod
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import _create_zoom_meeting_for_appointment
from apps.scheduling.utils import delete_zoom_meeting
from apps.scheduling.zoom_hosts import email_for_host_id, host_id_for_email

# === 수정 구간 ===
CLIENT_NAME = "구현정"
SESSION_NUMBER = 2
SCHEDULED_LABEL = "2026-07-08 10:00"  # KST
TARGET_HOST_ID = "host_02"
NOTIFY_LINK_CHANGE = False
DRY_RUN = True  # False 로 바꾸면 실제 반영
# === 수정 구간 끝 ===

target_email = email_for_host_id(TARGET_HOST_ID)
if not target_email:
    raise SystemExit(f"호스트 ID 오류: {TARGET_HOST_ID}")

apt = (
    Appointment.objects.filter(
        client__name=CLIENT_NAME,
        session_number=SESSION_NUMBER,
        status=AppointmentStatus.CONFIRMED,
        case__counseling_method=CounselingMethod.REMOTE,
    )
    .select_related("client", "counselor", "zoom_meeting")
    .order_by("-scheduled_at")
    .first()
)
if not apt:
    raise SystemExit(f"예약 없음: {CLIENT_NAME} {SESSION_NUMBER}회차")

label = timezone.localtime(apt.scheduled_at).strftime("%Y-%m-%d %H:%M")
if label != SCHEDULED_LABEL:
    raise SystemExit(f"일시 불일치: DB={label}, 기대={SCHEDULED_LABEL}")

zoom = getattr(apt, "zoom_meeting", None)
old_host = (zoom.zoom_host_email or "").strip() if zoom else ""
old_url = (zoom.join_url or "").strip() if zoom else ""
old_meeting_id = (zoom.zoom_meeting_id or "").strip() if zoom else ""

print("=== 대상 ===")
print(f"  {apt.client.name} s{apt.session_number} {label}")
print(f"  counselor={apt.counselor.name if apt.counselor else '-'}")
print(f"  현재 host={host_id_for_email(old_host) or '-'} ({old_host})")
print(f"  목표 host={TARGET_HOST_ID} ({target_email})")
print(f"  join_url={old_url or '(empty)'}")

if old_host.lower() == target_email.lower():
    print("이미 목표 호스트입니다. 종료.")
    raise SystemExit(0)

if DRY_RUN:
    print("\nDRY RUN — DRY_RUN=False 로 바꾼 뒤 다시 실행하세요.")
    raise SystemExit(0)

_create_zoom_meeting_for_appointment(
    apt,
    host_user_email=target_email,
    notify_link_change=NOTIFY_LINK_CHANGE,
)
apt.refresh_from_db()
new_zoom = getattr(apt, "zoom_meeting", None)
new_id = (new_zoom.zoom_meeting_id or "").strip() if new_zoom else ""
if old_meeting_id and new_id and old_meeting_id != new_id:
    delete_zoom_meeting(old_meeting_id)

print("\n=== 완료 ===")
print(f"  host={host_id_for_email(new_zoom.zoom_host_email)} ({new_zoom.zoom_host_email})")
print(f"  meeting_id={new_zoom.zoom_meeting_id}")
print(f"  join_url={new_zoom.join_url}")
