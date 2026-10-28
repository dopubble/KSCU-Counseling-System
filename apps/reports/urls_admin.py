from django.urls import path

from apps.counseling import views_supervision, views_training_certificate

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
    path(
        "counselors/<uuid:counselor_id>/training-certificate/",
        views_training_certificate.admin_counselor_training_certificate,
        name="counselor_training_certificate",
    ),
    path(
        "supervision/",
        views_supervision.admin_supervision_cohorts,
        name="supervision_cohorts",
    ),
    path(
        "supervision/<int:cohort>/",
        views_supervision.admin_supervision_sessions,
        name="supervision_sessions",
    ),
    path(
        "supervision/<int:cohort>/sessions/new/",
        views_supervision.admin_supervision_session_create,
        name="supervision_session_create",
    ),
    path(
        "supervision/sessions/<uuid:pk>/edit/",
        views_supervision.admin_supervision_session_edit,
        name="supervision_session_edit",
    ),
    path(
        "supervision/sessions/<uuid:pk>/",
        views_supervision.admin_supervision_attendance,
        name="supervision_attendance",
    ),
    path("consents/", views.consent_submissions, name="consent_submissions"),
]
