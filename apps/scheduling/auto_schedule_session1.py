"""내담자 희망 시간 × 상담사 가용시간 매칭 — 1회기 예약."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User, UserRole
from apps.counseling.models import Case, CaseStatus, CounselingMethod
from apps.scheduling.availability import is_counselor_slot_available, local_timezone
from apps.scheduling.client_preference_seed import (
    CLIENT_PREFERENCE_SEEDS,
    EXCLUDED_CLIENT_EMAILS,
    EXCLUDED_COUNSELOR_NAMES,
    SESSION1_AUTO_MATCH_EMAILS,
    ClientPreferenceSeed,
)
from apps.scheduling.counselor_availability_seed import (
    COUNSELOR_AVAILABILITY_SEEDS,
    AvailabilitySlotSeed,
    CounselorAvailabilitySeed,
)
from apps.scheduling.forms import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus, CounselorAvailability

# Zoom 1계정 — 같은 날 다른 상담과 시작 시각 최소 간격(분)
MIN_SAME_DAY_START_GAP_MINUTES = 120
from apps.scheduling.services import (
    AppointmentServiceError,
    _counselor_slot_taken,
    confirm_appointment_with_zoom,
    ensure_pending_session_appointment,
)


@dataclass
class ScheduleMatchResult:
    client_name: str
    client_email: str
    counselor_name: str
    scheduled_at: datetime | None
    status: str  # matched | no_overlap | skipped | error | already_confirmed
    detail: str = ""


@dataclass(frozen=True)
class _TimeInterval:
    start: datetime
    end: datetime


def _slot_end(start: datetime, duration_minutes: int) -> datetime:
    return start + timedelta(minutes=duration_minutes)


def _intervals_overlap(a: _TimeInterval, b: _TimeInterval) -> bool:
    return a.start < b.end and a.end > b.start


def _normalize_interval(start: datetime, duration_minutes: int) -> _TimeInterval:
    local_start = timezone.localtime(start)
    return _TimeInterval(local_start, _slot_end(local_start, duration_minutes))


def _global_slot_blocked(
    scheduled_at: datetime,
    duration_minutes: int,
    blocked: list[_TimeInterval],
) -> bool:
    """Zoom 동시 1회 + 같은 날 시작 시각 최소 2시간 간격."""
    candidate = _normalize_interval(scheduled_at, duration_minutes)
    candidate_start = candidate.start

    for other in blocked:
        if _intervals_overlap(candidate, other):
            return True
        if candidate_start.date() != other.start.date():
            continue
        gap_minutes = abs((candidate_start - other.start).total_seconds()) / 60
        if gap_minutes < MIN_SAME_DAY_START_GAP_MINUTES:
            return True
    return False


def _collect_global_blocked_intervals(
    *,
    exclude_appointment_ids: set | None = None,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    exclude_session1_client_emails: frozenset[str] | None = None,
) -> list[_TimeInterval]:
    """Zoom 동시 1회 — 확정·대기·예약 중 시간대가 겹치면 차단."""
    blocked: list[_TimeInterval] = []
    exclude_appointment_ids = exclude_appointment_ids or set()
    qs = Appointment.objects.filter(
        status__in=[
            AppointmentStatus.SCHEDULED,
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.PENDING,
        ]
    ).exclude(pk__in=exclude_appointment_ids).select_related("client")
    for apt in qs.only("scheduled_at", "duration_minutes", "session_number", "client__email"):
        if (
            exclude_session1_client_emails
            and apt.session_number == 1
            and (apt.client.email or "").lower() in exclude_session1_client_emails
        ):
            continue
        duration = apt.duration_minutes or duration_minutes
        blocked.append(_normalize_interval(apt.scheduled_at, duration))
    return blocked


def _parse_time(value: str) -> time:
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).time()
        except ValueError:
            continue
    raise ValueError(f"invalid time: {value!r}")


def _combine(local_date: date, time_value: time) -> datetime:
    tz = local_timezone()
    return timezone.make_aware(datetime.combine(local_date, time_value), tz)


def _intersect_time_ranges(
    start_a: time, end_a: time, start_b: time, end_b: time
) -> tuple[time, time] | None:
    start = max(start_a, start_b)
    end = min(end_a, end_b)
    if start >= end:
        return None
    return start, end


def _counselor_rules_for_weekday(counselor_id, weekday: int):
    return CounselorAvailability.objects.filter(
        counselor_id=counselor_id,
        is_recurring=True,
        day_of_week=weekday,
        is_available=True,
        is_active=True,
    )


def _iter_candidate_starts(
    local_date: date,
    client_slot: AvailabilitySlotSeed,
    counselor_rules,
    *,
    duration_minutes: int,
    step_minutes: int = 10,
) -> list[datetime]:
    weekday = local_date.weekday()
    if weekday not in client_slot.days:
        return []

    client_start = _parse_time(client_slot.start_time)
    client_end = _parse_time(client_slot.end_time)
    candidates: list[datetime] = []

    for rule in counselor_rules:
        overlap = _intersect_time_ranges(
            client_start, client_end, rule.start_time, rule.end_time
        )
        if not overlap:
            continue
        win_start, win_end = overlap
        cursor = _combine(local_date, win_start)
        window_end = _combine(local_date, win_end)
        slot_delta = timedelta(minutes=step_minutes)
        duration = timedelta(minutes=duration_minutes)

        while cursor + duration <= window_end:
            candidates.append(cursor)
            cursor += slot_delta

    return candidates


def _counselor_seed_by_name(name: str) -> CounselorAvailabilitySeed | None:
    target = (name or "").strip()
    for seed in COUNSELOR_AVAILABILITY_SEEDS:
        if seed.name == target:
            return seed
    return None


@dataclass
class _TimeWindowRule:
    start_time: time
    end_time: time


def _seed_rules_for_weekday(
    counselor_seed: CounselorAvailabilitySeed, weekday: int
) -> list[_TimeWindowRule]:
    rules: list[_TimeWindowRule] = []
    for slot in counselor_seed.slots:
        if weekday in slot.days:
            rules.append(
                _TimeWindowRule(
                    start_time=_parse_time(slot.start_time),
                    end_time=_parse_time(slot.end_time),
                )
            )
    return rules


def find_first_session1_slot_from_seeds(
    client_seed: ClientPreferenceSeed,
    counselor_name: str,
    *,
    start_date: date | None = None,
    weeks: int = 8,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    blocked_intervals: list[_TimeInterval] | None = None,
) -> datetime | None:
    """DB 없이 시드 데이터만으로 겹치는 첫 슬롯 (분석용)."""
    counselor_seed = _counselor_seed_by_name(counselor_name)
    if not counselor_seed:
        return None

    blocked = blocked_intervals or []
    today = timezone.localdate()
    cursor_date = start_date or (today + timedelta(days=1))
    end_date = cursor_date + timedelta(weeks=weeks)

    while cursor_date <= end_date:
        weekday = cursor_date.weekday()
        rules = _seed_rules_for_weekday(counselor_seed, weekday)
        if not rules:
            cursor_date += timedelta(days=1)
            continue

        day_candidates: list[datetime] = []
        for slot in client_seed.slots:
            day_candidates.extend(
                _iter_candidate_starts(
                    cursor_date,
                    slot,
                    rules,
                    duration_minutes=duration_minutes,
                )
            )

        for scheduled_at in sorted(day_candidates):
            if _global_slot_blocked(scheduled_at, duration_minutes, blocked):
                continue
            return timezone.localtime(scheduled_at)

        cursor_date += timedelta(days=1)

    return None


def find_first_session1_slot(
    counselor_id,
    client_seed: ClientPreferenceSeed,
    *,
    start_date: date | None = None,
    weeks: int = 8,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
    blocked_intervals: list[_TimeInterval] | None = None,
) -> datetime | None:
    """내담자 희망 × 상담사 DB 가용시간 겹치는 가장 빠른 슬롯."""
    tz = local_timezone()
    blocked = blocked_intervals or []
    today = timezone.localdate()
    cursor_date = start_date or (today + timedelta(days=1))
    end_date = cursor_date + timedelta(weeks=weeks)

    while cursor_date <= end_date:
        weekday = cursor_date.weekday()
        rules = list(_counselor_rules_for_weekday(counselor_id, weekday))
        if not rules:
            cursor_date += timedelta(days=1)
            continue

        day_candidates: list[datetime] = []
        for slot in client_seed.slots:
            day_candidates.extend(
                _iter_candidate_starts(
                    cursor_date,
                    slot,
                    rules,
                    duration_minutes=duration_minutes,
                )
            )

        for scheduled_at in sorted(day_candidates):
            if _global_slot_blocked(scheduled_at, duration_minutes, blocked):
                continue
            available, _msg = is_counselor_slot_available(
                counselor_id,
                scheduled_at,
                duration_minutes=duration_minutes,
                require_full_duration=True,
            )
            if not available:
                continue
            if _counselor_slot_taken(counselor_id, scheduled_at):
                continue
            return timezone.localtime(scheduled_at, tz)

        cursor_date += timedelta(days=1)

    return None


def _existing_session1(case: Case) -> Appointment | None:
    return (
        case.appointments.filter(session_number=1)
        .exclude(status__in=[AppointmentStatus.CANCELLED])
        .order_by("-created_at")
        .first()
    )


def _lookup_client_case(
    seed: ClientPreferenceSeed,
) -> tuple[User | None, Case | None, str | None]:
    """제외·스킵만 거르고, 사례 없음은 (None, None, None)으로 반환."""
    email = seed.email.strip().lower()
    if email in EXCLUDED_CLIENT_EMAILS or seed.counselor_name in EXCLUDED_COUNSELOR_NAMES:
        return None, None, "전효영·이수정 담당 - 제외"

    if seed.skip:
        return None, None, seed.skip_reason or "수동 조율 필요"

    client = User.objects.filter(
        email__iexact=email,
        role=UserRole.CLIENT,
    ).first()
    if not client:
        return None, None, None

    case = (
        Case.objects.filter(client=client, status=CaseStatus.ACTIVE)
        .select_related("counselor")
        .first()
    )
    if case and not case.counselor_id:
        return client, None, None

    if case:
        counselor_name = (case.counselor.name or "").strip()
        if counselor_name in EXCLUDED_COUNSELOR_NAMES:
            return None, None, "전효영·이수정 담당 - 제외"

    return client, case, None


def _resolve_client_and_case(seed: ClientPreferenceSeed) -> tuple[User | None, Case | None, str]:
    email = seed.email.strip().lower()
    if email in EXCLUDED_CLIENT_EMAILS or seed.counselor_name in EXCLUDED_COUNSELOR_NAMES:
        return None, None, "전효영·이수정 담당 - 제외"

    if seed.skip:
        return None, None, seed.skip_reason or "수동 조율 필요"

    client = User.objects.filter(
        email__iexact=email,
        role=UserRole.CLIENT,
    ).first()
    if not client:
        return None, None, "내담자 계정 없음"

    case = (
        Case.objects.filter(client=client, status=CaseStatus.ACTIVE)
        .select_related("counselor")
        .first()
    )
    if not case:
        return None, None, "ACTIVE 사례 없음"
    if not case.counselor_id:
        return None, None, "상담사 미배정"

    counselor_name = (case.counselor.name or "").strip()
    if counselor_name in EXCLUDED_COUNSELOR_NAMES:
        return None, None, "전효영·이수정 담당 - 제외"

    return client, case, ""


def _find_slot_for_seed(
    seed: ClientPreferenceSeed,
    case: Case | None,
    *,
    start_date: date | None,
    weeks: int,
    blocked_intervals: list[_TimeInterval],
) -> datetime | None:
    counselor_id = case.counselor_id if case else None
    slot = None
    if counselor_id:
        slot = find_first_session1_slot(
            counselor_id,
            seed,
            start_date=start_date,
            weeks=weeks,
            blocked_intervals=blocked_intervals,
        )
    if not slot:
        slot = find_first_session1_slot_from_seeds(
            seed,
            seed.counselor_name,
            start_date=start_date,
            weeks=weeks,
            blocked_intervals=blocked_intervals,
        )
    return slot


def analyze_session1_batch_no_overlap(
    *,
    target_emails: frozenset[str] | None = None,
    start_date: date | None = None,
    weeks: int = 8,
    include_existing_confirmed: bool = False,
    replace_existing: bool = False,
) -> list[ScheduleMatchResult]:
    """
    대상 내담자를 순차 배정 — Zoom 동시 1회(전역 시간 겹침 없음).
    """
    targets = target_emails or SESSION1_AUTO_MATCH_EMAILS
    seeds = [s for s in CLIENT_PREFERENCE_SEEDS if s.email.lower() in targets]
    results: list[ScheduleMatchResult] = []
    exclude_emails = targets if replace_existing else None
    blocked = _collect_global_blocked_intervals(
        exclude_session1_client_emails=exclude_emails,
    )

    for seed in seeds:
        _client, case, skip_reason = _lookup_client_case(seed)
        if skip_reason:
            results.append(
                ScheduleMatchResult(
                    seed.name,
                    seed.email,
                    seed.counselor_name,
                    None,
                    "skipped",
                    skip_reason,
                )
            )
            continue

        counselor_name = case.counselor.name if case and case.counselor else seed.counselor_name

        if case and not replace_existing:
            existing = _existing_session1(case)
            if existing and existing.status == AppointmentStatus.CONFIRMED:
                if include_existing_confirmed:
                    local_at = timezone.localtime(existing.scheduled_at)
                    blocked.append(
                        _normalize_interval(
                            local_at,
                            existing.duration_minutes or DEFAULT_APPOINTMENT_DURATION_MINUTES,
                        )
                    )
                results.append(
                    ScheduleMatchResult(
                        seed.name,
                        seed.email,
                        counselor_name,
                        timezone.localtime(existing.scheduled_at),
                        "already_confirmed",
                        "1회기 이미 확정",
                    )
                )
                continue

        slot = _find_slot_for_seed(
            seed,
            case,
            start_date=start_date,
            weeks=weeks,
            blocked_intervals=blocked,
        )
        if slot:
            detail = ""
            if not case:
                detail = "시간 겹침 확인 (ACTIVE 사례 없음)"
            blocked.append(
                _normalize_interval(slot, DEFAULT_APPOINTMENT_DURATION_MINUTES)
            )
            results.append(
                ScheduleMatchResult(
                    seed.name,
                    seed.email,
                    counselor_name,
                    slot,
                    "matched",
                    detail,
                )
            )
        else:
            results.append(
                ScheduleMatchResult(
                    seed.name,
                    seed.email,
                    counselor_name,
                    None,
                    "no_overlap",
                    "내담자·상담사 시간 또는 Zoom 전역(동일일 2시간 간격) 배정 불가",
                )
            )

    return results


def analyze_session1_matches(
    *,
    start_date: date | None = None,
    weeks: int = 8,
) -> list[ScheduleMatchResult]:
    """매칭 가능 여부만 분석 (DB 변경 없음)."""
    results: list[ScheduleMatchResult] = []

    for seed in CLIENT_PREFERENCE_SEEDS:
        _client, case, skip_reason = _lookup_client_case(seed)
        if skip_reason:
            results.append(
                ScheduleMatchResult(
                    seed.name,
                    seed.email,
                    seed.counselor_name,
                    None,
                    "skipped",
                    skip_reason,
                )
            )
            continue

        counselor_name = case.counselor.name if case and case.counselor else seed.counselor_name
        counselor_id = case.counselor_id if case else None

        if case:
            existing = _existing_session1(case)
            if existing and existing.status == AppointmentStatus.CONFIRMED:
                results.append(
                    ScheduleMatchResult(
                        seed.name,
                        seed.email,
                        counselor_name,
                        timezone.localtime(existing.scheduled_at),
                        "already_confirmed",
                        "1회기 이미 확정",
                    )
                )
                continue

        slot = None
        if counselor_id:
            slot = find_first_session1_slot(
                counselor_id,
                seed,
                start_date=start_date,
                weeks=weeks,
            )
        if not slot:
            slot = find_first_session1_slot_from_seeds(
                seed,
                seed.counselor_name,
                start_date=start_date,
                weeks=weeks,
            )

        if slot:
            detail = ""
            if not case:
                detail = "시간 겹침 확인 (ACTIVE 사례 없음)"
            results.append(
                ScheduleMatchResult(
                    seed.name,
                    seed.email,
                    counselor_name,
                    slot,
                    "matched",
                    detail,
                )
            )
        else:
            results.append(
                ScheduleMatchResult(
                    seed.name,
                    seed.email,
                    counselor_name,
                    None,
                    "no_overlap",
                    "내담자 희망 시간과 상담사 가용시간 겹침 없음",
                )
            )

    return results


@dataclass
class ApplySummary:
    matched: int = 0
    confirmed: int = 0
    skipped: int = 0
    no_overlap: int = 0
    errors: int = 0
    cleared: int = 0


def clear_session1_appointments(
    *,
    target_emails: frozenset[str] | None = None,
) -> int:
    """대상 내담자 1회기 예약(취소 제외) 및 Case Zoom URL 삭제."""
    targets = target_emails or SESSION1_AUTO_MATCH_EMAILS
    cleared = 0

    for seed in CLIENT_PREFERENCE_SEEDS:
        if seed.email.lower() not in targets:
            continue
        _client, case, skip_reason = _lookup_client_case(seed)
        if skip_reason or not case:
            continue

        apts = case.appointments.filter(session_number=1).exclude(
            status=AppointmentStatus.CANCELLED
        )
        count = apts.count()
        if count:
            apts.delete()
            cleared += count

        if case.zoom_meeting_url:
            case.zoom_meeting_url = ""
            case.save(update_fields=["zoom_meeting_url"])

    return cleared


def apply_session1_schedule(
    *,
    dry_run: bool = True,
    with_zoom: bool = False,
    start_date: date | None = None,
    weeks: int = 8,
    global_zoom_limit: bool = False,
    target_emails: frozenset[str] | None = None,
    replace_existing: bool = False,
) -> tuple[list[ScheduleMatchResult], ApplySummary]:
    """분석 후 PENDING 생성 및 (선택) 확정."""
    summary = ApplySummary()

    if replace_existing and not dry_run:
        with transaction.atomic():
            summary.cleared = clear_session1_appointments(target_emails=target_emails)

    if global_zoom_limit:
        results = analyze_session1_batch_no_overlap(
            target_emails=target_emails,
            start_date=start_date,
            weeks=weeks,
            replace_existing=replace_existing and dry_run,
        )
    else:
        results = analyze_session1_matches(start_date=start_date, weeks=weeks)

    blocked = _collect_global_blocked_intervals()

    for result in results:
        if result.status == "skipped":
            summary.skipped += 1
            continue
        if result.status == "already_confirmed":
            summary.skipped += 1
            continue
        if result.status == "no_overlap":
            summary.no_overlap += 1
            continue
        if result.status != "matched" or not result.scheduled_at:
            summary.errors += 1
            continue

        summary.matched += 1
        if dry_run:
            continue

        seed = next(
            s for s in CLIENT_PREFERENCE_SEEDS if s.email.lower() == result.client_email.lower()
        )
        client, case, skip_reason = _resolve_client_and_case(seed)
        if not client or not case:
            result.status = "error"
            result.detail = skip_reason or "ACTIVE 사례 없음"
            summary.errors += 1
            continue

        try:
            slot = _find_slot_for_seed(
                seed,
                case,
                start_date=start_date,
                weeks=weeks,
                blocked_intervals=blocked,
            )
            if not slot:
                result.status = "error"
                result.detail = "예약 가능 시간 없음 (가용·충돌 재검사)"
                summary.errors += 1
                continue

            appointment = ensure_pending_session_appointment(
                case=case,
                client=client,
                session_number=1,
                scheduled_at=slot,
                request_message="내담자 희망 시간 기반 자동 1회기 배정 (Zoom 전역 비겹침)",
                notify=False,
            )
            if with_zoom and case.counseling_method == CounselingMethod.REMOTE:
                confirm_appointment_with_zoom(appointment, notify=False)
            else:
                appointment.status = AppointmentStatus.CONFIRMED
                appointment.confirmed_at = timezone.now()
                appointment.save(
                    update_fields=["status", "confirmed_at", "updated_at"]
                )
            result.scheduled_at = timezone.localtime(slot)
            blocked.append(
                _normalize_interval(slot, DEFAULT_APPOINTMENT_DURATION_MINUTES)
            )
            summary.confirmed += 1
        except (AppointmentServiceError, Exception) as exc:
            result.status = "error"
            result.detail = str(exc)
            summary.errors += 1

    return results, summary


# 6/9(화) 확정분 → 6/15(월)부터 시작 (+6일)
SESSION1_JUNE15_SHIFT_DAYS = 6


@dataclass
class Session1ShiftResult:
    client_name: str
    client_email: str
    counselor_name: str
    old_at: datetime | None
    new_at: datetime | None
    status: str  # shifted | missing | skipped | error
    detail: str = ""


def shift_session1_confirmed_schedule(
    *,
    shift_days: int = SESSION1_JUNE15_SHIFT_DAYS,
    target_emails: frozenset[str] | None = None,
    dry_run: bool = True,
    skip_availability: bool = True,
    only_if_before: date | None = None,
) -> list[Session1ShiftResult]:
    """확정된 1회기 예약 일시를 shift_days만큼 미루고 Zoom 일정 갱신."""
    from apps.scheduling.services import (
        AppointmentServiceError,
        reschedule_confirmed_appointment,
    )

    targets = target_emails or SESSION1_AUTO_MATCH_EMAILS
    results: list[Session1ShiftResult] = []

    for seed in CLIENT_PREFERENCE_SEEDS:
        if seed.email.lower() not in targets:
            continue

        client, case, skip_reason = _resolve_client_and_case(seed)
        if skip_reason or not case:
            results.append(
                Session1ShiftResult(
                    seed.name,
                    seed.email,
                    seed.counselor_name,
                    None,
                    None,
                    "missing",
                    skip_reason or "ACTIVE 사례 없음",
                )
            )
            continue

        appointment = _existing_session1(case)
        if not appointment:
            results.append(
                Session1ShiftResult(
                    seed.name,
                    seed.email,
                    (case.counselor.name if case.counselor else seed.counselor_name),
                    None,
                    None,
                    "missing",
                    "1회기 예약 없음",
                )
            )
            continue

        if appointment.status != AppointmentStatus.CONFIRMED:
            results.append(
                Session1ShiftResult(
                    seed.name,
                    seed.email,
                    (case.counselor.name if case.counselor else seed.counselor_name),
                    timezone.localtime(appointment.scheduled_at),
                    None,
                    "skipped",
                    f"상태 {appointment.get_status_display()} — 확정만 변경",
                )
            )
            continue

        old_at = timezone.localtime(appointment.scheduled_at)
        if only_if_before and old_at.date() >= only_if_before:
            results.append(
                Session1ShiftResult(
                    seed.name,
                    seed.email,
                    (case.counselor.name if case.counselor else seed.counselor_name),
                    old_at,
                    old_at,
                    "skipped",
                    f"이미 {only_if_before.isoformat()} 이후 일정",
                )
            )
            continue

        new_at = old_at + timedelta(days=shift_days)

        if dry_run:
            results.append(
                Session1ShiftResult(
                    seed.name,
                    seed.email,
                    (case.counselor.name if case.counselor else seed.counselor_name),
                    old_at,
                    new_at,
                    "shifted",
                    f"+{shift_days}일",
                )
            )
            continue

        try:
            reschedule_confirmed_appointment(
                appointment,
                new_scheduled_at=new_at,
                skip_availability=skip_availability,
            )
            results.append(
                Session1ShiftResult(
                    seed.name,
                    seed.email,
                    (case.counselor.name if case.counselor else seed.counselor_name),
                    old_at,
                    timezone.localtime(new_at),
                    "shifted",
                    f"+{shift_days}일",
                )
            )
        except AppointmentServiceError as exc:
            results.append(
                Session1ShiftResult(
                    seed.name,
                    seed.email,
                    (case.counselor.name if case.counselor else seed.counselor_name),
                    old_at,
                    new_at,
                    "error",
                    str(exc),
                )
            )

    return results
