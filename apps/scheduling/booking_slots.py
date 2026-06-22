"""예약 캘린더 — 날짜별 시간 슬롯 상태 계산."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from django.utils import timezone

from apps.counseling.models import Case, CounselingMethod
from apps.reports.appointment_calendar import _calendar_localtime, _intervals_overlap
from apps.scheduling.availability import (
    counselor_has_recurring_allow_rules,
    get_counselor_blocked_dates,
    is_counselor_slot_available,
    local_timezone,
    _combine,
    _ranges_overlap,
    _slot_fits_window,
    _slot_start_within_window,
)
from apps.scheduling.constants import (
    BOOKING_SLOT_END_HOUR,
    BOOKING_SLOT_START_HOUR,
    DEFAULT_APPOINTMENT_DURATION_MINUTES,
)
from apps.scheduling.in_person_room_capacity import (
    count_overlapping_confirmed_in_person,
    in_person_room_capacity_limit,
)
from apps.scheduling.models import Appointment, AppointmentStatus, AvailabilityException, CounselorAvailability
from apps.scheduling.remote_zoom_capacity import (
    appointment_duration_minutes,
    count_overlapping_confirmed_remote,
    remote_zoom_capacity_limit,
)

SlotState = Literal[
    "available",
    "blocked",
    "taken",
    "zoom_full",
    "room_full",
]


@dataclass(frozen=True)
class BookingSlot:
    start: datetime
    end: datetime
    state: SlotState
    label: str

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "state": self.state,
            "label": self.label,
        }


def _slot_label(start: datetime, end: datetime) -> str:
    return f"{start:%H:%M} – {end:%H:%M}"


def hourly_slot_starts(on_date: date) -> list[datetime]:
    """09:00~21:00 시작 (60분 상담 시 22:00 종료)."""
    tz = local_timezone()
    slots: list[datetime] = []
    for hour in range(BOOKING_SLOT_START_HOUR, BOOKING_SLOT_END_HOUR):
        if hour + (DEFAULT_APPOINTMENT_DURATION_MINUTES // 60) > BOOKING_SLOT_END_HOUR:
            break
        slots.append(
            timezone.make_aware(datetime.combine(on_date, time(hour, 0)), tz)
        )
    return slots


def counselor_has_overlapping_appointment(
    counselor_id,
    scheduled_at: datetime,
    duration_minutes: int,
    *,
    exclude_appointment_id=None,
) -> bool:
    start = _calendar_localtime(scheduled_at)
    end = start + timedelta(minutes=duration_minutes)

    peers = Appointment.objects.filter(
        counselor_id=counselor_id,
        status__in=[AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED],
    )
    if exclude_appointment_id:
        peers = peers.exclude(pk=exclude_appointment_id)

    for appointment in peers.iterator():
        peer_start = _calendar_localtime(appointment.scheduled_at)
        peer_end = peer_start + timedelta(
            minutes=appointment_duration_minutes(appointment)
        )
        if _intervals_overlap(start, end, peer_start, peer_end):
            return True
    return False


@dataclass
class _PeerInterval:
    start: datetime
    end: datetime


class MonthBookingContext:
    """월간 예약 가능일·슬롯 계산용 — DB 조회를 월 단위로 묶음."""

    __slots__ = (
        "case",
        "counselor_id",
        "counseling_method",
        "duration_minutes",
        "exclude_appointment_id",
        "require_full_duration",
        "blocked_dates",
        "blocked_exception_dates",
        "specific_by_date",
        "recurring_by_dow",
        "has_recurring_allow_rules",
        "counselor_peers",
        "venue_peers",
        "venue_limit",
    )

    def __init__(
        self,
        *,
        case: Case,
        month_start: date,
        month_end: date,
        duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
        exclude_appointment_id=None,
        require_full_duration: bool = False,
    ):
        self.case = case
        self.counselor_id = case.counselor_id
        self.counseling_method = case.counseling_method
        self.duration_minutes = duration_minutes
        self.exclude_appointment_id = exclude_appointment_id
        self.require_full_duration = require_full_duration
        self.blocked_dates = frozenset(get_counselor_blocked_dates(case.counselor_id))

        tz = local_timezone()
        range_start = timezone.make_aware(datetime.combine(month_start, time.min), tz)
        range_end = timezone.make_aware(datetime.combine(month_end, time.min), tz)

        self.blocked_exception_dates = frozenset(
            AvailabilityException.objects.filter(
                counselor_id=case.counselor_id,
                is_available=False,
                date__gte=month_start,
                date__lt=month_end,
            ).values_list("date", flat=True)
        )

        specific_rules = list(
            CounselorAvailability.objects.filter(
                counselor_id=case.counselor_id,
                is_recurring=False,
                is_active=True,
                specific_date__gte=month_start,
                specific_date__lt=month_end,
            )
        )
        self.specific_by_date: dict[date, list] = {}
        for rule in specific_rules:
            if rule.specific_date:
                self.specific_by_date.setdefault(rule.specific_date, []).append(rule)

        recurring_rules = list(
            CounselorAvailability.objects.filter(
                counselor_id=case.counselor_id,
                is_recurring=True,
                is_active=True,
            )
        )
        self.recurring_by_dow: dict[int, list] = {}
        for rule in recurring_rules:
            self.recurring_by_dow.setdefault(rule.day_of_week, []).append(rule)
        self.has_recurring_allow_rules = counselor_has_recurring_allow_rules(
            case.counselor_id
        )

        peer_qs = Appointment.objects.filter(
            counselor_id=case.counselor_id,
            status__in=[AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED],
            scheduled_at__lt=range_end,
        )
        if exclude_appointment_id:
            peer_qs = peer_qs.exclude(pk=exclude_appointment_id)
        self.counselor_peers = [
            _PeerInterval(
                start=_calendar_localtime(apt.scheduled_at),
                end=_calendar_localtime(apt.scheduled_at)
                + timedelta(minutes=appointment_duration_minutes(apt)),
            )
            for apt in peer_qs
            if _calendar_localtime(apt.scheduled_at) + timedelta(
                minutes=appointment_duration_minutes(apt)
            )
            > range_start
        ]

        if self.counseling_method == CounselingMethod.REMOTE:
            self.venue_limit = remote_zoom_capacity_limit()
            venue_qs = Appointment.objects.filter(
                status=AppointmentStatus.CONFIRMED,
                case__counseling_method=CounselingMethod.REMOTE,
                scheduled_at__lt=range_end,
            )
        elif self.counseling_method == CounselingMethod.IN_PERSON:
            self.venue_limit = in_person_room_capacity_limit()
            venue_qs = Appointment.objects.filter(
                status=AppointmentStatus.CONFIRMED,
                case__counseling_method=CounselingMethod.IN_PERSON,
                scheduled_at__lt=range_end,
            )
        else:
            self.venue_limit = 0
            venue_qs = Appointment.objects.none()

        if exclude_appointment_id:
            venue_qs = venue_qs.exclude(pk=exclude_appointment_id)
        self.venue_peers = [
            _PeerInterval(
                start=_calendar_localtime(apt.scheduled_at),
                end=_calendar_localtime(apt.scheduled_at)
                + timedelta(minutes=appointment_duration_minutes(apt)),
            )
            for apt in venue_qs
            if _calendar_localtime(apt.scheduled_at) + timedelta(
                minutes=appointment_duration_minutes(apt)
            )
            > range_start
        ]

    def _counselor_slot_available(self, slot_start: datetime, slot_end: datetime) -> bool:
        local_date = slot_start.date()
        if local_date in self.blocked_exception_dates:
            return False

        specific = self.specific_by_date.get(local_date, [])
        for rule in specific:
            if not rule.is_available:
                window_start = _combine(local_date, rule.start_time)
                window_end = _combine(local_date, rule.end_time)
                if _ranges_overlap(slot_start, slot_end, window_start, window_end):
                    return False

        specific_allows = [rule for rule in specific if rule.is_available]
        if specific_allows:
            for rule in specific_allows:
                window_start = _combine(local_date, rule.start_time)
                window_end = _combine(local_date, rule.end_time)
                if self.require_full_duration:
                    if _slot_fits_window(slot_start, slot_end, window_start, window_end):
                        return True
                elif _slot_start_within_window(slot_start, window_start, window_end):
                    return True
            return False

        recurring = self.recurring_by_dow.get(local_date.weekday(), [])
        if not recurring:
            if self.has_recurring_allow_rules:
                return False
            return True

        for rule in recurring:
            if not rule.is_available:
                window_start = _combine(local_date, rule.start_time)
                window_end = _combine(local_date, rule.end_time)
                if _ranges_overlap(slot_start, slot_end, window_start, window_end):
                    return False

        recurring_allows = [rule for rule in recurring if rule.is_available]
        if not recurring_allows:
            return False

        for rule in recurring_allows:
            window_start = _combine(local_date, rule.start_time)
            window_end = _combine(local_date, rule.end_time)
            if self.require_full_duration:
                if _slot_fits_window(slot_start, slot_end, window_start, window_end):
                    return True
            elif _slot_start_within_window(slot_start, window_start, window_end):
                return True
        return False

    def _counselor_taken(self, slot_start: datetime, slot_end: datetime) -> bool:
        for peer in self.counselor_peers:
            if _intervals_overlap(slot_start, slot_end, peer.start, peer.end):
                return True
        return False

    def _venue_full(self, slot_start: datetime, slot_end: datetime) -> bool:
        if self.venue_limit <= 0:
            return False
        count = sum(
            1
            for peer in self.venue_peers
            if _intervals_overlap(slot_start, slot_end, peer.start, peer.end)
        )
        return count >= self.venue_limit

    def resolve_slot_state(self, slot_start: datetime) -> SlotState:
        slot_end = slot_start + timedelta(minutes=self.duration_minutes)
        if not self._counselor_slot_available(slot_start, slot_end):
            return "blocked"
        if self._counselor_taken(slot_start, slot_end):
            return "taken"
        if self._venue_full(slot_start, slot_end):
            if self.counseling_method == CounselingMethod.REMOTE:
                return "zoom_full"
            return "room_full"
        return "available"

    def date_has_bookable_slot(self, on_date: date) -> bool:
        if on_date.isoformat() in self.blocked_dates:
            return False
        for slot_start in hourly_slot_starts(on_date):
            if self.resolve_slot_state(slot_start) == "available":
                return True
        return False

    def build_slots_for_date(self, on_date: date) -> list[BookingSlot]:
        slots: list[BookingSlot] = []
        for slot_start in hourly_slot_starts(on_date):
            slot_end = slot_start + timedelta(minutes=self.duration_minutes)
            state = self.resolve_slot_state(slot_start)
            slots.append(
                BookingSlot(
                    start=slot_start,
                    end=slot_end,
                    state=state,
                    label=_slot_label(slot_start, slot_end),
                )
            )
        return slots


def _venue_capacity_state(
    *,
    counseling_method: str,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id=None,
) -> SlotState | None:
    if counseling_method == CounselingMethod.REMOTE:
        limit = remote_zoom_capacity_limit()
        if limit <= 0:
            return None
        count = count_overlapping_confirmed_remote(
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            exclude_appointment_id=exclude_appointment_id,
        )
        if count >= limit:
            return "zoom_full"
    elif counseling_method == CounselingMethod.IN_PERSON:
        limit = in_person_room_capacity_limit()
        if limit <= 0:
            return None
        count = count_overlapping_confirmed_in_person(
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            exclude_appointment_id=exclude_appointment_id,
        )
        if count >= limit:
            return "room_full"
    return None


def resolve_slot_state(
    *,
    counselor_id,
    counseling_method: str,
    scheduled_at: datetime,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
    require_full_duration: bool = False,
) -> SlotState:
    available, _message = is_counselor_slot_available(
        counselor_id,
        scheduled_at,
        duration_minutes=duration_minutes,
        require_full_duration=require_full_duration,
    )
    if not available:
        return "blocked"

    if counselor_has_overlapping_appointment(
        counselor_id,
        scheduled_at,
        duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    ):
        return "taken"

    venue_state = _venue_capacity_state(
        counseling_method=counseling_method,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
    )
    if venue_state:
        return venue_state

    return "available"


def build_booking_slots_for_date(
    *,
    case: Case,
    on_date: date,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
    require_full_duration: bool = False,
    month_context: MonthBookingContext | None = None,
) -> list[BookingSlot]:
    """사례 기준 — 하루 시간 슬롯 목록."""
    if not case.counselor_id:
        return []

    if month_context is not None:
        return month_context.build_slots_for_date(on_date)

    counseling_method = case.counseling_method
    slots: list[BookingSlot] = []

    for slot_start in hourly_slot_starts(on_date):
        slot_end = slot_start + timedelta(minutes=duration_minutes)
        state = resolve_slot_state(
            counselor_id=case.counselor_id,
            counseling_method=counseling_method,
            scheduled_at=slot_start,
            duration_minutes=duration_minutes,
            exclude_appointment_id=exclude_appointment_id,
            require_full_duration=require_full_duration,
        )
        slots.append(
            BookingSlot(
                start=slot_start,
                end=slot_end,
                state=state,
                label=_slot_label(slot_start, slot_end),
            )
        )
    return slots


def date_has_bookable_slot(
    *,
    case: Case,
    on_date: date,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
    require_full_duration: bool = False,
    blocked_dates: set[str] | frozenset[str] | None = None,
) -> bool:
    """월간 달력 — 예약 가능한 슬롯이 하나라도 있는 날."""
    if not case.counselor_id:
        return False
    if blocked_dates is None:
        blocked = set(get_counselor_blocked_dates(case.counselor_id))
    else:
        blocked = set(blocked_dates)
    if on_date.isoformat() in blocked:
        return False

    for slot in build_booking_slots_for_date(
        case=case,
        on_date=on_date,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
        require_full_duration=require_full_duration,
    ):
        if slot.state == "available":
            return True
    return False


def month_date_bounds(year: int, month: int) -> tuple[date, date]:
    """해당 월 [시작일, 다음 달 1일) 구간."""
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1)
    else:
        month_end = date(year, month + 1, 1)
    return month_start, month_end


def build_available_dates_for_month(
    *,
    case: Case,
    month_start: date,
    month_end: date,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_appointment_id=None,
    require_full_duration: bool = False,
) -> list[str]:
    """월간 달력 — 예약 가능한 날짜 목록."""
    if not case.counselor_id:
        return []
    ctx = MonthBookingContext(
        case=case,
        month_start=month_start,
        month_end=month_end,
        duration_minutes=duration_minutes,
        exclude_appointment_id=exclude_appointment_id,
        require_full_duration=require_full_duration,
    )
    available_dates: list[str] = []
    cursor = month_start
    while cursor < month_end:
        if ctx.date_has_bookable_slot(cursor):
            available_dates.append(cursor.isoformat())
        cursor = cursor.fromordinal(cursor.toordinal() + 1)
    return available_dates
