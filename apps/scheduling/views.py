from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.decorators import counselor_required

from .forms import AppointmentScheduleForm, CounselorAvailabilityForm, SETTING_RECURRING
from .models import Appointment, AppointmentStatus, CounselorAvailability
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


@counselor_required
def availability_list(request):
    availabilities = _counselor_availabilities(request.user)
    return render(
        request,
        "counselor/availability.html",
        {
            "availabilities": availabilities,
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
        is_recurring = data["setting_type"] == SETTING_RECURRING
        CounselorAvailability.objects.create(
            counselor=request.user,
            is_recurring=is_recurring,
            specific_date=None if is_recurring else data["specific_date"],
            day_of_week=data["day_of_week"] if is_recurring else None,
            start_time=data["start_time"],
            end_time=data["end_time"],
            is_available=data["is_available"] == "1",
        )
        messages.success(request, "가용 시간이 등록되었습니다.")
        return redirect("scheduling:availability_list")

    messages.error(request, "입력 내용을 확인해 주세요.")
    return render(
        request,
        "counselor/availability.html",
        {
            "availabilities": _counselor_availabilities(request.user),
            "form": form,
            "open_availability_modal": True,
        },
    )


@counselor_required
@require_POST
def availability_delete(request, pk):
    availability = get_object_or_404(
        CounselorAvailability,
        pk=pk,
        counselor=request.user,
    )
    availability.delete()
    messages.success(request, "가용 시간이 삭제되었습니다.")
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
                messages.success(request, "희망 시간이 수정되었습니다. 내담자에게 안내해 주세요.")
                return redirect("counselor:appointment_manage", pk=pk)
            except AppointmentServiceError as exc:
                messages.error(request, str(exc))
        elif action == "confirm":
            if not is_zoom_configured():
                messages.error(
                    request,
                    "Zoom API가 설정되지 않아 확정할 수 없습니다. .env 설정을 확인해 주세요.",
                )
            else:
                try:
                    update_pending_appointment(
                        appointment,
                        scheduled_at=form.cleaned_data["scheduled_at"],
                        duration_minutes=form.cleaned_data["duration_minutes"],
                    )
                    appointment.refresh_from_db()
                    _appointment, zoom = confirm_appointment_with_zoom(appointment)
                    case.refresh_from_db()
                    messages.success(
                        request,
                        f"상담이 확정되었습니다. ({appointment.scheduled_at:%Y-%m-%d %H:%M})",
                    )
                    if case.zoom_meeting_url:
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
                except Exception:
                    messages.error(
                        request,
                        "예약 확정 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                    )
        else:
            messages.error(request, "잘못된 요청입니다.")
    else:
        form = AppointmentScheduleForm(instance=appointment, counselor_label=True)

    return render(
        request,
        "counselor/appointment_manage.html",
        {
            "appointment": appointment,
            "case": case,
            "form": form,
            "zoom_configured": is_zoom_configured(),
        },
    )
