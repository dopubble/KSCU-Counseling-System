from django.contrib import admin, messages
from django.db import transaction
from django.http import HttpResponseRedirect
from django.urls import reverse

from apps.counseling.models import CounselingMethod

from .models import (
    Appointment,
    AppointmentStatus,
    AvailabilityException,
    CounselorAvailability,
    RemoteZoomSchedulingSettings,
)
from .services import AppointmentServiceError, confirm_appointment_with_zoom
from .utils import ZoomAPIError, ZoomNotConfiguredError


def _admin_intends_remote_confirm(obj: Appointment, *, change: bool) -> bool:
    """Admin에서 비대면 확정 처리(Zoom 발급)가 필요한 저장인지."""
    if obj.status != AppointmentStatus.CONFIRMED:
        return False
    if not obj.case_id:
        return False
    if obj.case.counseling_method != CounselingMethod.REMOTE:
        return False
    if not change:
        return True
    prior_status = (
        Appointment.objects.filter(pk=obj.pk)
        .values_list("status", flat=True)
        .first()
    )
    return prior_status != AppointmentStatus.CONFIRMED


@admin.register(RemoteZoomSchedulingSettings)
class RemoteZoomSchedulingSettingsAdmin(admin.ModelAdmin):
    list_display = ("simultaneous_session_capacity", "updated_at")
    fields = ("simultaneous_session_capacity", "updated_at")
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        if RemoteZoomSchedulingSettings.objects.filter(
            pk=RemoteZoomSchedulingSettings.SETTINGS_PK
        ).exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CounselorAvailability)
class CounselorAvailabilityAdmin(admin.ModelAdmin):
    list_display = (
        "counselor",
        "is_recurring",
        "specific_date",
        "day_of_week",
        "start_time",
        "end_time",
        "is_available",
        "is_active",
    )
    list_filter = ("is_recurring", "is_available", "is_active", "day_of_week")


@admin.register(AvailabilityException)
class AvailabilityExceptionAdmin(admin.ModelAdmin):
    list_display = ("counselor", "date", "is_available", "note")
    list_filter = ("is_available",)


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        "case",
        "session_number",
        "client",
        "counselor",
        "scheduled_at",
        "status",
    )
    list_filter = ("status",)
    search_fields = (
        "case__case_number",
        "client__name",
        "counselor__name",
    )
    raw_id_fields = ("case", "client", "counselor")
    ordering = ("-scheduled_at",)
    date_hierarchy = "scheduled_at"

    def save_model(self, request, obj, form, change):
        if not _admin_intends_remote_confirm(obj, change=change):
            super().save_model(request, obj, form, change)
            return

        try:
            with transaction.atomic():
                obj.status = AppointmentStatus.PENDING
                super().save_model(request, obj, form, change)
                confirm_appointment_with_zoom(obj, notify=True)
        except (
            AppointmentServiceError,
            ZoomAPIError,
            ZoomNotConfiguredError,
        ) as exc:
            messages.error(
                request,
                f"비대면 예약 확정 및 Zoom 연동에 실패했습니다. 변경 사항은 저장되지 않았습니다. ({exc})",
            )
            request._appointment_admin_zoom_save_failed = True

    def _zoom_save_failed(self, request) -> bool:
        return bool(getattr(request, "_appointment_admin_zoom_save_failed", False))

    def log_addition(self, request, object, message):
        if self._zoom_save_failed(request):
            return
        super().log_addition(request, object, message)

    def log_change(self, request, object, message):
        if self._zoom_save_failed(request):
            return
        super().log_change(request, object, message)

    def response_add(self, request, obj, post_url_continue=None):
        if self._zoom_save_failed(request):
            return HttpResponseRedirect(
                reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_add")
            )
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if self._zoom_save_failed(request):
            return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)
