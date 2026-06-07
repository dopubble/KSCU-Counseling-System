from django.contrib import admin

from .models import Case, CounselingApplication, SessionScheduleChangeRequest


@admin.register(CounselingApplication)
class CounselingApplicationAdmin(admin.ModelAdmin):
    list_display = ("client", "counseling_type", "status", "created_at")
    list_filter = ("status", "counseling_type")
    search_fields = ("client__name", "client__email", "reason")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = (
        "case_number",
        "client",
        "counselor",
        "status",
        "remaining_sessions",
        "total_sessions",
        "day_of_cancel_count",
        "risk_level",
        "zoom_meeting_url",
        "opened_at",
    )
    list_filter = ("status", "risk_level")
    search_fields = ("case_number", "client__name", "counselor__name")
    readonly_fields = ("case_number", "opened_at")


@admin.register(SessionScheduleChangeRequest)
class SessionScheduleChangeRequestAdmin(admin.ModelAdmin):
    list_display = ("case", "session_number", "client", "preferred_datetime", "created_at")
    list_filter = ("created_at",)
    search_fields = ("case__case_number", "client__name", "message")
    readonly_fields = ("created_at",)
