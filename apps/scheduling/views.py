from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

import logging

from apps.accounts.decorators import counselor_required
from apps.accounts.models import UserRole

logger = logging.getLogger(__name__)

from apps.reports.appointment_calendar import build_calendar_events, parse_calendar_bound
from apps.counseling.models import Case, CounselingMethod
from .booking_slots import (
    build_available_dates_for_month,
    build_booking_slots_for_date,
    month_date_bounds,
)
from .constants import DEFAULT_APPOINTMENT_DURATION_MINUTES, IN_PERSON_ROOM_CAPACITY
from .display import group_availabilities_for_display
from .forms import (
    AppointmentScheduleForm,
    CounselorAvailabilityForm,
    SETTING_DAILY,
    SETTING_RECURRING,
)
from .in_person_room_capacity import (
    get_in_person_busy_intervals,
    in_person_room_capacity_limit,
)
from .models import Appointment, AppointmentStatus, CounselorAvailability
from .remote_zoom_capacity import (
    get_remote_zoom_busy_intervals,
    remote_zoom_capacity_limit,
)
from .schedule_picker import build_schedule_picker_context
from .services import (
    AppointmentServiceError,
    confirm_appointment_with_zoom,
    update_pending_appointment,
)
from .utils import ZoomAPIError, ZoomNotConfiguredError, is_zoom_configured


def _counselor_availabilities(user):
    return CounselorAvailability.objects.filter(counselor=user).order_by(
        "-is_recurring", "specific_date", "day_of_week", "start_time"
    )


def _counselor_availability_groups(user):
    return group_availabilities_for_display(_counselor_availabilities(user))


@counselor_required
def availability_list(request):
    return render(
        request,
        "counselor/availability.html",
        {
            "availability_groups": _counselor_availability_groups(request.user),
            "form": CounselorAvailabilityForm(),
        },
    )


@counselor_required
def availability_create(request):
    if request.method != "POST":
        return redirect("scheduling:availability_list")

    form = CounselorAvailabilityForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        setting_type = data["setting_type"]
        is_available = data["is_available"] == "1"

        if setting_type == SETTING_DAILY:
            weekdays = data["weekdays"]
            for day in weekdays:
                CounselorAvailability.objects.create(
                    counselor=request.user,
                    is_recurring=True,
                    specific_date=None,
                    day_of_week=day,
                    start_time=data["start_time"],
                    end_time=data["end_time"],
                    is_available=is_available,
                )
            messages.success(
                request,
                f"가용 시간이 월~금요일({len(weekdays)}개)에 등록되었습니다.",
            )
        else:
            is_recurring = setting_type == SETTING_RECURRING
            CounselorAvailability.objects.create(
                counselor=request.user,
                is_recurring=is_recurring,
                specific_date=None if is_recurring else data["specific_date"],
                day_of_week=data["day_of_week"] if is_recurring else None,
                start_time=data["start_time"],
                end_time=data["end_time"],
                is_available=is_available,
            )
            messages.success(request, "가용 시간이 등록되었습니다.")
        return redirect("scheduling:availability_list")

    messages.error(request, "입력 내용을 확인해 주세요.")
    return render(
        request,
        "counselor/availability.html",
        {
            "availability_groups": _counselor_availability_groups(request.user),
            "form": form,
            "open_availability_modal": True,
        },
    )


@counselor_required
@require_POST
def availability_delete(request, pk):
    ids = request.POST.getlist("availability_ids")
    if not ids:
        ids = [str(pk)]
    deleted, _ = CounselorAvailability.objects.filter(
        pk__in=ids,
        counselor=request.user,
    ).delete()
    if deleted:
        if len(ids) > 1:
            messages.success(request, f"가용 시간 {deleted}건이 삭제되었습니다.")
        else:
            messages.success(request, "가용 시간이 삭제되었습니다.")
    else:
        messages.error(request, "삭제할 가용 시간을 찾을 수 없습니다.")
    return redirect("scheduling:availability_list")


def _get_counselor_pending_appointment(request, pk):
    appointment = get_object_or_404(
        Appointment.objects.select_related("case", "case__client", "case__application", "client"),
        pk=pk,
        counselor=request.user,
        status=AppointmentStatus.PENDING,
    )
    return appointment


@counselor_required
def appointment_manage(request, pk):
    """대기 중 예약 — 시간 수정 또는 확정(+Zoom)"""
    appointment = _get_counselor_pending_appointment(request, pk)
    case = appointment.case

    if request.method == "POST":
        form = AppointmentScheduleForm(
            request.POST,
            instance=appointment,
            counselor_label=True,
            calendar_picker=True,
        )
        action = request.POST.get("action", "confirm")

        if not form.is_valid():
            messages.error(request, "입력 내용을 확인해 주세요.")
        elif action == "update":
            try:
                update_pending_appointment(
                    appointment,
                    scheduled_at=form.cleaned_data["scheduled_at"],
                    duration_minutes=form.cleaned_data["duration_minutes"],
                )
                messages.success(request, "희망 시간이 수정되었습니다. 내담자 이메일로 안내가 발송됩니다.")
                return redirect("counselor:appointment_manage", pk=pk)
            except AppointmentServiceError as exc:
                messages.error(request, str(exc))
        elif action == "confirm":
            case = appointment.case
            if case.counseling_method == CounselingMethod.REMOTE and not is_zoom_configured():
                messages.error(
                    request,
                    "Zoom API가 설정되지 않아 비대면 예약을 확정할 수 없습니다. .env 설정을 확인해 주세요.",
                )
            else:
                try:
                    update_pending_appointment(
                        appointment,
                        scheduled_at=form.cleaned_data["scheduled_at"],
                        duration_minutes=form.cleaned_data["duration_minutes"],
                        notify_client=False,
                    )
                    appointment.refresh_from_db()
                    _appointment, zoom = confirm_appointment_with_zoom(appointment)
                    case.refresh_from_db()
                    messages.success(
                        request,
                        f"상담이 확정되었습니다. ({appointment.scheduled_at:%Y-%m-%d %H:%M})",
                    )
                    if zoom and case.zoom_meeting_url:
                        messages.success(
                            request,
                            "Zoom 회의가 성공적으로 생성되었습니다.",
                        )
                    return redirect("counselor:case_detail", pk=case.pk)
                except ZoomNotConfiguredError as exc:
                    messages.error(request, str(exc))
                except ZoomAPIError as exc:
                    messages.error(request, str(exc))
                except AppointmentServiceError as exc:
                    messages.error(request, str(exc))
                except IntegrityError:
                    messages.error(
                        request,
                        "선택한 시간에 이미 다른 확정 예약이 있습니다.",
                    )
                except ValidationError as exc:
                    messages.error(
                        request,
                        exc.messages[0] if getattr(exc, "messages", None) else str(exc),
                    )
                except Exception:
                    logger.exception(
                        "Appointment confirm failed (appointment=%s, case=%s)",
                        appointment.pk,
                        case.pk,
                    )
                    messages.error(
                        request,
                        "예약 확정 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                    )
        else:
            messages.error(request, "잘못된 요청입니다.")
    else:
        form = AppointmentScheduleForm(
            instance=appointment,
            counselor_label=True,
            calendar_picker=True,
        )

    return render(
        request,
        "counselor/appointment_manage.html",
        {
            "appointment": appointment,
            "case": case,
            "form": form,
            "zoom_configured": is_zoom_configured(),
            **build_schedule_picker_context(case, appointment=appointment),
        },
    )


@login_required
@require_GET
def remote_zoom_busy_intervals(request):
    """비대면 확정 예약 구간 — 달력 만석 표시용."""
    range_start = parse_calendar_bound(request.GET.get("start", ""))
    range_end = parse_calendar_bound(request.GET.get("end", ""))
    if range_start is None or range_end is None:
        return JsonResponse(
            {"error": "start, end 쿼리가 필요합니다."},
            status=400,
        )

    exclude_id = (request.GET.get("exclude_appointment_id") or "").strip() or None
    intervals = get_remote_zoom_busy_intervals(
        range_start,
        range_end,
        exclude_appointment_id=exclude_id,
    )
    return JsonResponse(
        {
            "capacity": remote_zoom_capacity_limit(),
            "default_duration_minutes": DEFAULT_APPOINTMENT_DURATION_MINUTES,
            "intervals": intervals,
        }
    )


def _get_booking_case_for_user(request, case_id: str) -> Case:
    case = get_object_or_404(
        Case.objects.select_related("counselor", "client", "application"),
        pk=case_id,
    )
    user = request.user
    if user.is_counselor and case.counselor_id == user.pk:
        return case
    if getattr(user, "role", None) == UserRole.CLIENT and case.client_id == user.pk:
        return case
    raise PermissionDenied


@login_required
@require_GET
def booking_slots(request):
    """날짜별 예약 슬롯 상태 — 내담자·상담사 공통."""
    case_id = (request.GET.get("case_id") or "").strip()
    date_text = (request.GET.get("date") or "").strip()
    if not case_id or not date_text:
        return JsonResponse(
            {"error": "case_id, date 쿼리가 필요합니다."},
            status=400,
        )

    try:
        from datetime import date as date_cls

        on_date = date_cls.fromisoformat(date_text)
    except ValueError:
        return JsonResponse({"error": "date는 YYYY-MM-DD 형식이어야 합니다."}, status=400)

    case = _get_booking_case_for_user(request, case_id)
    duration_raw = (request.GET.get("duration_minutes") or "").strip()
    duration = DEFAULT_APPOINTMENT_DURATION_MINUTES
    if duration_raw.isdigit():
        duration = max(1, int(duration_raw))

    exclude_id = (request.GET.get("exclude_appointment_id") or "").strip() or None
    require_full = request.user.is_counselor

    slots = build_booking_slots_for_date(
        case=case,
        on_date=on_date,
        duration_minutes=duration,
        exclude_appointment_id=exclude_id,
        require_full_duration=require_full,
    )
    return JsonResponse(
        {
            "date": on_date.isoformat(),
            "counseling_method": case.counseling_method,
            "duration_minutes": duration,
            "zoom_capacity": remote_zoom_capacity_limit(),
            "room_capacity": in_person_room_capacity_limit(),
            "slots": [slot.to_dict() for slot in slots],
        }
    )


@login_required
@require_GET
def booking_available_dates(request):
    """월간 달력 — 예약 가능한 날짜 목록."""
    case_id = (request.GET.get("case_id") or "").strip()
    month_text = (request.GET.get("month") or "").strip()
    if not case_id or not month_text:
        return JsonResponse(
            {"error": "case_id, month 쿼리가 필요합니다."},
            status=400,
        )

    try:
        parts = month_text.split("-")
        year, month = int(parts[0]), int(parts[1])
        month_start, month_end = month_date_bounds(year, month)
    except (ValueError, IndexError):
        return JsonResponse(
            {"error": "month는 YYYY-MM 형식이어야 합니다."},
            status=400,
        )

    case = _get_booking_case_for_user(request, case_id)
    exclude_id = (request.GET.get("exclude_appointment_id") or "").strip() or None
    require_full = request.user.is_counselor

    available_dates = build_available_dates_for_month(
        case=case,
        month_start=month_start,
        month_end=month_end,
        exclude_appointment_id=exclude_id,
        require_full_duration=require_full,
    )

    return JsonResponse({"month": month_text, "available_dates": available_dates})


@counselor_required
@require_GET
def counselor_calendar_events(request):
    """상담사 본인 확정 예약 — FullCalendar 이벤트."""
    start = parse_calendar_bound(request.GET.get("start", ""))
    end = parse_calendar_bound(request.GET.get("end", ""))
    if start is None or end is None:
        return JsonResponse(
            {"error": "start, end 쿼리가 필요합니다."},
            status=400,
        )
    events = build_calendar_events(
        start=start,
        end=end,
        counselor_id=request.user.pk,
    )
    return JsonResponse(events, safe=False)


@login_required
@require_GET
def in_person_busy_intervals(request):
    """대면 확정 예약 구간 — 달력 만석 표시용 (Flatpickr 호환)."""
    range_start = parse_calendar_bound(request.GET.get("start", ""))
    range_end = parse_calendar_bound(request.GET.get("end", ""))
    if range_start is None or range_end is None:
        return JsonResponse(
            {"error": "start, end 쿼리가 필요합니다."},
            status=400,
        )

    exclude_id = (request.GET.get("exclude_appointment_id") or "").strip() or None
    intervals = get_in_person_busy_intervals(
        range_start,
        range_end,
        exclude_appointment_id=exclude_id,
    )
    return JsonResponse(
        {
            "capacity": in_person_room_capacity_limit(),
            "default_duration_minutes": DEFAULT_APPOINTMENT_DURATION_MINUTES,
            "intervals": intervals,
        }
    )
