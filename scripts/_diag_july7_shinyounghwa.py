"""7/7 신영화·장경화 예약 불가 원인 진단."""
from datetime import date, datetime

from django.utils import timezone

from apps.accounts.models import User
from apps.counseling.models import Case, CounselingMethod
from apps.scheduling.availability import is_counselor_slot_available
from apps.scheduling.booking_slots import build_booking_slots_for_date, resolve_slot_state
from apps.scheduling.models import Appointment, CounselorAvailability

TARGET = date(2026, 7, 7)


def main():
    counselor = User.objects.filter(name="신영화").first()
    client = User.objects.filter(name="장경화").first()
    if not counselor:
        print("신영화 not found")
        return

    print("=== 신영화 가용 규칙 ===")
    for av in CounselorAvailability.objects.filter(
        counselor=counselor, is_active=True
    ).order_by("is_recurring", "specific_date", "day_of_week", "start_time"):
        print(
            f"  recurring={av.is_recurring} dow={av.day_of_week} "
            f"date={av.specific_date} {av.start_time}-{av.end_time} "
            f"avail={av.is_available}"
        )

    print(f"\n=== {TARGET} 신영화 예약 ===")
    for apt in Appointment.objects.filter(
        counselor=counselor, scheduled_at__date=TARGET
    ).order_by("scheduled_at"):
        client_name = apt.case.client.name if apt.case_id else "?"
        print(
            f"  {timezone.localtime(apt.scheduled_at):%Y-%m-%d %H:%M} "
            f"status={apt.status} client={client_name} session={apt.session_number}"
        )

    tz = timezone.get_current_timezone()
    for hour in (9, 10, 10, 30, 11):
        minute = hour if hour != 10.5 else 30
        h = 10 if hour == 10.5 else int(hour)
        m = 30 if hour == 10.5 else 0
        slot = timezone.make_aware(datetime(2026, 7, 7, h, m), tz)
        ok, msg = is_counselor_slot_available(
            counselor.pk, slot, require_full_duration=False
        )
        state = resolve_slot_state(
            counselor_id=counselor.pk,
            counseling_method=CounselingMethod.REMOTE,
            scheduled_at=slot,
        )
        print(f"\n  {slot:%H:%M} availability={ok} ({msg or 'ok'}) state={state}")

    if client:
        case = Case.objects.filter(client=client).select_related("counselor").first()
        if case:
            print(f"\n=== 장경화 사례 ===")
            print(f"  counselor={case.counselor.name} method={case.counseling_method}")
            slots = build_booking_slots_for_date(case=case, on_date=TARGET)
            print("  morning slots:")
            for s in slots:
                if 9 <= s.start.hour <= 12:
                    print(f"    {s.start:%H:%M} -> {s.state}")


if __name__ == "__main__":
    main()
