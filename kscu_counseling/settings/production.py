from .base import *  # noqa: F401, F403
from .base import _env_str  # star import는 _ 접두 private 이름 제외
import os

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

# Railway / reverse proxy — HTTPS만 프록시 헤더로 신뢰
# USE_X_FORWARDED_HOST=True면 Railway 내부 Host로 검증되어 400(DisallowedHost) 발생 가능
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = False

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


def _sanitize_allowed_host(value: str) -> str:
    """ALLOWED_HOSTS — https:// 제거, 포트·경로 제거."""
    value = value.strip()
    for prefix in ("https://", "http://"):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
    value = value.split("/")[0]
    if value.startswith("[") and "]" in value:
        return value  # IPv6
    if ":" in value:
        value = value.rsplit(":", 1)[0]
    return value.strip()


def _normalize_origin(value: str) -> str:
    """https://host 형식으로 통일."""
    value = value.strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value.rstrip("/")
    return f"https://{value.rstrip('/')}"


def _env_csv(name: str) -> list[str]:
    """쉼표 구분 env — Value에 'KEY=value' 형태로 잘못 넣은 경우 보정."""
    raw = _env_str(name)
    prefix = f"{name}="
    if raw.startswith(prefix):
        raw = raw[len(prefix) :]
    return [part.strip() for part in raw.split(",") if part.strip()]


def _build_csrf_trusted_origins() -> list[str]:
    """
    CSRF_TRUSTED_ORIGINS 환경 변수 + Railway 자동 도메인.
    예: CSRF_TRUSTED_ORIGINS=https://app.up.railway.app,https://counseling.example.com
    """
    origins: list[str] = []
    for part in _env_csv("CSRF_TRUSTED_ORIGINS"):
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


def _extend_allowed_hosts(hosts: list[str], *candidates: str) -> None:
    for host in candidates:
        host = _sanitize_allowed_host(host)
        if host and host not in hosts:
            hosts.append(host)


# 환경 변수에 https:// 가 포함된 경우 정리
ALLOWED_HOSTS = [  # noqa: F405
    h for h in (_sanitize_allowed_host(x) for x in ALLOWED_HOSTS) if h  # noqa: F405
]

# Railway 배포 — ALLOWED_HOSTS·CSRF 누락 시 400(DisallowedHost) 방지
_on_railway = bool(
    _env_str("RAILWAY_ENVIRONMENT")
    or _env_str("RAILWAY_PROJECT_ID")
    or _env_str("RAILWAY_SERVICE_ID")
    or _env_str("PORT")
)
if _on_railway:
    _extend_allowed_hosts(ALLOWED_HOSTS, ".up.railway.app", ".railway.app")  # noqa: F405

for _railway_host in (
    _env_str("RAILWAY_PUBLIC_DOMAIN"),
    _env_str("RAILWAY_PRIVATE_DOMAIN"),
):
    _extend_allowed_hosts(ALLOWED_HOSTS, _railway_host)  # noqa: F405

# CSRF — Railway 도메인이 env에 없을 때 public domain으로 보완
if _on_railway and not CSRF_TRUSTED_ORIGINS:
    _pub = _env_str("RAILWAY_PUBLIC_DOMAIN")
    if _pub:
        CSRF_TRUSTED_ORIGINS = [_normalize_origin(_pub)]

# Railway Deploy Logs에서 Host 설정 확인용 (비밀값 없음)
if _on_railway:
    import sys

    print(
        f"[kscu] DJANGO_SETTINGS_MODULE={os.environ.get('DJANGO_SETTINGS_MODULE')}",
        f"ALLOWED_HOSTS={ALLOWED_HOSTS}",
        file=sys.stderr,
    )
