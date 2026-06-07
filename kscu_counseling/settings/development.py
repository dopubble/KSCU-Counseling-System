import importlib.util

from .base import *  # noqa: F401, F403
from .base import _configure_email_backend

DEBUG = True

# 프로젝트 루트의 local_email.py (manage.py 와 같은 폴더) — Git 제외
_LOCAL_EMAIL_FILE = BASE_DIR / "local_email.py"  # noqa: F405


def _load_project_local_email():
    if not _LOCAL_EMAIL_FILE.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "project_local_email",
        _LOCAL_EMAIL_FILE,
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_local_email = _load_project_local_email()
if _local_email is not None:
    if getattr(_local_email, "EMAIL_HOST", ""):
        EMAIL_HOST = _local_email.EMAIL_HOST
    if getattr(_local_email, "EMAIL_PORT", None) is not None:
        EMAIL_PORT = _local_email.EMAIL_PORT
    if getattr(_local_email, "EMAIL_HOST_USER", ""):
        EMAIL_HOST_USER = _local_email.EMAIL_HOST_USER
    if getattr(_local_email, "EMAIL_HOST_PASSWORD", ""):
        EMAIL_HOST_PASSWORD = _local_email.EMAIL_HOST_PASSWORD
    if hasattr(_local_email, "EMAIL_USE_TLS"):
        EMAIL_USE_TLS = _local_email.EMAIL_USE_TLS
    if getattr(_local_email, "DEFAULT_FROM_EMAIL", ""):
        DEFAULT_FROM_EMAIL = _local_email.DEFAULT_FROM_EMAIL
    if getattr(_local_email, "STAFF_NOTIFY_EMAILS", None):
        STAFF_NOTIFY_EMAILS = _local_email.STAFF_NOTIFY_EMAILS
    _configure_email_backend()

try:
    import debug_toolbar  # noqa: F401

    INSTALLED_APPS += ["debug_toolbar"]  # noqa: F405
    MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware", *MIDDLEWARE]  # noqa: F405
    INTERNAL_IPS = ["127.0.0.1"]
except ImportError:
    pass
