from django.urls import path

from . import views

app_name = "sessions"

urlpatterns = [
    path("journals/", views.journal_list, name="journal_list"),
]
