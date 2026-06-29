"""DB 예약 일시(KST)와 Zoom 회의 start_time 불일치 일괄 동기화."""

from django.core.management.base import BaseCommand

from apps.scheduling.services import sync_zoom_meeting_times
from apps.scheduling.utils import ZoomNotConfiguredError


class Command(BaseCommand):
    help = (
        "확정된 비대면 예약의 DB scheduled_at과 Zoom 회의 start_time을 비교·동기화합니다.\n"
        "  · 먼저 불일치 목록을 출력한 뒤, --dry-run이 아니면 Zoom PATCH로 DB 시간에 맞춥니다.\n"
        "  · 개별 PATCH 실패 시 다음 예약으로 계속 진행합니다.\n\n"
        "사전 조건: Zoom Scope meeting:update:meeting:admin (또는 meeting:update:meeting)\n"
        "  docs/ZOOM_MEETING_UPDATE_SCOPES.md 참고\n\n"
        "예시:\n"
        "  python manage.py sync_zoom_meeting_times --dry-run\n"
        "  python manage.py sync_zoom_meeting_times"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="불일치 목록만 출력 (Zoom PATCH 호출 없음)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        try:
            in_sync, updated, _skipped, failed, mismatches, errors = (
                sync_zoom_meeting_times(dry_run=dry_run)
            )
        except ZoomNotConfiguredError as exc:
            self.stdout.write(self.style.WARNING(str(exc)))
            return

        self.stdout.write("")
        self.stdout.write(
            self.style.HTTP_INFO(
                f"=== Zoom 회의 시간 점검 (일치 {in_sync}건, 불일치 {len(mismatches)}건) ==="
            )
        )

        if not mismatches and not errors:
            self.stdout.write(self.style.SUCCESS("모든 확정 비대면 예약이 Zoom과 일치합니다."))
            return

        for index, row in enumerate(mismatches, start=1):
            session_label = (
                f"{row['session_number']}회기"
                if row["session_number"]
                else "회기 미지정"
            )
            zoom_duration = row["zoom_duration"]
            zoom_duration_label = (
                f"{zoom_duration}분" if zoom_duration is not None else "?"
            )
            self.stdout.write("")
            self.stdout.write(
                f"[{index}] {row['client_name']} | {row['case_number']} | "
                f"{session_label} | meeting {row['meeting_id']}"
            )
            self.stdout.write(
                f"    DB(KST):  {row['db_local']} ({row['db_duration']}분)"
            )
            self.stdout.write(
                f"    Zoom:     {row['zoom_local']} ({zoom_duration_label})"
            )

        if errors:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("=== 조회/처리 중 오류 ==="))
            for line in errors:
                self.stdout.write(self.style.ERROR(line))

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"--dry-run: 위 {len(mismatches)}건은 Zoom PATCH를 호출하지 않았습니다. "
                    "Scope 적용 후 `python manage.py sync_zoom_meeting_times` 로 동기화하세요."
                )
            )
            return

        if mismatches:
            self.stdout.write("")
            self.stdout.write(self.style.HTTP_INFO("=== Zoom PATCH 동기화 결과 ==="))
            self.stdout.write(
                self.style.SUCCESS(
                    f"updated={updated}, failed={failed}, "
                    f"already_in_sync={in_sync}"
                )
            )
