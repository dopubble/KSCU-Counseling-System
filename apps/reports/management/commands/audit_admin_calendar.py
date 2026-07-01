"""관리자 캘린더 API와 DB 확정 예약 대조 — 누락·불일치 진단."""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from zoneinfo import ZoneInfo

from apps.counseling.session1_bulk_import import load_session1_matches
from apps.reports.appointment_calendar import (
    CALENDAR_VISIBLE_APPOINTMENT_STATUSES,
    _calendar_localtime,
    appointment_in_calendar_events,
    build_calendar_events,
    calendar_day_bounds,
    get_calendar_timezone_name,
    parse_calendar_bound,
)
from apps.scheduling.models import Appointment, AppointmentStatus

DEFAULT_JSON = (
    Path(settings.BASE_DIR) / "data" / "import" / "session1_matches_bulk_202606.json"
)


class Command(BaseCommand):
    help = (
        "지정 기간의 확정·완료 예약과 build_calendar_events() 결과를 대조합니다.\n"
        "예시: python manage.py audit_admin_calendar --from 2026-06-01 --to 2026-06-30"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--from",
            dest="from_date",
            default="",
            help="시작일 (YYYY-MM-DD, 기본: 이번 달 1일)",
        )
        parser.add_argument(
            "--to",
            dest="to_date",
            default="",
            help="종료일 (YYYY-MM-DD, inclusive, 기본: 이번 달 말일)",
        )
        parser.add_argument(
            "--roster",
            action="store_true",
            help="1회기 로스터 JSON의 first_session 날짜도 함께 검사",
        )
        parser.add_argument(
            "--fail-on-error",
            action="store_true",
            help="누락이 있으면 exit code 1",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        from_date = self._parse_date(options["from_date"]) or today.replace(day=1)
        to_date = self._parse_date(options["to_date"]) or self._month_end(today)

        if to_date < from_date:
            raise CommandError("--to는 --from 이후여야 합니다.")

        service_tz = ZoneInfo(get_calendar_timezone_name())
        range_start = timezone.make_aware(
            datetime.combine(from_date, datetime.min.time()),
            service_tz,
        )
        range_end = timezone.make_aware(
            datetime.combine(to_date + timedelta(days=1), datetime.min.time()),
            service_tz,
        )

        db_qs = (
            Appointment.objects.filter(status__in=CALENDAR_VISIBLE_APPOINTMENT_STATUSES)
            .filter(
                scheduled_at__gte=range_start.astimezone(dt_timezone.utc),
                scheduled_at__lt=range_end.astimezone(dt_timezone.utc),
            )
            .select_related("client", "counselor")
            .order_by("scheduled_at")
        )
        db_by_id = {str(apt.pk): apt for apt in db_qs}
        events = build_calendar_events(start=range_start, end=range_end)
        event_ids = {event["id"] for event in events}

        missing_in_calendar = sorted(
            set(db_by_id) - event_ids,
            key=lambda pk: db_by_id[pk].scheduled_at,
        )
        extra_in_calendar = sorted(event_ids - set(db_by_id))

        self.stdout.write(
            self.style.NOTICE(
                f"=== 캘린더 감사 {from_date} ~ {to_date} (표시 대상 {len(db_by_id)}건) ==="
            )
        )

        if missing_in_calendar:
            self.stdout.write(self.style.ERROR("DB에는 있으나 캘린더 이벤트에 없음:"))
            for pk in missing_in_calendar:
                apt = db_by_id[pk]
                self.stdout.write(
                    f"  - {apt.client.name} {apt.session_number}회차 "
                    f"{timezone.localtime(apt.scheduled_at):%Y-%m-%d %H:%M} "
                    f"({apt.get_status_display()}) id={pk}"
                )
        else:
            self.stdout.write(self.style.SUCCESS("캘린더 누락 없음 (표시 대상 전건 표시)."))

        if extra_in_calendar:
            self.stdout.write(self.style.WARNING("캘린더에만 있는 ID (DB 범위 밖):"))
            for pk in extra_in_calendar:
                self.stdout.write(f"  - id={pk}")

        not_confirmed_session1: list[str] = []
        if options["roster"]:
            path = DEFAULT_JSON
            if not path.is_file():
                raise CommandError(f"로스터 JSON 없음: {path}")
            rows = load_session1_matches(path)
            for row in rows:
                roster_day = _calendar_localtime(row.first_session).date()
                if not (from_date <= roster_day <= to_date):
                    continue
                apt = (
                    Appointment.objects.filter(
                        client__name=row.client_name,
                        counselor__name=row.counselor_name,
                        session_number=1,
                    )
                    .order_by("-created_at")
                    .first()
                )
                expected_label = _calendar_localtime(row.first_session).strftime(
                    "%Y-%m-%d %H:%M"
                )
                if apt is None:
                    not_confirmed_session1.append(f"{row.client_name}: 1회기 예약 없음")
                elif apt.status != AppointmentStatus.CONFIRMED:
                    not_confirmed_session1.append(
                        f"{row.client_name}: {apt.get_status_display()} "
                        f"({_calendar_localtime(apt.scheduled_at):%Y-%m-%d %H:%M})"
                    )
                elif _calendar_localtime(apt.scheduled_at).strftime("%Y-%m-%d %H:%M") != expected_label:
                    not_confirmed_session1.append(
                        f"{row.client_name}: 일시 불일치 DB="
                        f"{_calendar_localtime(apt.scheduled_at):%Y-%m-%d %H:%M} "
                        f"기대={expected_label}"
                    )
                elif not appointment_in_calendar_events(apt.pk, local_day=roster_day):
                    day_start, day_end = calendar_day_bounds(roster_day)
                    fc_events = build_calendar_events(start=day_start, end=day_end)
                    not_confirmed_session1.append(
                        f"{row.client_name}: CONFIRMED·일치하나 "
                        f"{roster_day:%Y-%m-%d} 캘린더 미표시 id={apt.pk} "
                        f"(day events={len(fc_events)})"
                    )

            if not_confirmed_session1:
                self.stdout.write("")
                self.stdout.write(self.style.ERROR("1회기 로스터 캘린더 가시성 문제:"))
                for line in not_confirmed_session1:
                    self.stdout.write(f"  - {line}")
            else:
                self.stdout.write("")
                self.stdout.write(
                    self.style.SUCCESS("1회기 로스터: 기간 내 전원 캘린더 표시 가능.")
                )

        has_issues = bool(missing_in_calendar or not_confirmed_session1)
        if has_issues and options["fail_on_error"]:
            raise CommandError("캘린더 감사에서 누락이 발견되었습니다.")

    @staticmethod
    def _parse_date(raw: str) -> date | None:
        text = (raw or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise CommandError(f"날짜 형식 오류: {text!r} (YYYY-MM-DD)") from exc

    @staticmethod
    def _month_end(day: date) -> date:
        if day.month == 12:
            return day.replace(day=31)
        next_month = day.replace(month=day.month + 1, day=1)
        return next_month - timedelta(days=1)
