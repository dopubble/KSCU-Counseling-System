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
    path(
        "counselor-assignments/",
        views.counselor_assignment_list,
        name="counselor_assignment_list",
    ),
    path(
        "counselor-assignments/<uuid:assignment_pk>/file/",
        views.admin_assignment_file,
        name="assignment_file",
    ),
]
