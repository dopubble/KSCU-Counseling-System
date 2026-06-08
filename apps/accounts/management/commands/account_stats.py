"""연결 중인 DB와 계정 수 확인 (로컬 vs Railway 구분용)."""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

from apps.accounts.models import ClientProfile, CounselorProfile, User, UserRole


class Command(BaseCommand):
    help = "현재 DATABASE 설정과 User/상담사/내담자 수를 출력합니다."

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        engine = db.get("ENGINE", "")
        host = db.get("HOST") or "(sqlite 파일)" if "sqlite" in engine else "(unknown)"

        self.stdout.write(f"ENGINE: {engine}")
        self.stdout.write(f"HOST: {host}")
        self.stdout.write(f"NAME: {db.get('NAME')}")

        if "postgresql" in engine:
            host_s = str(host).lower()
            if "internal" in host_s:
                self.stdout.write(
                    self.style.WARNING(
                        "경고: *.railway.internal 은 Railway 서버 안에서만 접속됩니다. "
                        "PC에서는 Postgres Connect 탭의 Public URL(rlwy.net)을 쓰세요."
                    )
                )
            elif "rlwy.net" in host_s or "railway.app" in host_s:
                self.stdout.write(
                    self.style.SUCCESS("Public Railway Postgres URL로 보입니다.")
                )

        try:
            connection.ensure_connection()
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"DB 연결 실패: {exc}"))
            return

        total = User.objects.count()
        counselors = User.objects.filter(role=UserRole.COUNSELOR).count()
        clients = User.objects.filter(role=UserRole.CLIENT).count()
        c_profiles = CounselorProfile.objects.count()
        cl_profiles = ClientProfile.objects.count()

        self.stdout.write("")
        self.stdout.write(f"User 전체: {total}")
        self.stdout.write(f"  상담사(role): {counselors}  / CounselorProfile: {c_profiles}")
        self.stdout.write(f"  내담자(role): {clients}  / ClientProfile: {cl_profiles}")

        if total <= 2:
            self.stdout.write(
                self.style.WARNING(
                    "계정이 거의 없습니다. 운영 DB라면 import_users를 Public DATABASE_URL로 다시 실행하세요."
                )
            )
