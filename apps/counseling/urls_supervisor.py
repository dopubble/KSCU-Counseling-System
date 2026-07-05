from django.urls import path

from . import views_supervisor

app_name = "supervisor"

urlpatterns = [
    path("", views_supervisor.supervisor_dashboard, name="dashboard"),
    path("journals/", views_supervisor.supervisor_cohort_journals, name="cohort_journals"),
    path(
        "journals/<uuid:journal_pk>/pdf/",
        views_supervisor.supervisor_journal_pdf,
        name="journal_pdf",
    ),
    path(
        "initial-records/",
        views_supervisor.supervisor_cohort_initial_records,
        name="cohort_initial_records",
    ),
    path(
        "initial-records/<uuid:record_pk>/",
        views_supervisor.supervisor_initial_record_detail,
        name="initial_record_detail",
    ),
    path(
        "initial-records/<uuid:record_pk>/pdf/",
        views_supervisor.supervisor_initial_record_pdf,
        name="initial_record_pdf",
    ),
]
