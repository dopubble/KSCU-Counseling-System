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
    path(
        "api/in-person-busy-intervals/",
        views.in_person_busy_intervals,
        name="in_person_busy_intervals",
    ),
    path(
        "api/booking-slots/",
        views.booking_slots,
        name="booking_slots",
    ),
    path(
        "api/booking-available-dates/",
        views.booking_available_dates,
        name="booking_available_dates",
    ),
    path(
        "api/counselor-calendar-events/",
        views.counselor_calendar_events,
        name="counselor_calendar_events",
    ),
]
