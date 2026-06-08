"""상담사 가용시간(매주 반복) 일괄 등록."""

from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.accounts.models import User, UserRole
from apps.scheduling.counselor_availability_seed import COUNSELOR_AVAILABILITY_SEEDS
from apps.scheduling.models import CounselorAvailability


class Command(BaseCommand):
    help = (
        "상담사 이메일 기준으로 매주 반복 가용시간을 일괄 등록합니다.\n"
        "기본 데이터: apps/scheduling/counselor_availability_seed.py (16명, 전효영·이수정 제외)\n\n"
        "예시:\n"
        "  python manage.py import_counselor_availabilities --dry-run\n"
        "  python manage.py import_counselor_availabilities --replace\n"
        "  python manage.py import_counselor_availabilities --allow-local"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--replace",
            action="store_true",
            help="해당 상담사의 기존 가용시간을 모두 삭제 후 다시 등록",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="DB 저장 없이 처리 예상만 출력",
        )
        parser.add_argument(
            "--allow-local",
            action="store_true",
            help="로컬 SQLite에서도 실행 허용",
        )

    def handle(self, *args, **options):
        if not options["allow_local"]:
            self._ensure_database_ready()

        dry_run = options["dry_run"]
        replace = options["replace"]
        created = 0
        skipped = 0
        errors = 0

        for seed in COUNSELOR_AVAILABILITY_SEEDS:
            counselor = User.objects.filter(
                email__iexact=seed.email.strip(),
                role=UserRole.COUNSELOR,
            ).first()
            if not counselor:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"[error] {seed.name} ({seed.email}) — 등록된 상담사 계정 없음"
                    )
                )
                continue

            if replace and not dry_run:
                deleted, _ = CounselorAvailability.objects.filter(
                    counselor=counselor
                ).delete()
                if deleted:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  {seed.name}: 기존 가용시간 {deleted}건 삭제"
                        )
                    )

            for slot in seed.slots:
                start = self._parse_time(slot.start_time)
                end = self._parse_time(slot.end_time)
                if end <= start:
                    errors += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"[error] {seed.name} {slot.start_time}-{slot.end_time} 시간 오류"
                        )
                    )
                    continue

                for day in slot.days:
                    exists = CounselorAvailability.objects.filter(
                        counselor=counselor,
                        is_recurring=True,
                        day_of_week=day,
                        start_time=start,
                        end_time=end,
                        is_available=True,
                    ).exists()
                    if exists and not replace:
                        skipped += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"[skipped] {seed.name} {_day_label(day)} "
                                f"{slot.start_time}-{slot.end_time} (이미 있음)"
                            )
                        )
                        continue

                    if dry_run:
                        created += 1
                        self.stdout.write(
                            self.style.NOTICE(
                                f"[would_create] {seed.name} {_day_label(day)} "
                                f"{slot.start_time}-{slot.end_time}"
                            )
                        )
                        continue

                    with transaction.atomic():
                        CounselorAvailability.objects.create(
                            counselor=counselor,
                            is_recurring=True,
                            specific_date=None,
                            day_of_week=day,
                            start_time=start,
                            end_time=end,
                            is_available=True,
                        )
                    created += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[created] {seed.name} {_day_label(day)} "
                            f"{slot.start_time}-{slot.end_time}"
                        )
                    )

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}완료: 생성 {created}, 건너뜀 {skipped}, 오류 {errors}"
            )
        )
        if errors:
            raise CommandError(f"{errors}건 오류 — import_users로 상담사 계정을 먼저 등록하세요.")

    def _parse_time(self, value: str):
        text = (value or "").strip().replace("시", "")
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).time()
            except ValueError:
                continue
        raise CommandError(f"시간 형식 오류: {value!r}")

    def _ensure_database_ready(self) -> None:
        db = settings.DATABASES["default"]
        engine = db.get("ENGINE", "")
        host = (db.get("HOST") or "").lower()

        if "sqlite" in engine:
            raise CommandError(
                "운영 DB에 등록하려면 Railway Public DATABASE_URL을 설정하세요.\n"
                "  $env:DATABASE_URL = \"postgresql://...@xxxx.proxy.rlwy.net:포트/railway\"\n"
                "  $env:DJANGO_SETTINGS_MODULE = \"kscu_counseling.settings.production\"\n"
                "로컬 테스트: --allow-local"
            )

        if "internal" in host or host.endswith(".railway.internal"):
            raise CommandError("Public DATABASE_URL (*.proxy.rlwy.net)을 사용하세요.")

        try:
            connection.ensure_connection()
        except Exception as exc:
            raise CommandError(f"PostgreSQL 연결 실패: {exc}") from exc


def _day_label(day: int) -> str:
    labels = ("월", "화", "수", "목", "금", "토", "일")
    return labels[day] if 0 <= day < 7 else str(day)
