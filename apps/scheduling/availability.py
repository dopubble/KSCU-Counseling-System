"""상담사 가용·차단 시간 조회."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from .models import AvailabilityException, CounselorAvailability


def local_timezone() -> ZoneInfo:
    """프로젝트 기준 타임존 (Asia/Seoul)."""
    return ZoneInfo(settings.TIME_ZONE)


def normalize_client_preferred_datetime(dt: datetime | None) -> datetime | None:
    """폼·뷰에서 사용 — 사용자가 고른 일시를 KST aware datetime으로 통일."""
    if dt is None:
        return None
    tz = local_timezone()
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, tz)
    return timezone.localtime(dt, tz)


def format_local_datetime(dt: datetime | None) -> str:
    """플랫폼 표시용 — KST wall-clock (Y-m-d H:i)."""
    if dt is None:
        return "—"
    return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M")


def serialize_counselor_availability_rules(counselor) -> list[dict]:
    """내담자 예약 달력용 — 상담사 가용·차단 규칙 JSON."""
    if not counselor:
        return []
    return [
        {
            "is_recurring": av.is_recurring,
            "day_of_week": av.day_of_week,
            "is_available": av.is_available,
            "specific_date": av.specific_date.isoformat() if av.specific_date else None,
            "start_time": av.start_time.strftime("%H:%M"),
            "end_time": av.end_time.strftime("%H:%M"),
        }
        for av in CounselorAvailability.objects.filter(
            counselor=counselor, is_active=True
        ).order_by(
            "specific_date", "day_of_week", "start_time"
        )
    ]


def _normalize_time(value: time) -> time:
    """TimeField 비교 시 초·마이크로초 차이로 인한 오판 방지."""
    return value.replace(microsecond=0)


def _combine(local_date, time_value: time) -> datetime:
    """날짜 + 시각 → KST aware datetime."""
    tz = local_timezone()
    normalized = _normalize_time(time_value)
    return timezone.make_aware(datetime.combine(local_date, normalized), tz)


def _to_local_dt(dt: datetime) -> datetime:
    """검증 대상 일시를 KST aware datetime으로 변환."""
    return normalize_client_preferred_datetime(dt)


def _slot_end(start_dt: datetime, duration_minutes: int) -> datetime:
    return start_dt + timedelta(minutes=duration_minutes)


def _ranges_overlap(start_a, end_a, start_b, end_b) -> bool:
    return start_a < end_b and end_a > start_b


def _slot_start_within_window(slot_start: datetime, window_start: datetime, window_end: datetime) -> bool:
    """
    예약 시작 시각이 가용 구간에 포함되는지 (KST wall-clock 기준).
    종료 시각(window_end)에 시작하는 경우도 허용.
    """
    return window_start <= slot_start <= window_end


def _slot_fits_window(
    slot_start: datetime,
    slot_end: datetime,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    """시작~종료(상담 시간 포함) 전체가 가용 구간 안에 들어가는지."""
    return slot_start >= window_start and slot_end <= window_end


def _collect_allow_rules_for_date(
    counselor_id,
    local_date,
    *,
    specific_rules=None,
    recurring_rules=None,
):
    """해당 날짜에 적용되는 상담 가능 규칙 목록."""
    if specific_rules is None:
        specific_rules = CounselorAvailability.objects.filter(
            counselor_id=counselor_id,
            is_recurring=False,
            specific_date=local_date,
            is_active=True,
        )
    allows = list(specific_rules.filter(is_available=True))
    if allows:
        return allows

    if recurring_rules is None:
        recurring_rules = CounselorAvailability.objects.filter(
            counselor_id=counselor_id,
            is_recurring=True,
            day_of_week=local_date.weekday(),
            is_active=True,
        )
    return list(recurring_rules.filter(is_available=True))


def format_availability_windows_hint(counselor_id, local_dt: datetime) -> str:
    """검증 실패 안내용 — 해당 날짜 가능 시간대 요약."""
    local_dt = _to_local_dt(local_dt)
    local_date = local_dt.date()

    specific_rules = CounselorAvailability.objects.filter(
        counselor_id=counselor_id,
        is_recurring=False,
        specific_date=local_date,
        is_active=True,
    )
    recurring_rules = CounselorAvailability.objects.filter(
        counselor_id=counselor_id,
        is_recurring=True,
        day_of_week=local_date.weekday(),
        is_active=True,
    )
    allows = _collect_allow_rules_for_date(
        counselor_id,
        local_date,
        specific_rules=specific_rules,
        recurring_rules=recurring_rules,
    )
    if not allows and not specific_rules.exists() and not recurring_rules.exists():
        return ""

    if not allows:
        return "등록된 상담 가능 시간이 없습니다."

    labels = []
    for rule in allows:
        labels.append(
            f"{rule.start_time.strftime('%H:%M')}~{rule.end_time.strftime('%H:%M')}"
        )
    return "가능 시간: " + ", ".join(labels)


def counselor_has_availability_rules(counselor_id) -> bool:
    return CounselorAvailability.objects.filter(
        counselor_id=counselor_id,
        is_active=True,
    ).exists()


def counselor_has_recurring_allow_rules(counselor_id) -> bool:
    """매주 반복 상담 가능 시간이 등록되어 있는지."""
    return CounselorAvailability.objects.filter(
        counselor_id=counselor_id,
        is_recurring=True,
        is_available=True,
        is_active=True,
    ).exists()


def get_counselor_recurring_availabilities(counselor):
    """
    내담자 안내용 — 매주 반복·상담 가능 시간(월~일, 시간순).

    counselor: User 인스턴스 또는 counselor_id(UUID).
    """
    if not counselor:
        return CounselorAvailability.objects.none()

    counselor_filter = (
        {"counselor": counselor}
        if hasattr(counselor, "_meta")
        else {"counselor_id": counselor}
    )

    return (
        CounselorAvailability.objects.filter(**counselor_filter)
        .filter(Q(is_recurring=True) | Q(specific_date__isnull=True))
        .exclude(is_available=False)
        .order_by("day_of_week", "start_time")
    )


def is_counselor_slot_available(
    counselor_id,
    scheduled_at,
    *,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    require_full_duration: bool = False,
) -> tuple[bool, str]:
    """
    내담자 예약 가능 여부.
    - 특정일 차단(is_available=False)이 정기 가용시간보다 우선
    - 가용 규칙이 없으면 기존과 동일하게 허용
    - 매주 반복 가용 시간이 등록된 경우, 해당 요일 규칙이 없으면 불가 (예: 월~금만 등록 시 주말 불가)
    - require_full_duration=False(기본): 시작 시각만 가용 구간에 포함되면 허용 (예약 요청)
    - require_full_duration=True: 상담 시간(분)까지 포함해 구간 안인지 검사 (확정 등)
    """
    if scheduled_at is None:
        return False, "상담 일시를 확인해 주세요."

    local_dt = _to_local_dt(scheduled_at)
    local_date = local_dt.date()
    slot_start = local_dt
    slot_end = _slot_end(slot_start, duration_minutes)

    if AvailabilityException.objects.filter(
        counselor_id=counselor_id,
        date=local_date,
        is_available=False,
    ).exists():
        return False, "해당 날짜는 상담사 휴무일입니다."

    specific_rules = CounselorAvailability.objects.filter(
        counselor_id=counselor_id,
        is_recurring=False,
        specific_date=local_date,
        is_active=True,
    )

    for rule in specific_rules.filter(is_available=False):
        window_start = _combine(local_date, rule.start_time)
        window_end = _combine(local_date, rule.end_time)
        if _ranges_overlap(slot_start, slot_end, window_start, window_end):
            return False, "해당 시간은 상담사 차단(휴무) 시간입니다."

    specific_allows = list(specific_rules.filter(is_available=True))
    if specific_allows:
        for rule in specific_allows:
            window_start = _combine(local_date, rule.start_time)
            window_end = _combine(local_date, rule.end_time)
            if require_full_duration:
                if _slot_fits_window(slot_start, slot_end, window_start, window_end):
                    return True, ""
            elif _slot_start_within_window(slot_start, window_start, window_end):
                return True, ""
        hint = format_availability_windows_hint(counselor_id, local_dt)
        if hint:
            return False, f"선택하신 시간은 상담사 상담 가능 시간대가 아닙니다. ({hint})"
        return False, "해당 시간은 상담사 상담 가능 시간이 아닙니다."

    recurring_rules = CounselorAvailability.objects.filter(
        counselor_id=counselor_id,
        is_recurring=True,
        day_of_week=local_date.weekday(),
        is_active=True,
    )
    if not recurring_rules.exists():
        if counselor_has_recurring_allow_rules(counselor_id):
            return False, "해당 요일은 상담 가능 시간이 등록되어 있지 않습니다."
        return True, ""

    for rule in recurring_rules.filter(is_available=False):
        window_start = _combine(local_date, rule.start_time)
        window_end = _combine(local_date, rule.end_time)
        if _ranges_overlap(slot_start, slot_end, window_start, window_end):
            return False, "해당 시간은 상담사 차단 시간입니다."

    recurring_allows = list(recurring_rules.filter(is_available=True))
    if not recurring_allows:
        return False, "해당 요일은 상담 가능 시간이 등록되어 있지 않습니다."

    for rule in recurring_allows:
        window_start = _combine(local_date, rule.start_time)
        window_end = _combine(local_date, rule.end_time)
        if require_full_duration:
            if _slot_fits_window(slot_start, slot_end, window_start, window_end):
                return True, ""
        elif _slot_start_within_window(slot_start, window_start, window_end):
            return True, ""
    hint = format_availability_windows_hint(counselor_id, local_dt)
    if hint:
        return False, f"선택하신 시간은 상담사 상담 가능 시간대가 아닙니다. ({hint})"
    return False, "해당 시간은 상담사 상담 가능 시간이 아닙니다."


def get_counselor_blocked_dates(counselor_id) -> list[str]:
    """전일 차단된 특정 날짜(ISO) — 클라이언트 date 입력 안내용."""
    blocked: set[str] = set()

    for exc in AvailabilityException.objects.filter(
        counselor_id=counselor_id,
        is_available=False,
    ):
        blocked.add(exc.date.isoformat())

    for rule in CounselorAvailability.objects.filter(
        counselor_id=counselor_id,
        is_recurring=False,
        is_available=False,
        is_active=True,
    ):
        if not rule.specific_date:
            continue
        if rule.start_time <= time(0, 0) and rule.end_time >= time(23, 0):
            blocked.add(rule.specific_date.isoformat())

    return sorted(blocked)
