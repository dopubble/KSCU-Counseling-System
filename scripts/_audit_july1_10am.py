"""7/1 10:00 슬롯 전수 감사 — Railway: python manage.py shell < scripts/_audit_july1_10am.py"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.reports.appointment_calendar import build_calendar_events, parse_calendar_bound
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import ZoomMeeting

KST = ZoneInfo("Asia/Seoul")
SLOT_START = timezone.make_aware(datetime(2026, 7, 1, 9, 30), KST)
SLOT_END = timezone.make_aware(datetime(2026, 7, 1, 10, 30), KST)

JULY1_TARGETS = [
    ("이현옥", "gusdhrl@empas.com", "신영화", 3),
    ("김수미", "glory921@hanmail.net", "성소미", 2),
    ("구현정", "kookoo162@daum.net", "양은영", 1),
]

User = get_user_model()
apt_ct = ContentType.objects.get_for_model(Appointment)

print("=" * 60)
print("A. 7/1 09:30~10:30 — ALL appointments (any status)")
print("=" * 60)
slot_qs = (
    Appointment.objects.filter(scheduled_at__gte=SLOT_START, scheduled_at__lt=SLOT_END)
    .select_related("client", "counselor", "case")
    .order_by("scheduled_at")
)
for a in slot_qs:
    zm = ZoomMeeting.objects.filter(appointment=a).first()
    join_preview = (zm.join_url[:50] + "...") if zm and zm.join_url else "-"
    print(
        f"  {timezone.localtime(a.scheduled_at):%H:%M} | {a.client.name} s{a.session_number} | "
        f"{a.status:14} | {a.counselor.name} | id={a.pk}\n"
        f"    zoom_host={(zm.zoom_host_email if zm else '-')} join={join_preview}"
    )
print(f"  total={slot_qs.count()}")

print("\n" + "=" * 60)
print("B. Target trio — session-specific lookup")
print("=" * 60)
for name, email, counselor, session in JULY1_TARGETS:
    client = User.objects.filter(email__iexact=email, role="CLIENT").first()
    apt = None
    if client:
        apt = (
            Appointment.objects.filter(client=client, session_number=session)
            .select_related("counselor", "case", "zoom_meeting")
            .order_by("-scheduled_at")
            .first()
        )
    print(f"\n--- {name} session {session} ---")
    if not apt:
        print("  ❌ Appointment 행 없음")
        continue
    local = timezone.localtime(apt.scheduled_at)
    in_slot = SLOT_START <= apt.scheduled_at < SLOT_END
    print(f"  status={apt.status}  scheduled={local:%Y-%m-%d %H:%M} KST")
    print(f"  counselor={apt.counselor.name} (expected {counselor})")
    print(f"  in 7/1 10:00 slot? {in_slot and local.strftime('%H:%M') == '10:00'}")
    print(f"  calendar eligible (CONFIRMED)? {apt.status == AppointmentStatus.CONFIRMED}")
    zm = getattr(apt, "zoom_meeting", None)
    if zm:
        print(f"  zoom_host={zm.zoom_host_email} meeting_id={zm.zoom_meeting_id}")

print("\n" + "=" * 60)
print("C. Calendar API vs DB (7/1 day)")
print("=" * 60)
day = parse_calendar_bound("2026-07-01T00:00:00+09:00")
nxt = parse_calendar_bound("2026-07-02T00:00:00+09:00")
events = build_calendar_events(start=day, end=nxt)
event_ids = {e["id"] for e in events}
db_confirmed = Appointment.objects.filter(
    status=AppointmentStatus.CONFIRMED,
    scheduled_at__gte=SLOT_START,
    scheduled_at__lt=SLOT_END,
)
print("Calendar event names:", [e["extendedProps"]["client_name"] for e in events])
for a in db_confirmed:
    visible = str(a.pk) in event_ids
    flag = "✅" if visible else "❌ MISSING FROM CALENDAR"
    print(f"  {flag} {a.client.name} s{a.session_number} id={a.pk} status={a.status}")

print("\n" + "=" * 60)
print("D. Non-CONFIRMED in slot (hidden from calendar)")
print("=" * 60)
hidden = slot_qs.exclude(status=AppointmentStatus.CONFIRMED)
if not hidden.exists():
    print("  (none)")
for a in hidden:
    print(f"  HIDDEN: {a.client.name} s{a.session_number} status={a.status} id={a.pk}")

print("\n" + "=" * 60)
print("E. Django Admin change log (Appointment, last 24h)")
print("=" * 60)
since = timezone.now() - timedelta(hours=24)
entries = LogEntry.objects.filter(content_type=apt_ct, action_time__gte=since).order_by(
    "-action_time"
)[:30]
if not entries:
    print("  (no entries)")
for entry in entries:
    print(
        f"  {entry.action_time:%m-%d %H:%M} action={entry.get_action_flag_display()} "
        f"user={entry.user} object={entry.object_repr}"
    )
    if entry.change_message:
        print(f"    {entry.change_message[:200]}")
