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

_ACTIVE_STATUSES = (
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.PENDING,
    AppointmentStatus.SCHEDULED,
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


def _local_label(dt) -> str:
    if dt is None:
        return "—"
    return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M")


def _format_appointment_line(appt: Appointment) -> str:
    return (
        f"  - id={appt.pk} status={appt.status} "
        f"session={appt.session_number} scheduled_at={_local_label(appt.scheduled_at)} "
        f"case={appt.case.case_number} counselor={getattr(appt.counselor, 'name', '')}"
    )


class Command(BaseCommand):
    help = (
        "관리자용: 특정 예약(회기)을 지정한 일시로 강제 변경합니다.\n"
        "예) python manage.py admin_reschedule_appointment "
        '--client "이경숙" --session 1 --from "2026-07-03 14:00" --to "2026-07-10 14:00" --apply\n'
        "예) python manage.py admin_reschedule_appointment --client \"이경숙\" --list\n"
    )

    def add_arguments(self, parser):
        parser.add_argument("--client", required=True, help="내담자 이름(정확히)")
        parser.add_argument("--session", type=int, help="회기 번호(예: 1). --list 시 생략 가능")
        parser.add_argument(
            "--from",
            dest="from_dt",
            help="기존 일시 (YYYY-MM-DD HH:MM, 한국 시간). 생략 시 해당 회기 예약 1건이면 자동 선택",
        )
        parser.add_argument("--to", dest="to_dt", help="변경할 일시 (YYYY-MM-DD HH:MM)")
        parser.add_argument(
            "--appointment-id",
            help="예약 UUID로 직접 지정 (--client/--session/--from 대신)",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="해당 내담자 예약 목록만 출력하고 종료",
        )
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

        if options["list"]:
            self._list_appointments(client_name, options.get("session"))
            return

        to_dt = _parse_local_dt(options["to_dt"]) if options.get("to_dt") else None
        if not to_dt and not options["list"]:
            raise CommandError("변경하려면 --to \"YYYY-MM-DD HH:MM\" 가 필요합니다.")

        tz = timezone.get_current_timezone()
        to_aware = timezone.make_aware(to_dt.value, tz) if to_dt else None

        appt = self._resolve_appointment(
            client_name=client_name,
            session_number=options.get("session"),
            from_raw=options.get("from_dt"),
            appointment_id=options.get("appointment_id"),
        )

        apply = bool(options["apply"])
        skip_availability = bool(options["force"])
        from_label = _local_label(appt.scheduled_at)

        self.stdout.write(
            f"- 대상: appointment_id={appt.pk} status={appt.status} "
            f"case={appt.case.case_number} counselor={getattr(appt.counselor, 'name', '')} "
            f"scheduled_at={from_label}"
        )
        self.stdout.write(
            f"- 변경: {from_label} → {to_dt.raw} (force={skip_availability}, apply={apply})"
        )

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
                f"완료: appointment_id={appt.pk} scheduled_at={_local_label(appt.scheduled_at)}"
            )
        )

    def _list_appointments(self, client_name: str, session_number: int | None) -> None:
        qs = (
            Appointment.objects.select_related("case", "case__client", "counselor", "client")
            .filter(client__name=client_name)
            .order_by("session_number", "scheduled_at")
        )
        if session_number:
            qs = qs.filter(session_number=session_number)

        rows = list(qs[:50])
        if not rows:
            raise CommandError(f"내담자 {client_name!r} 예약이 없습니다.")

        self.stdout.write(self.style.NOTICE(f"내담자 {client_name!r} 예약 목록 ({len(rows)}건):"))
        for appt in rows:
            self.stdout.write(_format_appointment_line(appt))

    def _resolve_appointment(
        self,
        *,
        client_name: str,
        session_number: int | None,
        from_raw: str | None,
        appointment_id: str | None,
    ) -> Appointment:
        if appointment_id:
            try:
                appt = Appointment.objects.select_related(
                    "case", "case__client", "counselor", "client"
                ).get(pk=appointment_id)
            except Appointment.DoesNotExist as exc:
                raise CommandError(f"appointment-id={appointment_id!r} 를 찾지 못했습니다.") from exc
            if appt.client.name != client_name:
                raise CommandError(
                    f"appointment-id={appointment_id} 의 내담자가 {client_name!r} 가 아닙니다."
                )
            return appt

        if not session_number:
            raise CommandError("--session 또는 --appointment-id 가 필요합니다.")

        base_qs = (
            Appointment.objects.select_related("case", "case__client", "counselor", "client")
            .filter(client__name=client_name, session_number=session_number)
            .order_by("-updated_at")
        )

        if from_raw:
            from_dt = _parse_local_dt(from_raw)
            active = [a for a in base_qs if a.status in _ACTIVE_STATUSES]
            matched = [
                a for a in active if _local_label(a.scheduled_at) == from_dt.raw
            ]
            if len(matched) == 1:
                return matched[0]
            if len(matched) > 1:
                lines = "\n".join(_format_appointment_line(a) for a in matched)
                raise CommandError(
                    f"일시 {from_dt.raw!r} 에 해당하는 예약이 여러 건입니다:\n{lines}"
                )

        active = [a for a in base_qs if a.status in _ACTIVE_STATUSES]
        if not from_raw and len(active) == 1:
            return active[0]

        all_rows = list(base_qs[:20])
        if not all_rows:
            raise CommandError(
                f"내담자 {client_name!r} 의 {session_number}회기 예약이 없습니다."
            )

        lines = "\n".join(_format_appointment_line(a) for a in all_rows)
        hint = (
            f"\n\n힌트: 먼저 목록 확인 → python manage.py admin_reschedule_appointment "
            f'--client "{client_name}" --session {session_number} --list'
        )
        if from_raw:
            raise CommandError(
                f"대상 예약을 찾지 못했습니다. client={client_name!r}, session={session_number}, "
                f"from={from_raw!r}\n\nDB에 있는 예약:\n{lines}{hint}"
            )
        raise CommandError(
            f"변경할 예약을 특정할 수 없습니다. --from 또는 --appointment-id 를 지정해 주세요.\n\n"
            f"DB에 있는 예약:\n{lines}{hint}"
        )
