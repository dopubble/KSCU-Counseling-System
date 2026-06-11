from django.urls import path

from . import chat_views, views

app_name = "client"

urlpatterns = [
    path("dashboard/", views.client_dashboard, name="dashboard"),
    path("applications/", views.application_list, name="application_list"),
    path(
        "applications/<uuid:pk>/",
        views.client_application_detail,
        name="application_detail",
    ),
    path(
        "applications/<uuid:pk>/delete/",
        views.delete_application,
        name="delete_application",
    ),
    path(
        "applications/<uuid:pk>/edit/",
        views.edit_application,
        name="edit_application",
    ),
    path(
        "applications/<uuid:pk>/cancel-request/",
        views.request_cancel_application,
        name="request_cancel",
    ),
    path("case/<uuid:pk>/", views.client_case_detail, name="case_detail"),
    path(
        "case/<uuid:pk>/chat/unread/",
        chat_views.client_case_chat_unread,
        name="case_chat_unread",
    ),
    path(
        "case/<uuid:pk>/chat/messages/",
        chat_views.client_case_chat_messages,
        name="case_chat_messages",
    ),
    path(
        "case/<uuid:pk>/chat/send/",
        chat_views.client_case_chat_send,
        name="case_chat_send",
    ),
    path(
        "case/<uuid:case_pk>/shared-materials/<uuid:material_pk>/file/",
        views.client_shared_material_file,
        name="shared_material_file",
    ),
    path(
        "case/<uuid:case_pk>/session/<uuid:appointment_pk>/materials/",
        views.client_session_materials,
        name="session_materials",
    ),
    path(
        "case/<uuid:case_pk>/session/<uuid:appointment_pk>/materials/<uuid:material_pk>/download/",
        views.client_session_material_download,
        name="session_material_download",
    ),
    path(
        "case/<uuid:case_pk>/session/<int:session_number>/schedule-change/",
        views.client_session_schedule_change,
        name="session_schedule_change",
    ),
    path(
        "case/<uuid:case_pk>/appointment/<uuid:appointment_pk>/cancel-request/",
        views.client_session_appointment_cancel,
        name="session_appointment_cancel",
    ),
    path(
        "case/<uuid:case_pk>/appointment/<uuid:appointment_pk>/cancel-withdraw/",
        views.client_session_cancel_withdraw,
        name="session_cancel_withdraw",
    ),
    path(
        "case/<uuid:case_pk>/appointment/<uuid:appointment_pk>/pending-withdraw/",
        views.client_session_pending_withdraw,
        name="session_pending_withdraw",
    ),
    path(
        "case/<uuid:case_pk>/session/<int:session_number>/materials/upload/",
        views.client_session_material_upload,
        name="session_material_upload",
    ),
    path(
        "case/<uuid:case_pk>/session/<int:session_number>/materials/<uuid:material_pk>/file/",
        views.client_session_material_file,
        name="session_material_file",
    ),
    path(
        "case/<uuid:case_pk>/session/<int:session_number>/materials/<uuid:material_pk>/delete/",
        views.client_session_material_delete,
        name="session_material_delete",
    ),
    path(
        "case/<uuid:pk>/appointment/request/",
        views.client_request_appointment,
        name="request_appointment",
    ),
]
