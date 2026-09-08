from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.counseling.admin_lock import RecordsSubmittedLockMixin
from apps.counseling.models import Case, CounselingMethod
from apps.counseling.services import (
    RECORDS_LOCKED_MESSAGE,
    case_records_are_locked,
    records_lock_case_for_obj,
)
from apps.scheduling.zoom_hosts import (
    normalize_licensed_user_emails_text,
    parse_licensed_user_email_lines,
)
from apps.scheduling.zoom_links import appointment_zoom_link_is_locked
from apps.sessions_app.models import ZoomMeeting

from .models import (
    Appointment,
    AppointmentStatus,
    AvailabilityException,
    CounselorAvailability,
    RemoteZoomSchedulingSettings,
)
from .services import AppointmentServiceError, confirm_appointment_with_zoom
from .utils import ZoomAPIError, ZoomNotConfiguredError, verify_zoom_host_emails


def _case_is_remote(case_id) -> bool:
    if not case_id:
        return False
    method = (
        Case.objects.filter(pk=case_id)
        .values_list("counseling_method", flat=True)
        .first()
    )
    return method == CounselingMethod.REMOTE


def _appointment_lacks_zoom(appointment: Appointment) -> bool:
    if not appointment.pk:
        return True
    zoom = (
        ZoomMeeting.objects.filter(appointment_id=appointment.pk)
        .only("join_url", "zoom_meeting_id")
        .first()
    )
    if zoom is None:
        return True
    appointment.zoom_meeting = zoom
    return not appointment_zoom_link_is_locked(appointment)


def _previous_status_for_admin_save(
    *,
    obj: Appointment,
    change: bool,
    form,
) -> str | None:
    if not change:
        return None
    if form is not None:
        initial = getattr(form, "initial", None) or {}
        if "status" in initial:
            return initial.get("status")
    if obj.pk:
        return (
            Appointment.objects.filter(pk=obj.pk)
            .values_list("status", flat=True)
            .first()
        )
    return None


def _admin_intends_remote_confirm(
    obj: Appointment,
    *,
    change: bool,
    form=None,
) -> bool:
    """
    Admin에서 비대면 확정 + Zoom 발급이 필요한 저장인지.

    - 신규(CONFIRMED) 또는 PENDING→CONFIRMED 전환: confirm_appointment_with_zoom
    - 이미 CONFIRMED인데 ZoomMeeting이 없는 건(이전 패스로 깨진 상태): 재발급
    """
    if obj.status != AppointmentStatus.CONFIRMED:
        return False
    if not _case_is_remote(obj.case_id):
        return False

    if not change:
        return True

    previous_status = _previous_status_for_admin_save(
        obj=obj,
        change=change,
        form=form,
    )
    if previous_status != AppointmentStatus.CONFIRMED:
        return True

    return _appointment_lacks_zoom(obj)


class RemoteZoomSchedulingSettingsForm(forms.ModelForm):
    class Meta:
        model = RemoteZoomSchedulingSettings
        fields = (
            "licensed_user_emails",
            "simultaneous_session_capacity",
        )
        widgets = {
            "licensed_user_emails": forms.Textarea(
                attrs={
                    "rows": 6,
                    "cols": 48,
                    "placeholder": "host1@example.com\nhost2@example.com",
                }
            ),
        }

    def clean_licensed_user_emails(self):
        raw = self.cleaned_data.get("licensed_user_emails") or ""
        emails, parse_errors = parse_licensed_user_email_lines(raw)
        if parse_errors:
            raise forms.ValidationError(parse_errors)
        if not emails:
            return ""
        try:
            ok, messages_out = verify_zoom_host_emails(emails)
        except (ZoomAPIError, ZoomNotConfiguredError) as exc:
            raise forms.ValidationError(str(exc)) from exc
        if not ok:
            raise forms.ValidationError(messages_out)
        return normalize_licensed_user_emails_text(emails)


@admin.register(RemoteZoomSchedulingSettings)
class RemoteZoomSchedulingSettingsAdmin(admin.ModelAdmin):
    form = RemoteZoomSchedulingSettingsForm
    change_form_template = "admin/scheduling/remotezoomschedulingsettings/change_form.html"
    list_display = ("simultaneous_session_capacity", "host_preview", "last_verified_at", "updated_at")
    fields = (
        "licensed_user_emails",
        "simultaneous_session_capacity",
        "last_verified_at",
        "updated_by",
        "updated_at",
    )
    readonly_fields = ("last_verified_at", "updated_by", "updated_at")

    def host_preview(self, obj):
        emails, _errors = parse_licensed_user_email_lines(obj.licensed_user_emails)
        if not emails:
            return "환경변수/기본값"
        return format_html("<br>".join(emails))

    host_preview.short_description = "Zoom 상담 호스트"

    def has_module_permission(self, request):
        return bool(request.user.is_staff)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_staff)

    def has_add_permission(self, request):
        if not request.user.is_superuser:
            return False
        if RemoteZoomSchedulingSettings.objects.filter(
            pk=RemoteZoomSchedulingSettings.SETTINGS_PK
        ).exists():
            return False
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        urls = super().get_urls()
        extra = [
            path(
                "zoom-connection-test/",
                self.admin_site.admin_view(self.zoom_connection_test_view),
                name="scheduling_remotezoomschedulingsettings_zoom_test",
            ),
        ]
        return extra + urls

    def zoom_connection_test_view(self, request):
        if request.method != "POST":
            return JsonResponse({"ok": False, "messages": ["POST만 허용됩니다."]}, status=405)
        if not request.user.is_superuser:
            return JsonResponse(
                {"ok": False, "messages": ["최고 관리자만 Zoom 연결 테스트를 할 수 있습니다."]},
                status=403,
            )
        raw = request.POST.get("licensed_user_emails", "")
        emails, parse_errors = parse_licensed_user_email_lines(raw)
        if parse_errors:
            return JsonResponse({"ok": False, "messages": parse_errors})
        if not emails:
            return JsonResponse(
                {
                    "ok": False,
                    "messages": [
                        "Host 이메일을 한 줄에 하나씩 입력한 뒤 연결 테스트를 실행해 주세요."
                    ],
                }
            )
        try:
            ok, messages_out = verify_zoom_host_emails(emails)
        except (ZoomAPIError, ZoomNotConfiguredError) as exc:
            return JsonResponse({"ok": False, "messages": [str(exc)]})
        return JsonResponse({"ok": ok, "messages": messages_out})

    def save_model(self, request, obj, form, change):
        emails, _errors = parse_licensed_user_email_lines(obj.licensed_user_emails)
        if emails:
            obj.last_verified_at = timezone.now()
        else:
            obj.last_verified_at = None
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


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
class AppointmentAdmin(RecordsSubmittedLockMixin, admin.ModelAdmin):
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

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        if case_records_are_locked(records_lock_case_for_obj(obj)):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied(RECORDS_LOCKED_MESSAGE)
        if not _admin_intends_remote_confirm(obj, change=change, form=form):
            super().save_model(request, obj, form, change)
            return

        try:
            obj.status = AppointmentStatus.PENDING
            super().save_model(request, obj, form, change)
            obj.refresh_from_db(
                fields=["status", "case_id", "scheduled_at", "duration_minutes"]
            )
            # 게이트(_case_is_remote)는 DB를 보지만 confirm은 obj.case 캐시를 본다.
            obj.case = Case.objects.get(pk=obj.case_id)
            confirm_appointment_with_zoom(obj, notify=True)
        except (
            AppointmentServiceError,
            ZoomAPIError,
            ZoomNotConfiguredError,
        ) as exc:
            transaction.set_rollback(True)
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
