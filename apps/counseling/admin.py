from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .admin_lock import RecordsSubmittedLockMixin
from .models import (
    Case,
    CaseStatus,
    CaseTerminationType,
    ChatMessage,
    CounselingApplication,
    SessionScheduleChangeRequest,
)
from .services import RECORDS_LOCKED_MESSAGE, case_records_are_locked


# 기록 최종 제출 이후에도 관리자가 운영 상태만 고칠 수 있는 필드.
_LOCKED_CASE_EDITABLE_FIELDS = frozenset({"status", "termination_type"})


class CaseAdminForm(forms.ModelForm):
    class Meta:
        model = Case
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get("termination_type")
        if field is not None:
            field.choices = [("", "미지정"), *CaseTerminationType.choices]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") != CaseStatus.CLOSED:
            cleaned["termination_type"] = ""
        return cleaned


@admin.register(CounselingApplication)
class CounselingApplicationAdmin(RecordsSubmittedLockMixin, admin.ModelAdmin):
    list_display = (
        "client",
        "display_counseling_types",
        "residence_region",
        "status",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = (
        "client__name",
        "client__email",
        "reason",
        "residence_region",
        "clinical_diagnosis",
        "occupation",
    )
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="상담 유형")
    def display_counseling_types(self, obj):
        return obj.get_counseling_types_display()


@admin.register(Case)
class CaseAdmin(RecordsSubmittedLockMixin, admin.ModelAdmin):
    form = CaseAdminForm
    list_display = (
        "case_number",
        "client",
        "counselor",
        "counseling_method",
        "status",
        "termination_type",
        "remaining_sessions",
        "total_sessions",
        "day_of_cancel_count",
        "risk_level",
        "zoom_meeting_url",
        "opened_at",
    )
    list_filter = ("status", "termination_type", "risk_level", "counseling_method")
    search_fields = ("case_number", "client__name", "counselor__name")
    readonly_fields = ("case_number", "opened_at")

    def has_change_permission(self, request, obj=None):
        if obj is not None and case_records_are_locked(obj):
            return admin.ModelAdmin.has_change_permission(self, request, obj)
        return super().has_change_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and case_records_are_locked(obj):
            for field in self.model._meta.fields:
                if field.name in _LOCKED_CASE_EDITABLE_FIELDS:
                    continue
                if field.name not in readonly:
                    readonly.append(field.name)
        return readonly

    def save_model(self, request, obj, form, change):
        if not case_records_are_locked(obj):
            super().save_model(request, obj, form, change)
            return
        if not change or not obj.pk:
            raise PermissionDenied(RECORDS_LOCKED_MESSAGE)
        original = Case.objects.get(pk=obj.pk)
        for field in obj._meta.concrete_fields:
            if field.primary_key or field.name in _LOCKED_CASE_EDITABLE_FIELDS:
                continue
            if getattr(obj, field.attname) != getattr(original, field.attname):
                raise PermissionDenied(RECORDS_LOCKED_MESSAGE)
        obj.save(update_fields=["status", "termination_type"])


@admin.register(ChatMessage)
class ChatMessageAdmin(RecordsSubmittedLockMixin, admin.ModelAdmin):
    list_display = ("case", "sender", "recipient", "body_preview", "created_at")
    list_filter = ("created_at",)
    search_fields = ("case__case_number", "sender__name", "recipient__name", "body")
    readonly_fields = ("created_at",)

    @admin.display(description="메시지")
    def body_preview(self, obj):
        return (obj.body[:60] + "…") if len(obj.body) > 60 else obj.body


@admin.register(SessionScheduleChangeRequest)
class SessionScheduleChangeRequestAdmin(RecordsSubmittedLockMixin, admin.ModelAdmin):
    list_display = ("case", "session_number", "client", "preferred_datetime", "created_at")
    list_filter = ("created_at",)
    search_fields = ("case__case_number", "client__name", "message")
    readonly_fields = ("created_at",)
