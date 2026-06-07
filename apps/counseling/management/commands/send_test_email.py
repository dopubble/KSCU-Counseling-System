"""
SMTP 테스트 메일 발송.

사용:
    python manage.py send_test_email
    python manage.py send_test_email recipient@example.com
"""

from pathlib import Path

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

try:
    from dotenv import dotenv_values
except ImportError:
    dotenv_values = None  # type: ignore[misc, assignment]


class Command(BaseCommand):
    help = "Gmail SMTP 설정을 확인하고 테스트 메일을 발송합니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "recipient",
            nargs="?",
            default="",
            help="수신 이메일 (생략 시 STAFF_NOTIFY_EMAILS 첫 주소)",
        )

    def _print_diagnostics(self) -> None:
        env_path = Path(getattr(settings, "DOTENV_FILE", settings.BASE_DIR / ".env"))
        local_path = Path(settings.BASE_DIR) / "local_email.py"

        self.stdout.write("=== 이메일 설정 진단 ===")
        self.stdout.write(f"BASE_DIR          : {settings.BASE_DIR}")
        self.stdout.write(f".env 경로         : {env_path}")
        self.stdout.write(f".env 존재         : {env_path.is_file()}")
        self.stdout.write(f"load_dotenv       : {getattr(settings, 'DOTENV_LOADED', '?')}")
        self.stdout.write(f"local_email.py    : {local_path.is_file()}")
        self.stdout.write(f"EMAIL_BACKEND     : {settings.EMAIL_BACKEND}")
        self.stdout.write(f"EMAIL_HOST        : {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
        self.stdout.write(
            f"EMAIL_HOST_USER   : len={len(settings.EMAIL_HOST_USER or '')} "
            f"set={bool((settings.EMAIL_HOST_USER or '').strip())}"
        )
        self.stdout.write(
            f"EMAIL_HOST_PASSWORD: len={len(settings.EMAIL_HOST_PASSWORD or '')} "
            f"set={bool((settings.EMAIL_HOST_PASSWORD or '').strip())}"
        )

        if dotenv_values and env_path.is_file():
            from_file = dotenv_values(env_path, encoding="utf-8")
            for key in ("EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD"):
                v = (from_file.get(key) or "").strip()
                self.stdout.write(f".env[{key}]       : len={len(v)} set={bool(v)}")

    def handle(self, *args, **options):
        self._print_diagnostics()

        recipient = (options["recipient"] or "").strip()
        if not recipient and getattr(settings, "STAFF_NOTIFY_EMAILS", None):
            recipient = settings.STAFF_NOTIFY_EMAILS[0]

        if not recipient:
            self.stderr.write(
                self.style.ERROR(
                    "수신 이메일을 인자로 넣거나 STAFF_NOTIFY_EMAILS(.env / local_email.py)를 설정하세요."
                )
            )
            return

        user = (settings.EMAIL_HOST_USER or "").strip()
        password = (settings.EMAIL_HOST_PASSWORD or "").strip()
        if not user or not password:
            self.stderr.write(
                self.style.ERROR(
                    "\nEMAIL_HOST_USER 또는 EMAIL_HOST_PASSWORD가 비어 있습니다.\n"
                    "• 프로젝트 루트 local_email.py 의 두 값을 채우거나\n"
                    "• 프로젝트 루트 .env 에 동일 변수명으로 설정하세요.\n"
                    "• 설정 후: python manage.py check_dotenv\n"
                )
            )
            return

        if settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
            self.stdout.write(
                self.style.WARNING(
                    "SMTP 백엔드가 아닌 콘솔 백엔드입니다. USER/PASS가 로드되면 SMTP로 전환됩니다."
                )
            )

        from_email = settings.DEFAULT_FROM_EMAIL or user
        self.stdout.write(f"\n발송 시도: {from_email} → {recipient}")

        send_mail(
            subject="[KSCU 상담] SMTP 테스트",
            message="Gmail SMTP 설정이 정상적으로 동작합니다.",
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS("테스트 메일 발송을 완료했습니다."))
