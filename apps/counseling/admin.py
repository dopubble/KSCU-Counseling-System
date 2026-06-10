from django.contrib import admin

from .models import Case, CounselingApplication, SessionScheduleChangeRequest


@admin.register(CounselingApplication)
class CounselingApplicationAdmin(admin.ModelAdmin):
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
class CaseAdmin(admin.ModelAdmin):
    list_display = (
        "case_number",
        "client",
        "counselor",
        "counseling_method",
        "status",
        "remaining_sessions",
        "total_sessions",
        "day_of_cancel_count",
        "risk_level",
        "zoom_meeting_url",
        "opened_at",
    )
    list_filter = ("status", "risk_level", "counseling_method")
    search_fields = ("case_number", "client__name", "counselor__name")
    readonly_fields = ("case_number", "opened_at")


@admin.register(SessionScheduleChangeRequest)
class SessionScheduleChangeRequestAdmin(admin.ModelAdmin):
    list_display = ("case", "session_number", "client", "preferred_datetime", "created_at")
    list_filter = ("created_at",)
    search_fields = ("case__case_number", "client__name", "message")
    readonly_fields = ("created_at",)
