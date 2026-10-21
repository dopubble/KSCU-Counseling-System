"""운영 메일 backend 선택. Resend는 HTTPS API만 사용한다."""

RESEND_EMAIL_BACKEND = "anymail.backends.resend.EmailBackend"
SMTP_EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
CONSOLE_EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


def select_production_email_backend(
    *,
    resend_api_key: str,
    email_host_user: str,
    email_host_password: str,
) -> str:
    """RESEND_API_KEY가 있으면 Resend HTTPS backend를 SMTP보다 먼저 고른다."""
    if (resend_api_key or "").strip():
        return RESEND_EMAIL_BACKEND
    if (email_host_user or "").strip() and (email_host_password or "").strip():
        return SMTP_EMAIL_BACKEND
    return CONSOLE_EMAIL_BACKEND
