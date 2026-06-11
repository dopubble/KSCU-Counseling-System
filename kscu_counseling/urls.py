from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import home
from apps.reports.views import platform_user_manual

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", home, name="home"),
    path("manual/", platform_user_manual, name="platform_user_manual"),
    path("counseling/", include("apps.counseling.urls")),
    path("counseling/counselor/", include("apps.counseling.urls_counselor")),
    path("accounts/", include("apps.accounts.urls")),
    path("client/", include("apps.counseling.urls_client")),
    path("admin-panel/", include("apps.reports.urls_admin")),
    path("scheduling/", include("apps.scheduling.urls")),
    path("documents/", include("apps.documents.urls")),
    path("sessions/", include("apps.sessions_app.urls")),
    path("health/", include("apps.reports.urls_health")),
]

handler403 = "apps.accounts.views.permission_denied_view"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    try:
        import debug_toolbar

        urlpatterns = [path("__debug__/", include(debug_toolbar.urls)), *urlpatterns]
    except ImportError:
        pass
