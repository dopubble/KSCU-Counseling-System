"""확정 Zoom join_url이 있는 예약 — Case URL만 ZoomMeeting과 일치 (링크 자체는 변경하지 않음)."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.zoom_links import (
    appointment_zoom_link_is_locked,
    resolve_appointment_zoom_join_url,
    sync_case_zoom_meeting_url,
)


class Command(BaseCommand):
    help = (
        "join_url·meeting_id가 저장된 확정 예약의 Case.zoom_meeting_url만 동기화합니다.\n"
        "Zoom API 호출·join_url 변경 없음.\n"
        "예) python manage.py sync_locked_case_zoom_urls --clients 정진아 --date 2026-07-01 --hour 20\n"
        "예) python manage.py sync_locked_case_zoom_urls --apply"
    )

    def add_arguments(self, parser):
        parser.add_argument("--clients", default="", help="쉼표 구분 내담자 이름")
        parser.add_argument("--date", default="", help="YYYY-MM-DD")
        parser.add_argument("--from-date", default="", help="YYYY-MM-DD 시작 (포함)")
        parser.add_argument("--to-date", default="", help="YYYY-MM-DD 끝 (포함)")
        parser.add_argument("--hour", type=int, default=None, help="0-23 KST")
        parser.add_argument("--apply", action="store_true", help="실제 Case URL 저장")

    def handle(self, *args, **options):
        engine = connection.settings_dict.get("ENGINE", "")
        if options["apply"] and "sqlite" in engine:
            self.stdout.write(self.style.ERROR("로컬 SQLite에서는 --apply 불가"))
            return

        names = [n.strip() for n in (options.get("clients") or "").split(",") if n.strip()]
        date_text = (options.get("date") or "").strip()
        from_text = (options.get("from_date") or "").strip()
        to_text = (options.get("to_date") or "").strip()
        hour = options.get("hour")

        qs = (
            Appointment.objects.filter(
                status=AppointmentStatus.CONFIRMED,
                case__counseling_method="REMOTE",
            )
            .select_related("case", "client", "zoom_meeting")
            .order_by("scheduled_at", "pk")
        )
        if names:
            qs = qs.filter(client__name__in=names)
        if date_text:
            qs = qs.filter(scheduled_at__date=date_text)
        elif from_text or to_text:
            if from_text:
                qs = qs.filter(scheduled_at__date__gte=from_text)
            if to_text:
                qs = qs.filter(scheduled_at__date__lte=to_text)

        appointments = list(qs)
        if hour is not None:
            appointments = [
                apt
                for apt in appointments
                if timezone.localtime(apt.scheduled_at).hour == hour
            ]

        synced = skipped = 0
        for apt in appointments:
            label = (
                f"{apt.client.name} "
                f"{timezone.localtime(apt.scheduled_at):%Y-%m-%d %H:%M} "
                f"{apt.session_number}회차"
            )
            if not appointment_zoom_link_is_locked(apt):
                skipped += 1
                self.stdout.write(f"skip (no locked zoom): {label}")
                continue

            dash = resolve_appointment_zoom_join_url(apt, apt.case)
            case_url = (apt.case.zoom_meeting_url or "").strip()
            if dash.rstrip("/") == case_url.rstrip("/"):
                skipped += 1
                self.stdout.write(self.style.SUCCESS(f"ok (already match): {label}"))
                continue

            if not options["apply"]:
                synced += 1
                self.stdout.write(f"[dry-run] sync Case URL: {label}")
                self.stdout.write(f"  {case_url or '(empty)'}")
                self.stdout.write(f"  -> {dash}")
                continue

            sync_case_zoom_meeting_url(apt, join_url=dash)
            synced += 1
            self.stdout.write(self.style.SUCCESS(f"synced Case URL: {label}"))

        prefix = "[dry-run] " if not options["apply"] else ""
        self.stdout.write(
            self.style.SUCCESS(f"{prefix}동기화 {synced}건, 건너뜀 {skipped}건")
        )
        if not options["apply"] and synced:
            self.stdout.write("실제 반영: ... sync_locked_case_zoom_urls --apply")
