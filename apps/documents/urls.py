from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("consents/<uuid:pk>/file/", views.consent_file, name="consent_file"),
]
