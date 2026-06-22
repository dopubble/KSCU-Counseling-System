from django.urls import path

from . import views

app_name = "scheduling"

urlpatterns = [
    path("availability/", views.availability_list, name="availability_list"),
    path("availability/add/", views.availability_create, name="availability_create"),
    path(
        "availability/<uuid:pk>/delete/",
        views.availability_delete,
        name="availability_delete",
    ),
    path(
        "api/remote-zoom-busy-intervals/",
        views.remote_zoom_busy_intervals,
        name="remote_zoom_busy_intervals",
    ),
]
