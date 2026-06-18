from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import (
    AppointmentServiceError,
    reschedule_confirmed_appointment,
    update_pending_appointment,
)


@dataclass(frozen=True)
class _ParsedDatetime:
    value: datetime
    raw: str


def _parse_local_dt(text: str) -> _ParsedDatetime:
    raw = (text or "").strip()
    if not raw:
        raise CommandError("--from/--to 값이 비어 있습니다. 예: '2026-07-10 14:00'")
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise CommandError(
            f"날짜 형식이 올바르지 않습니다: {raw!r} (형식: YYYY-MM-DD HH:MM)"
        ) from exc
    return _ParsedDatetime(value=dt, raw=raw)


class Command(BaseCommand):
    help = (
        "관리자용: 특정 예약(회기)을 지정한 일시로 강제 변경합니다.\n"
        "예) python manage.py admin_reschedule_appointment "
        '--client "이경숙" --session 1 --from "2026-07-03 14:00" --to "2026-07-10 14:00" --apply\n'
    )

    def add_arguments(self, parser):
        parser.add_argument("--client", required=True, help="내담자 이름(정확히)")
        parser.add_argument("--session", type=int, required=True, help="회기 번호(예: 1)")
        parser.add_argument("--from", dest="from_dt", required=True, help="기존 일시 (YYYY-MM-DD HH:MM)")
        parser.add_argument("--to", dest="to_dt", required=True, help="변경할 일시 (YYYY-MM-DD HH:MM)")
        parser.add_argument(
            "--force",
            action="store_true",
            help="상담사 가용시간 검사를 건너뜁니다(중복 확정 예약 검사는 유지).",
        )
        parser.add_argument("--apply", action="store_true", help="실제 DB 변경을 적용합니다.")

    def handle(self, *args, **options):
        client_name = (options["client"] or "").strip()
        if not client_name:
            raise CommandError("--client 값이 비어 있습니다.")

        session_number = int(options["session"])
        if session_number < 1:
            raise CommandError("--session 값은 1 이상이어야 합니다.")

        from_dt = _parse_local_dt(options["from_dt"])
        to_dt = _parse_local_dt(options["to_dt"])

        tz = timezone.get_current_timezone()
        from_aware = timezone.make_aware(from_dt.value, tz)
        to_aware = timezone.make_aware(to_dt.value, tz)

        qs = (
            Appointment.objects.select_related("case", "case__client", "counselor", "client")
            .filter(
                session_number=session_number,
                client__name=client_name,
                status__in=(
                    AppointmentStatus.CONFIRMED,
                    AppointmentStatus.PENDING,
                    AppointmentStatus.SCHEDULED,
                ),
            )
            .filter(scheduled_at=from_aware)
            .order_by("-updated_at")
        )

        appt = qs.first()
        if not appt:
            # fallback: sometimes there are seconds stored; try small window match
            window_start = from_aware - timezone.timedelta(minutes=1)
            window_end = from_aware + timezone.timedelta(minutes=1)
            appt = (
                Appointment.objects.select_related("case", "case__client", "counselor", "client")
                .filter(
                    session_number=session_number,
                    client__name=client_name,
                    status__in=(
                        AppointmentStatus.CONFIRMED,
                        AppointmentStatus.PENDING,
                        AppointmentStatus.SCHEDULED,
                    ),
                )
                .filter(scheduled_at__gte=window_start, scheduled_at__lte=window_end)
                .order_by("scheduled_at", "-updated_at")
                .first()
            )

        if not appt:
            raise CommandError(
                f"대상 예약을 찾지 못했습니다. client={client_name!r}, session={session_number}, "
                f"from={from_dt.raw!r}"
            )

        apply = bool(options["apply"])
        skip_availability = bool(options["force"])

        self.stdout.write(
            f"- 대상: appointment_id={appt.pk} status={appt.status} "
            f"case={appt.case.case_number} counselor={getattr(appt.counselor, 'name', '')} "
            f"scheduled_at={timezone.localtime(appt.scheduled_at):%Y-%m-%d %H:%M}"
        )
        self.stdout.write(f"- 변경: {from_dt.raw} → {to_dt.raw} (force={skip_availability}, apply={apply})")

        if not apply:
            self.stdout.write(self.style.WARNING("DRY RUN: --apply가 없어 DB 변경을 수행하지 않았습니다."))
            return

        try:
            if appt.status == AppointmentStatus.CONFIRMED:
                appt, zoom_warning = reschedule_confirmed_appointment(
                    appt, new_scheduled_at=to_aware, skip_availability=skip_availability
                )
                if zoom_warning:
                    self.stdout.write(self.style.WARNING(f"Zoom 경고: {zoom_warning}"))
            else:
                appt = update_pending_appointment(
                    appt,
                    scheduled_at=to_aware,
                    duration_minutes=appt.duration_minutes,
                    notify_client=False,
                )
        except AppointmentServiceError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"완료: appointment_id={appt.pk} scheduled_at={timezone.localtime(appt.scheduled_at):%Y-%m-%d %H:%M}"
            )
        )

