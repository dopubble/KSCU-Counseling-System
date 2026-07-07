import os
from pathlib import Path

try:
    from dotenv import dotenv_values, load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):  # type: ignore[misc]
        return False

    def dotenv_values(*args, **kwargs):  # type: ignore[misc]
        return {}

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = BASE_DIR / ".env"


def _running_on_paas() -> bool:
    """Railway 등 PaaS — 플랫폼 환경 변수가 .env보다 우선해야 함."""
    return bool(
        os.environ.get("PORT")
        or os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_PROJECT_ID")
    )


# 로컬·PaaS 공통: 이미 설정된 환경 변수(셸·Railway)가 .env보다 우선
_DOTENV_LOADED = (
    load_dotenv(_ENV_FILE, override=False, encoding="utf-8")
    if _ENV_FILE.is_file()
    else False
)
DOTENV_FILE = str(_ENV_FILE)
DOTENV_LOADED = bool(_DOTENV_LOADED)
# load_dotenv 이후에도 파일에서 직접 읽기 (이중 안전)
_ENV_FROM_FILE: dict[str, str | None] = (
    dotenv_values(_ENV_FILE, encoding="utf-8") if _ENV_FILE.is_file() else {}
)


def _strip_env_value(value: str | None) -> str:
    """공백·따옴표 제거."""
    if value is None:
        return ""
    s = str(value).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1].strip()
    return s


def _env_str(name: str, default: str = "") -> str:
    """
    환경 변수 문자열.
    1) os.environ (load_dotenv로 .env 반영)
    2) 비어 있으면 dotenv_values로 .env 파일에서 직접 읽기
    """
    from_env = _strip_env_value(os.environ.get(name))
    if from_env:
        return from_env
    from_file = _strip_env_value(_ENV_FROM_FILE.get(name))
    if from_file:
        return from_file
    return _strip_env_value(default)


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# Google Calendar 스타일 예약 캘린더 UI (기본 활성, CALENDAR_GCAL_UI=false 로 끌 수 있음)
CALENDAR_GCAL_UI = _env_str("CALENDAR_GCAL_UI", "True").lower() in ("true", "1", "yes")

SECRET_KEY = _env_str("SECRET_KEY", "django-insecure-dev-key-change-in-production")

DEBUG = _env_str("DEBUG", "False").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = [
    host.strip()
    for host in _env_str("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "crispy_forms",
    "crispy_bootstrap5",
    "apps.accounts.apps.AccountsConfig",
    "apps.counseling",
    "apps.scheduling",
    "apps.documents",
    "apps.sessions_app",
    "apps.reports",
    "apps.notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "kscu_counseling.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.reports.context_processors.admin_pending_alerts",
            ],
        },
    },
]

WSGI_APPLICATION = "kscu_counseling.wsgi.application"
ASGI_APPLICATION = "kscu_counseling.asgi.application"

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "/"  # 메인 홈 (공개 페이지)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
        "consent": {
            "BACKEND": "apps.documents.storage.ConsentMediaStorage",
            "OPTIONS": {},
        },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {
    message_constants.DEBUG: "secondary",
    message_constants.INFO: "info",
    message_constants.SUCCESS: "success",
    message_constants.WARNING: "warning",
    message_constants.ERROR: "danger",
}

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# Session — 매 요청 DB 저장은 Railway Postgres 왕복을 늘림 (운영에서 False)
SESSION_COOKIE_AGE = 1800  # 30 minutes
SESSION_SAVE_EVERY_REQUEST = False

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "kscu-default",
    }
}

# File upload
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# Celery
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Zoom (Server-to-Server OAuth — .env 변수명과 동일해야 함)
ZOOM_ACCOUNT_ID = _env_str("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = _env_str("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = _env_str("ZOOM_CLIENT_SECRET")
# API로 생성한 회의의 Claim Host용 6자리 키 (Zoom 프로필과 동일 값, 상담사 화면 전용)
ZOOM_HOST_KEY = _env_str("ZOOM_HOST_KEY")
# Licensed Zoom 사용자 이메일 (쉼표 구분, 동시간대 회의 시 host 01/02/03… 자동 배정)
ZOOM_LICENSED_USERS = _env_str("ZOOM_LICENSED_USERS")
# 같은 시작 시각 비대면 동시 확정 상한 기본값(관리자 설정 없을 때). Django admin에서 상향 가능.
DEFAULT_REMOTE_ZOOM_SIMULTANEOUS_CAPACITY = _env_int(
    "DEFAULT_REMOTE_ZOOM_SIMULTANEOUS_CAPACITY", 2
)
# 관리자 캘린더 호스트 라벨 (미사용 시 Licensed 사용자 수에 맞춰 host_01,host_02 자동)
ZOOM_HOST_POOL = _env_str("ZOOM_HOST_POOL")
# 호스트 배정 시 예약 종료 후 점유 완충(분) — 기본 30
ZOOM_HOST_BUFFER_MINUTES = _env_int("ZOOM_HOST_BUFFER_MINUTES", 30)

# ---------------------------------------------------------------------------
# Gmail SMTP (상담 신청·취소 요청 알림, 비밀번호 찾기 등)
#
# 프로젝트 루트 .env 파일에 아래 값을 넣으세요. (Git에 커밋하지 마세요.)
#
#   EMAIL_HOST=smtp.gmail.com
#   EMAIL_PORT=587
#   EMAIL_USE_TLS=True
#   EMAIL_HOST_USER=your.account@gmail.com
#   EMAIL_HOST_PASSWORD=abcdefghijklmnop
#       ↑ Google '앱 비밀번호' 16자 (공백 없이 붙여 넣기. 일반 로그인 비밀번호 X)
#   DEFAULT_FROM_EMAIL=your.account@gmail.com
#   STAFF_NOTIFY_EMAILS=admin@example.com,another@example.com
#
# 앱 비밀번호 발급: Google 계정 → 보안 → 2단계 인증 → 앱 비밀번호
# 상세 가이드: docs/GMAIL_SMTP_SETUP.md
# ---------------------------------------------------------------------------
EMAIL_HOST = _env_str("EMAIL_HOST") or "smtp.gmail.com"
EMAIL_PORT = _env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = _env_str("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = _env_str("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = _env_str("EMAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
EMAIL_USE_SSL = _env_str("EMAIL_USE_SSL", "False").lower() in ("true", "1", "yes")
DEFAULT_FROM_EMAIL = _env_str("DEFAULT_FROM_EMAIL") or EMAIL_HOST_USER or "noreply@kscu.ac.kr"
STAFF_NOTIFY_EMAILS = [
    e.strip()
    for e in _env_str("STAFF_NOTIFY_EMAILS").split(",")
    if e.strip()
]


def _configure_email_backend() -> None:
    global EMAIL_BACKEND
    if EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:
        EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    else:
        EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


_configure_email_backend()


def _parse_database_url(url: str) -> dict:
    """Parse postgres://user:pass@host:port/dbname URL."""
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/").split("?")[0]
    config: dict = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": db_name,
        "USER": parsed.username,
        "PASSWORD": parsed.password,
        "HOST": parsed.hostname,
        "PORT": parsed.port or 5432,
    }
    query = parse_qs(parsed.query)
    options: dict = {}
    if "sslmode" in query:
        options["sslmode"] = query["sslmode"][0]
    host = (parsed.hostname or "").lower()
    if not options and ("railway" in host or "rlwy.net" in host):
        options["sslmode"] = "require"
    if options:
        config["OPTIONS"] = options
    return config


DATABASE_URL = _env_str("DATABASE_URL", "")
if DATABASE_URL:
    DATABASES = {"default": _parse_database_url(DATABASE_URL)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
