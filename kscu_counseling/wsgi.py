import os

from django.core.wsgi import get_wsgi_application

PRODUCTION_SETTINGS = "kscu_counseling.settings.production"
DEVELOPMENT_SETTINGS = "kscu_counseling.settings.development"


def _ensure_django_settings_module() -> None:
    """Railway/PaaS — production 설정 강제 (오타·development → 400 DisallowedHost)."""
    on_railway = bool(
        os.environ.get("PORT")
        or os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_PROJECT_ID")
    )
    if on_railway:
        os.environ["DJANGO_SETTINGS_MODULE"] = PRODUCTION_SETTINGS
        return
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", DEVELOPMENT_SETTINGS)


_ensure_django_settings_module()

application = get_wsgi_application()
