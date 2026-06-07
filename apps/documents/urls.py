from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("consents/", views.consent_list, name="consent_list"),
]
