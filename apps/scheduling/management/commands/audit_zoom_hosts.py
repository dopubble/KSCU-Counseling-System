"""특정 일시·내담자 Zoom 호스트 배정 감사 (DB 저장값 vs 알고리즘 기대값)."""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone
from zoneinfo import ZoneInfo

from apps.counseling.models import CounselingMethod
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.zoom_hosts import (
    assign_host_emails_for_appointments,
    get_zoom_licensed_user_emails,
    host_id_for_email,
)

KST = ZoneInfo("Asia/Seoul")


class Command(BaseCommand):
    help = (
        "확정 비대면 예약의 zoom_host_email(DB)과 호스트 배정 알고리즘 기대값을 비교합니다.\n"
        "예) python manage.py audit_zoom_hosts --date 2026-06-30 --hour 11\n"
        "예) python manage.py audit_zoom_hosts --date 2026-06-30 --clients 김혜정,김효순"
    )

    def add_arguments(self, parser):
        parser.add_argument("--date", default="", help="YYYY-MM-DD (Asia/Seoul)")
        parser.add_argument("--hour", type=int, default=None, help="시간(0-23), --date와 함께")
        parser.add_argument(
            "--clients",
            default="",
            help="쉼표 구분 내담자 이름 (예: 김혜정,김효순)",
        )

    def handle(self, *args, **options):
        from apps.scheduling.remote_zoom_capacity import remote_zoom_capacity_limit
        from apps.scheduling.zoom_scheduling_settings import remote_zoom_host_pool_size

        engine = connection.settings_dict.get("ENGINE", "")
        db_label = "postgres" if "postgres" in engine else "sqlite/local"
        self.stdout.write(f"Database: {db_label}")
        self.stdout.write(f"Licensed hosts: {', '.join(get_zoom_licensed_user_emails())}")
        self.stdout.write(
            f"동시간대 상한(관리자): {remote_zoom_capacity_limit()}건 · "
            f"호스트 풀: {remote_zoom_host_pool_size()}대"
        )
        self.stdout.write("")

        client_names = [
            n.strip() for n in (options.get("clients") or "").split(",") if n.strip()
        ]
        date_text = (options.get("date") or "").strip()
        hour = options.get("hour")

        if client_names and date_text:
            day = datetime.strptime(date_text, "%Y-%m-%d").date()
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=KST)
            day_end = day_start.replace(hour=0) + __import__("datetime").timedelta(days=1)
            for name in client_names:
                self._print_clients_on_day(name, day_start, day_end, hour)
            self.stdout.write("")
            if hour is not None:
                slot_start = day_start.replace(hour=hour, minute=0)
                slot_end = slot_start.replace(hour=hour + 1) if hour < 23 else day_end
            else:
                slot_start, slot_end = day_start, day_end
            self._print_overlap_slot(slot_start, slot_end)
            return

        if date_text:
            day = datetime.strptime(date_text, "%Y-%m-%d").date()
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=KST)
            day_end = day_start + __import__("datetime").timedelta(days=1)
            if hour is not None:
                slot_start = day_start.replace(hour=hour, minute=0)
                slot_end = (
                    slot_start + __import__("datetime").timedelta(hours=1)
                    if hour < 23
                    else day_end
                )
                self._print_overlap_slot(slot_start, slot_end)
            else:
                self._print_overlap_slot(day_start, day_end)
            return

        self._print_mismatches_all()

    def _print_clients_on_day(self, name, day_start, day_end, hour):
        qs = (
            Appointment.objects.filter(
                status=AppointmentStatus.CONFIRMED,
                client__name=name,
                scheduled_at__gte=day_start,
                scheduled_at__lt=day_end,
            )
            .select_related("client", "counselor", "case", "zoom_meeting")
            .order_by("scheduled_at")
        )
        self.stdout.write(f"=== {name} on {day_start.date()} ({qs.count()} confirmed) ===")
        for apt in qs:
            local = timezone.localtime(apt.scheduled_at)
            if hour is not None and local.hour != hour:
                continue
            zm = getattr(apt, "zoom_meeting", None)
            host_email = (getattr(zm, "zoom_host_email", None) or "").strip()
            self.stdout.write(
                f"  {local:%Y-%m-%d %H:%M} | counselor={apt.counselor.name if apt.counselor else '-'} "
                f"| remote={apt.case.counseling_method == CounselingMethod.REMOTE} "
                f"| stored={host_id_for_email(host_email) or '-'} ({host_email or '-'}) "
                f"| meeting_id={(zm.zoom_meeting_id if zm else '-')}"
            )

    def _print_overlap_slot(self, slot_start, slot_end):
        overlap = list(
            Appointment.objects.filter(
                status=AppointmentStatus.CONFIRMED,
                case__counseling_method=CounselingMethod.REMOTE,
                scheduled_at__gte=slot_start,
                scheduled_at__lt=slot_end,
            )
            .select_related("client", "counselor", "zoom_meeting")
            .order_by("scheduled_at", "pk")
        )
        self.stdout.write(
            f"REMOTE CONFIRMED {timezone.localtime(slot_start):%Y-%m-%d %H:%M}"
            f"-{timezone.localtime(slot_end):%H:%M}: {len(overlap)}"
        )
        if not overlap:
            return

        expected = assign_host_emails_for_appointments(overlap)
        mismatch = 0
        for apt in overlap:
            zm = getattr(apt, "zoom_meeting", None)
            stored_email = (getattr(zm, "zoom_host_email", None) or "").strip()
            stored_id = host_id_for_email(stored_email) or "?"
            exp_email = expected.get(str(apt.pk), "")
            exp_id = host_id_for_email(exp_email) or "?"
            flag = ""
            if stored_email and exp_email and stored_email.lower() != exp_email.lower():
                flag = "  <-- MISMATCH"
                mismatch += 1
            elif not stored_email and exp_email:
                flag = "  <-- empty zoom_host_email"
                mismatch += 1
            self.stdout.write(
                f"  {timezone.localtime(apt.scheduled_at):%H:%M} {apt.client.name} "
                f"stored={stored_id} expected={exp_id}{flag}"
            )

        dup_hosts: dict[str, list[str]] = {}
        for apt in overlap:
            zm = getattr(apt, "zoom_meeting", None)
            he = (getattr(zm, "zoom_host_email", None) or "").strip().lower()
            if he:
                dup_hosts.setdefault(he, []).append(apt.client.name)

        conflicts = {k: v for k, v in dup_hosts.items() if len(v) > 1}
        if conflicts:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Same host_email on overlapping slot:"))
            for email, names in conflicts.items():
                self.stdout.write(
                    f"  {host_id_for_email(email)} ({email}): {', '.join(names)}"
                )

        if mismatch:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"{mismatch} mismatch(es). Fix: python manage.py fix_zoom_host_mismatches --apply"
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("Host assignment matches algorithm."))

    def _print_mismatches_all(self):
        remote = list(
            Appointment.objects.filter(
                status=AppointmentStatus.CONFIRMED,
                case__counseling_method=CounselingMethod.REMOTE,
            )
            .select_related("client", "zoom_meeting")
            .order_by("scheduled_at", "pk")
        )
        expected = assign_host_emails_for_appointments(remote)
        mismatches = []
        for apt in remote:
            zm = getattr(apt, "zoom_meeting", None)
            stored = (getattr(zm, "zoom_host_email", None) or "").strip().lower()
            exp = (expected.get(str(apt.pk), "") or "").strip().lower()
            if stored != exp:
                mismatches.append((apt, stored, exp))

        self.stdout.write(f"All remote confirmed: {len(remote)}, mismatches: {len(mismatches)}")
        for apt, stored, exp in mismatches:
            self.stdout.write(
                f"  {timezone.localtime(apt.scheduled_at):%Y-%m-%d %H:%M} {apt.client.name} "
                f"stored={host_id_for_email(stored) or '-'} expected={host_id_for_email(exp) or '-'}"
            )
