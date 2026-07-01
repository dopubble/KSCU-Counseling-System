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
]
