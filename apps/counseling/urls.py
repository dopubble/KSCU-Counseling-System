from django.urls import path

from . import views

app_name = "counseling"

urlpatterns = [
    path("apply/", views.apply, name="apply"),
    path("apply/email-check/", views.apply_email_check, name="apply_email_check"),
    path("application/<uuid:pk>/", views.application_detail, name="application_detail"),
    path(
        "case/<uuid:pk>/records/unsubmit/",
        views.case_records_unsubmit,
        name="case_records_unsubmit",
    ),
]
