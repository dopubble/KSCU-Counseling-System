from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("login/", views.UserLoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("pending/", views.pending, name="pending"),
    path("profile/", views.profile_update, name="profile_update"),
    path(
        "password-change/",
        views.AccountPasswordChangeView.as_view(),
        name="password_change",
    ),
    path(
        "password-change/done/",
        views.AccountPasswordChangeDoneView.as_view(),
        name="password_change_done",
    ),
    path("find-id/", views.find_id, name="find_id"),
    path(
        "password-reset/",
        views.AccountPasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        views.AccountPasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "password-reset/confirm/<uidb64>/<token>/",
        views.AccountPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        views.AccountPasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
]
