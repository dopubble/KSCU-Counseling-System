from django.urls import path

from . import views

app_name = "admin_panel"

urlpatterns = [
    path("dashboard/", views.admin_dashboard, name="dashboard"),
    path("matching/", views.matching_list, name="matching_list"),
    path(
        "counseling-management/",
        views.counseling_management,
        name="counseling_management",
    ),
    path("applications/", views.application_list, name="application_list"),
    path("cases/", views.case_list, name="case_list"),
    path("statistics/", views.statistics, name="statistics"),
    path("cancel-pending/", views.cancel_pending_list, name="cancel_pending_list"),
    path("appointments/calendar/", views.appointment_calendar, name="appointment_calendar"),
    path(
        "appointments/calendar/events/",
        views.appointment_calendar_events,
        name="appointment_calendar_events",
    ),
    path("counselors/", views.counselor_list, name="counselor_list"),
]
