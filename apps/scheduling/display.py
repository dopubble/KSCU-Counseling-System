"""가용 시간 목록 표시용 그룹핑."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from .models import CounselorAvailability

DAY_SHORT = ("월", "화", "수", "목", "금", "토", "일")
WEEKDAY_MON_FRI = frozenset({0, 1, 2, 3, 4})


@dataclass
class AvailabilityDisplayGroup:
    schedule_label: str
    start_time: time
    end_time: time
    is_available: bool
    is_active: bool
    items: list[CounselorAvailability]

    @property
    def is_group(self) -> bool:
        return len(self.items) > 1


def _is_consecutive(days: list[int]) -> bool:
    return bool(days) and days[-1] - days[0] == len(days) - 1


def format_recurring_days_label(days: set[int]) -> str:
    if not days:
        return "매주"

    if days == WEEKDAY_MON_FRI:
        return "매주 월~금"

    sorted_days = sorted(days)
    if len(sorted_days) == 1:
        day = sorted_days[0]
        if 0 <= day < len(CounselorAvailability._DAY_LABELS):
            return f"매주 {CounselorAvailability._DAY_LABELS[day]}"
        return f"매주 {day}"

    if _is_consecutive(sorted_days):
        return f"매주 {DAY_SHORT[sorted_days[0]]}~{DAY_SHORT[sorted_days[-1]]}"

    return "매주 " + ", ".join(DAY_SHORT[day] for day in sorted_days)


def group_availabilities_for_display(
    availabilities,
) -> list[AvailabilityDisplayGroup]:
    """동일 시간·상태의 반복 일정을 묶어 표시 (예: 월~금 → 매주 월~금)."""
    items = list(availabilities)
    recurring: list[CounselorAvailability] = []
    others: list[CounselorAvailability] = []

    for av in items:
        if av.is_recurring and av.day_of_week is not None:
            recurring.append(av)
        else:
            others.append(av)

    grouped: dict[tuple, list[CounselorAvailability]] = {}
    for av in recurring:
        key = (av.start_time, av.end_time, av.is_available, av.is_active, av.slot_duration)
        grouped.setdefault(key, []).append(av)

    display_groups: list[AvailabilityDisplayGroup] = []

    for av_list in grouped.values():
        av_list.sort(key=lambda av: av.day_of_week or 0)
        days = {av.day_of_week for av in av_list if av.day_of_week is not None}
        if len(days) == len(av_list) and len(av_list) > 1:
            display_groups.append(
                AvailabilityDisplayGroup(
                    schedule_label=format_recurring_days_label(days),
                    start_time=av_list[0].start_time,
                    end_time=av_list[0].end_time,
                    is_available=av_list[0].is_available,
                    is_active=av_list[0].is_active,
                    items=av_list,
                )
            )
        else:
            for av in av_list:
                display_groups.append(
                    AvailabilityDisplayGroup(
                        schedule_label=av.schedule_label,
                        start_time=av.start_time,
                        end_time=av.end_time,
                        is_available=av.is_available,
                        is_active=av.is_active,
                        items=[av],
                    )
                )

    for av in others:
        display_groups.append(
            AvailabilityDisplayGroup(
                schedule_label=av.schedule_label,
                start_time=av.start_time,
                end_time=av.end_time,
                is_available=av.is_available,
                is_active=av.is_active,
                items=[av],
            )
        )

    display_groups.sort(
        key=lambda group: (
            0 if group.items[0].is_recurring else 1,
            group.items[0].specific_date or group.items[0].day_of_week or 0,
            group.start_time,
        )
    )
    return display_groups
