"""관리자 테이블 컬럼 정렬 (쿼리스트·리스트 공통)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from django.db.models import QuerySet
from django.http import HttpRequest

SortDirection = str  # "asc" | "desc"


@dataclass(frozen=True)
class SortState:
    field: str
    direction: SortDirection

    @property
    def is_asc(self) -> bool:
        return self.direction == "asc"


@dataclass(frozen=True)
class SortFieldSpec:
    key: str
    orm: str | None = None
    python_key: Callable[[Any], Any] | None = None


def parse_sort(
    request: HttpRequest,
    *,
    allowed: Sequence[str],
    default_field: str,
    default_direction: SortDirection = "desc",
) -> SortState:
    field = (request.GET.get("sort") or default_field).strip()
    direction = (request.GET.get("dir") or default_direction).strip().lower()
    if field not in allowed:
        field = default_field
    if direction not in {"asc", "desc"}:
        direction = default_direction
    return SortState(field=field, direction=direction)


def build_sort_query(request: HttpRequest, field: str, *, base_params: dict | None = None) -> str:
    """다음 클릭 시 적용할 sort/dir 쿼리 문자열 (? 포함)."""
    params = base_params.copy() if base_params else request.GET.copy()
    for key in ("sort", "dir"):
        if key in request.GET:
            params[key] = request.GET[key]

    current_field = params.get("sort")
    current_dir = params.get("dir", "asc")
    if current_field == field:
        params["dir"] = "desc" if current_dir == "asc" else "asc"
    else:
        params["sort"] = field
        params["dir"] = "asc"

    encoded = params.urlencode()
    return f"?{encoded}" if encoded else ""


def _order_expression(spec: SortFieldSpec, direction: SortDirection) -> str:
    assert spec.orm
    prefix = "-" if direction == "desc" else ""
    return f"{prefix}{spec.orm}"


def sort_queryset(
    queryset: QuerySet,
    sort: SortState,
    specs: Sequence[SortFieldSpec],
) -> QuerySet | list:
    spec_map = {spec.key: spec for spec in specs}
    spec = spec_map.get(sort.field)
    if not spec:
        return queryset

    if spec.orm:
        return queryset.order_by(_order_expression(spec, sort.direction))

    if spec.python_key:
        items = list(queryset)
        return sorted(items, key=spec.python_key, reverse=not sort.is_asc)

    return queryset


def sort_list(
    items: Iterable[Any],
    sort: SortState,
    specs: Sequence[SortFieldSpec],
) -> list[Any]:
    spec_map = {spec.key: spec for spec in specs}
    spec = spec_map.get(sort.field)
    materialized = list(items)
    if not spec or not spec.python_key:
        return materialized
    return sorted(materialized, key=spec.python_key, reverse=not sort.is_asc)


# --- 상담 통합 관리 탭별 정렬 필드 ---

WAITING_SORT_SPECS: tuple[SortFieldSpec, ...] = (
    SortFieldSpec("client", orm="client__name"),
    SortFieldSpec(
        "student_id",
        python_key=lambda app: ((app.preferred_schedule or {}).get("student_id") or "").lower(),
    ),
    SortFieldSpec(
        "counseling_type",
        python_key=lambda app: (app.counseling_type or "").lower(),
    ),
    SortFieldSpec(
        "preferred_at",
        python_key=lambda app: (
            (app.preferred_schedule or {}).get("preferred_date") or "",
            (app.preferred_schedule or {}).get("preferred_time") or "",
        ),
    ),
    SortFieldSpec("status", orm="status"),
    SortFieldSpec("counselor", orm="case__counselor__name"),
    SortFieldSpec("created_at", orm="created_at"),
)

ACTIVE_CASE_SORT_SPECS: tuple[SortFieldSpec, ...] = (
    SortFieldSpec("case_number", orm="case_number"),
    SortFieldSpec("client", orm="client__name"),
    SortFieldSpec(
        "counseling_type",
        python_key=lambda case: (case.application.counseling_type or "").lower(),
    ),
    SortFieldSpec("counselor", orm="counselor__name"),
    SortFieldSpec("remaining_sessions", orm="remaining_sessions"),
    SortFieldSpec("status", orm="status"),
    SortFieldSpec("app_status", orm="application__status"),
    SortFieldSpec("opened_at", orm="opened_at"),
)

CLOSED_CASE_SORT_SPECS: tuple[SortFieldSpec, ...] = (
    SortFieldSpec("case_number", orm="case_number"),
    SortFieldSpec("client", orm="client__name"),
    SortFieldSpec(
        "counseling_type",
        python_key=lambda case: (case.application.counseling_type or "").lower(),
    ),
    SortFieldSpec("counselor", orm="counselor__name"),
    SortFieldSpec("status", orm="status"),
    SortFieldSpec("app_status", orm="application__status"),
    SortFieldSpec("day_of_cancel_count", orm="day_of_cancel_count"),
    SortFieldSpec("closed_at", orm="closed_at"),
)

CANCEL_PENDING_SORT_SPECS: tuple[SortFieldSpec, ...] = (
    SortFieldSpec("case_number", orm="case__case_number"),
    SortFieldSpec("client", orm="client__name"),
    SortFieldSpec("counselor", orm="counselor__name"),
    SortFieldSpec("scheduled_at", orm="scheduled_at"),
    SortFieldSpec("cancel_requested_at", orm="cancel_requested_at"),
    SortFieldSpec("cancel_reason", orm="cancel_reason"),
)

MATCHING_SORT_SPECS: tuple[SortFieldSpec, ...] = (
    SortFieldSpec("client", orm="client__name"),
    SortFieldSpec(
        "counseling_type",
        python_key=lambda app: (app.counseling_type or "").lower(),
    ),
    SortFieldSpec("status", orm="status"),
    SortFieldSpec("counselor", orm="case__counselor__name"),
    SortFieldSpec("case_number", orm="case__case_number"),
    SortFieldSpec("created_at", orm="created_at"),
)

TAB_SORT_DEFAULTS: dict[str, tuple[str, SortDirection]] = {
    "waiting": ("created_at", "desc"),
    "active": ("opened_at", "desc"),
    "closed": ("closed_at", "desc"),
}

CANCEL_PENDING_DEFAULT: tuple[str, SortDirection] = ("cancel_requested_at", "desc")
MATCHING_DEFAULT: tuple[str, SortDirection] = ("created_at", "desc")


def allowed_sort_keys(specs: Sequence[SortFieldSpec]) -> tuple[str, ...]:
    return tuple(spec.key for spec in specs)
