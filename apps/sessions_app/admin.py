from django.contrib import admin

from .models import CounselingJournal, InitialCounselingRecord, TerminationCounselingRecord, ZoomMeeting


@admin.register(CounselingJournal)
class CounselingJournalAdmin(admin.ModelAdmin):
    list_display = (
        "case",
        "session_number",
        "session_category",
        "session_datetime",
        "counselor",
        "is_draft",
        "created_at",
    )
    list_filter = ("is_draft",)
    search_fields = ("case__case_number", "counselor__name")


@admin.register(InitialCounselingRecord)
class InitialCounselingRecordAdmin(admin.ModelAdmin):
    list_display = ("case", "counselor", "session_start_datetime", "is_draft", "updated_at")
    list_filter = ("is_draft",)
    search_fields = ("case__case_number", "counselor__name")


@admin.register(TerminationCounselingRecord)
class TerminationCounselingRecordAdmin(admin.ModelAdmin):
    list_display = ("case", "counselor", "is_draft", "updated_at")
    list_filter = ("is_draft",)
    search_fields = ("case__case_number", "counselor__name")


@admin.register(ZoomMeeting)
class ZoomMeetingAdmin(admin.ModelAdmin):
    list_display = ("appointment", "zoom_meeting_id", "created_at")
    search_fields = ("zoom_meeting_id",)
