from django.urls import path

from . import views

app_name = "counseling"

urlpatterns = [
    path("apply/", views.apply, name="apply"),
    path("application/<uuid:pk>/", views.application_detail, name="application_detail"),
]
