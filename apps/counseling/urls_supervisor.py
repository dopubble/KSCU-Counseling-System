from django.urls import path

from . import (
    views_supervision,
    views_supervisor,
    views_supervisor_training_progress,
    views_training_certificate,
)

app_name = "supervisor"

urlpatterns = [
    path("", views_supervisor.supervisor_dashboard, name="dashboard"),
    path("journals/", views_supervisor.supervisor_cohort_journals, name="cohort_journals"),
    path(
        "journals/counselors/<uuid:counselor_id>/",
        views_supervisor.supervisor_counselor_journals,
        name="counselor_journals",
    ),
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
    path(
        "training-progress/",
        views_supervisor_training_progress.supervisor_training_progress,
        name="training_progress",
    ),
    path(
        "training-progress/counselors/<uuid:counselor_id>/",
        views_supervisor_training_progress.supervisor_counselor_training_progress_detail,
        name="counselor_training_progress_detail",
    ),
    path(
        "training-progress/counselors/<uuid:counselor_id>/certificate/",
        views_training_certificate.supervisor_counselor_training_certificate,
        name="counselor_training_certificate",
    ),
    path(
        "supervision/",
        views_supervision.supervisor_supervision_cohorts,
        name="supervision_cohorts",
    ),
    path(
        "supervision/<int:cohort>/",
        views_supervision.supervisor_supervision_sessions,
        name="supervision_sessions",
    ),
    path(
        "supervision/sessions/<uuid:pk>/",
        views_supervision.supervisor_supervision_attendance,
        name="supervision_attendance",
    ),
]
