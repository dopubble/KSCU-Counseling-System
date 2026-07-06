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
    BOOKING_SLOT_INTERVAL_MINUTES,
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
    is_remote_zoom_slot_available,
    remote_zoom_capacity_limit,
    remote_zoom_same_start_remaining,
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
    room_remaining: int | None = None
    zoom_remaining: int | None = None

    def to_dict(self) -> dict:
        payload = {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "state": self.state,
            "label": self.label,
        }
        if self.room_remaining is not None:
            payload["room_remaining"] = self.room_remaining
        if self.zoom_remaining is not None:
            payload["zoom_remaining"] = self.zoom_remaining
        return payload


def _slot_label(start: datetime, end: datetime) -> str:
    return f"{start:%H:%M} – {end:%H:%M}"


def interval_slot_starts(on_date: date) -> list[datetime]:
    """09:00~22:00 — BOOKING_SLOT_INTERVAL_MINUTES 간격 시작 (기본 상담 시간 내 종료)."""
    tz = local_timezone()
    slots: list[datetime] = []
    duration = DEFAULT_APPOINTMENT_DURATION_MINUTES
    end_boundary = timezone.make_aware(
        datetime.combine(on_date, time(BOOKING_SLOT_END_HOUR, 0)),
        tz,
    )
    day_start = timezone.make_aware(
        datetime.combine(on_date, time(BOOKING_SLOT_START_HOUR, 0)),
        tz,
    )
    step = timedelta(minutes=BOOKING_SLOT_INTERVAL_MINUTES)
    cursor = day_start
    while cursor + timedelta(minutes=duration) <= end_boundary:
        slots.append(cursor)
        cursor += step
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


def _load_confirmed_venue_peers(
    *,
    counseling_method: str,
    range_start: datetime,
    range_end: datetime,
    exclude_appointment_id=None,
) -> list[_PeerInterval]:
    peers_qs = Appointment.objects.filter(
        status=AppointmentStatus.CONFIRMED,
        case__counseling_method=counseling_method,
        scheduled_at__lt=range_end,
    )
    if exclude_appointment_id:
        peers_qs = peers_qs.exclude(pk=exclude_appointment_id)
    intervals: list[_PeerInterval] = []
    for apt in peers_qs:
        peer_start = _calendar_localtime(apt.scheduled_at)
        peer_end = peer_start + timedelta(minutes=appointment_duration_minutes(apt))
        if peer_end > range_start:
            intervals.append(_PeerInterval(start=peer_start, end=peer_end))
    return intervals


def _venue_remaining(
    peers: list[_PeerInterval],
    limit: int,
    slot_start: datetime,
    slot_end: datetime,
) -> int:
    if limit <= 0:
        return 0
    count = sum(
        1 for peer in peers if _intervals_overlap(slot_start, slot_end, peer.start, peer.end)
    )
    return max(0, limit - count)


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
        "remote_peers",
        "in_person_peers",
        "zoom_limit",
        "room_limit",
        "include_venue_remainings",
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
        include_venue_remainings: bool = False,
    ):
        self.case = case
        self.counselor_id = case.counselor_id
        self.counseling_method = case.counseling_method
        self.duration_minutes = duration_minutes
        self.exclude_appointment_id = exclude_appointment_id
        self.require_full_duration = require_full_duration
        self.include_venue_remainings = include_venue_remainings
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

        self.zoom_limit = remote_zoom_capacity_limit()
        self.room_limit = in_person_room_capacity_limit()
        self.remote_peers = _load_confirmed_venue_peers(
            counseling_method=CounselingMethod.REMOTE,
            range_start=range_start,
            range_end=range_end,
            exclude_appointment_id=exclude_appointment_id,
        )
        self.in_person_peers = _load_confirmed_venue_peers(
            counseling_method=CounselingMethod.IN_PERSON,
            range_start=range_start,
            range_end=range_end,
            exclude_appointment_id=exclude_appointment_id,
        )
        if self.counseling_method == CounselingMethod.REMOTE:
            self.venue_peers = self.remote_peers
            self.venue_limit = self.zoom_limit
        elif self.counseling_method == CounselingMethod.IN_PERSON:
            self.venue_peers = self.in_person_peers
            self.venue_limit = self.room_limit
        else:
            self.venue_peers = []
            self.venue_limit = 0

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
        if self.counseling_method == CounselingMethod.REMOTE:
            return not is_remote_zoom_slot_available(
                scheduled_at=slot_start,
                duration_minutes=self.duration_minutes,
                exclude_appointment_id=self.exclude_appointment_id,
            )
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
        for slot_start in interval_slot_starts(on_date):
            if self.resolve_slot_state(slot_start) == "available":
                return True
        return False

    def build_slots_for_date(self, on_date: date) -> list[BookingSlot]:
        slots: list[BookingSlot] = []
        for slot_start in interval_slot_starts(on_date):
            slot_end = slot_start + timedelta(minutes=self.duration_minutes)
            state = self.resolve_slot_state(slot_start)
            room_remaining = None
            zoom_remaining = None
            if self.include_venue_remainings:
                room_remaining = _venue_remaining(
                    self.in_person_peers,
                    self.room_limit,
                    slot_start,
                    slot_end,
                )
                zoom_remaining = remote_zoom_same_start_remaining(
                    scheduled_at=slot_start,
                    exclude_appointment_id=self.exclude_appointment_id,
                )
            slots.append(
                BookingSlot(
                    start=slot_start,
                    end=slot_end,
                    state=state,
                    label=_slot_label(slot_start, slot_end),
                    room_remaining=room_remaining,
                    zoom_remaining=zoom_remaining,
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
        if not is_remote_zoom_slot_available(
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            exclude_appointment_id=exclude_appointment_id,
        ):
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
    include_venue_remainings: bool = False,
) -> list[BookingSlot]:
    """사례 기준 — 하루 시간 슬롯 목록."""
    if not case.counselor_id:
        return []

    if month_context is not None:
        return month_context.build_slots_for_date(on_date)

    if include_venue_remainings:
        day_end = on_date.fromordinal(on_date.toordinal() + 1)
        ctx = MonthBookingContext(
            case=case,
            month_start=on_date,
            month_end=day_end,
            duration_minutes=duration_minutes,
            exclude_appointment_id=exclude_appointment_id,
            require_full_duration=require_full_duration,
            include_venue_remainings=True,
        )
        return ctx.build_slots_for_date(on_date)

    counseling_method = case.counseling_method
    slots: list[BookingSlot] = []

    for slot_start in interval_slot_starts(on_date):
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
