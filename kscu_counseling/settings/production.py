from .base import *  # noqa: F401, F403
from .base import _env_str  # star import는 _ 접두 private 이름 제외

DEBUG = False

MIDDLEWARE = [
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE,  # noqa: F405
]

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Railway / reverse proxy — HTTPS 종단이 프록시 앞단일 때 Django가 HTTPS를 인식
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


def _normalize_origin(value: str) -> str:
    """https://host 형식으로 통일."""
    value = value.strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value.rstrip("/")
    return f"https://{value.rstrip('/')}"


def _build_csrf_trusted_origins() -> list[str]:
    """
    CSRF_TRUSTED_ORIGINS 환경 변수 + Railway 자동 도메인.
    예: CSRF_TRUSTED_ORIGINS=https://app.up.railway.app,https://counseling.example.com
    """
    origins: list[str] = []
    for part in _env_str("CSRF_TRUSTED_ORIGINS").split(","):
        origin = _normalize_origin(part)
        if origin:
            origins.append(origin)

    for env_name in ("RAILWAY_PUBLIC_DOMAIN",):
        raw = _env_str(env_name)
        if not raw:
            continue
        origin = _normalize_origin(raw)
        if origin and origin not in origins:
            origins.append(origin)

    return origins


CSRF_TRUSTED_ORIGINS = _build_csrf_trusted_origins()

# Railway가 주입하는 도메인을 ALLOWED_HOSTS에 자동 추가 (환경 변수 누락 방지)
for _railway_host in (
    _env_str("RAILWAY_PUBLIC_DOMAIN"),
    _env_str("RAILWAY_PRIVATE_DOMAIN"),
):
    if _railway_host and _railway_host not in ALLOWED_HOSTS:  # noqa: F405
        ALLOWED_HOSTS.append(_railway_host)  # noqa: F405
