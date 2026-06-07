from django.contrib import admin

from .models import Appointment, AvailabilityException, CounselorAvailability


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
