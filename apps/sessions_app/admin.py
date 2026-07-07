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
    list_display = (
        "client_name",
        "appointment_scheduled_at",
        "counselor_name",
        "session_number",
        "zoom_meeting_id",
        "zoom_host_email",
        "record_created_at",
    )
    search_fields = (
        "zoom_meeting_id",
        "appointment__client__name",
        "appointment__counselor__name",
    )
    list_select_related = (
        "appointment",
        "appointment__client",
        "appointment__counselor",
    )
    ordering = ("-appointment__scheduled_at",)

    @admin.display(description="내담자")
    def client_name(self, obj: ZoomMeeting) -> str:
        return obj.appointment.client.name

    @admin.display(description="상담사")
    def counselor_name(self, obj: ZoomMeeting) -> str:
        return obj.appointment.counselor.name

    @admin.display(description="회차", ordering="appointment__session_number")
    def session_number(self, obj: ZoomMeeting) -> str:
        number = obj.appointment.session_number
        return f"{number}회기" if number else "—"

    @admin.display(description="예약 일시 (KST)", ordering="appointment__scheduled_at")
    def appointment_scheduled_at(self, obj: ZoomMeeting) -> str:
        from apps.scheduling.availability import format_local_datetime

        return format_local_datetime(obj.appointment.scheduled_at)

    @admin.display(description="레코드 생성일 (KST)", ordering="created_at")
    def record_created_at(self, obj: ZoomMeeting) -> str:
        from apps.scheduling.availability import format_local_datetime

        return format_local_datetime(obj.created_at)
