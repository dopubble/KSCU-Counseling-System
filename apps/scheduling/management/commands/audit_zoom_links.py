"""내담자·상담사·이메일·Case별 Zoom 링크 출처 대조."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from django.core.management.base import BaseCommand
from django.db import connection

from apps.scheduling.zoom_links import resolve_appointment_zoom_join_url
from apps.scheduling.models import Appointment, AppointmentStatus


def _meeting_id_from_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    match = re.search(r"/j/(\d+)", text)
    if match:
        return match.group(1)
    parsed = urlparse(text)
    if parsed.path:
        digits = re.sub(r"\D", "", parsed.path.split("/")[-1])
        if len(digits) >= 9:
            return digits
    return ""


class Command(BaseCommand):
    help = (
        "확정 비대면 예약별 Zoom 링크 출처를 비교합니다.\n"
        "  · dashboard_url = ZoomMeeting.join_url 우선 (내담자·상담사 대시보드)\n"
        "  · case_url = Case.zoom_meeting_url (확정 이메일에 포함)\n"
        "예) python manage.py audit_zoom_links --clients 김수미,성순희 --date 2026-06-26"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--clients",
            default="",
            help="쉼표 구분 내담자 이름",
        )
        parser.add_argument("--date", default="", help="YYYY-MM-DD (선택)")

    def handle(self, *args, **options):
        engine = connection.settings_dict.get("ENGINE", "")
        db_label = "postgres" if "postgres" in engine else "sqlite/local"
        self.stdout.write(f"Database: {db_label}")
        self.stdout.write("")

        names = [n.strip() for n in (options.get("clients") or "").split(",") if n.strip()]
        date_text = (options.get("date") or "").strip()

        qs = (
            Appointment.objects.filter(
                status=AppointmentStatus.CONFIRMED,
                case__counseling_method="REMOTE",
            )
            .select_related("case", "client", "counselor", "zoom_meeting")
            .order_by("scheduled_at", "pk")
        )
        if names:
            qs = qs.filter(client__name__in=names)
        if date_text:
            qs = qs.filter(scheduled_at__date=date_text)

        if not qs.exists():
            self.stdout.write(self.style.WARNING("대상 예약 없음"))
            return

        mismatch_rows = 0
        for apt in qs:
            case = apt.case
            zm = getattr(apt, "zoom_meeting", None)
            dashboard_url = resolve_appointment_zoom_join_url(apt, case)
            case_url = (case.zoom_meeting_url or "").strip()
            db_join = (zm.join_url if zm else "") or ""
            db_meeting_id = (zm.zoom_meeting_id if zm else "") or ""
            host_email = (zm.zoom_host_email if zm else "") or ""

            dash_mid = _meeting_id_from_url(dashboard_url)
            case_mid = _meeting_id_from_url(case_url)
            join_mid = _meeting_id_from_url(db_join)

            same_dashboard_case = (
                not dashboard_url
                or not case_url
                or dash_mid == case_mid
                or dashboard_url.rstrip("/") == case_url.rstrip("/")
            )
            if not same_dashboard_case:
                mismatch_rows += 1

            self.stdout.write(f"=== {apt.client.name} | {apt.scheduled_at:%Y-%m-%d %H:%M} ===")
            self.stdout.write(f"  counselor: {apt.counselor.name if apt.counselor else '-'}")
            self.stdout.write(f"  zoom_host_email: {host_email or '(empty)'}")
            self.stdout.write(f"  zoom_meeting_id (DB): {db_meeting_id or '(empty)'}")
            self.stdout.write(f"  dashboard_url (client+counselor): {dashboard_url or '(empty)'}")
            self.stdout.write(f"  case_url (confirmation email): {case_url or '(empty)'}")
            self.stdout.write(
                f"  meeting_id from URL: dashboard={dash_mid or '-'} "
                f"case={case_mid or '-'} db_join={join_mid or '-'}"
            )
            if same_dashboard_case:
                self.stdout.write(self.style.SUCCESS("  dashboard vs email URL: MATCH"))
            else:
                self.stdout.write(
                    self.style.ERROR(
                        "  dashboard vs email URL: MISMATCH "
                        "(내담자 이메일·대시보드 링크 불일치 가능)"
                    )
                )
            self.stdout.write("")

        self.stdout.write(f"Checked {qs.count()} appointment(s), URL mismatches: {mismatch_rows}")
