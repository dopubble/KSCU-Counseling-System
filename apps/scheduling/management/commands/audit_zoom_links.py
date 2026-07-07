"""내담자·상담사·이메일·Case별 Zoom 링크 출처 대조."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from apps.scheduling.zoom_hosts import host_id_for_email
from apps.scheduling.zoom_links import (
    appointment_zoom_link_is_locked,
    is_zoom_host_url,
    resolve_appointment_zoom_join_url,
)
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
        "  · active_url = resolve_appointment_zoom_join_url (내담자·상담사·메일 공통)\n"
        "  · case_url = Case.zoom_meeting_url (join_url 없을 때만 fallback)\n"
        "  · db_join = ZoomMeeting.join_url\n"
        "예) python manage.py audit_zoom_links --from-date 2026-07-01 --to-date 2026-08-07\n"
        "예) python manage.py audit_zoom_links --case-stale-only\n"
        "예) python manage.py audit_zoom_links --clients 정진아 --date 2026-07-01 --hour 20"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--clients",
            default="",
            help="쉼표 구분 내담자 이름",
        )
        parser.add_argument("--date", default="", help="YYYY-MM-DD (단일 날짜)")
        parser.add_argument("--from-date", default="", help="YYYY-MM-DD 시작 (포함)")
        parser.add_argument("--to-date", default="", help="YYYY-MM-DD 끝 (포함)")
        parser.add_argument("--hour", type=int, default=None, help="시간(0-23) KST")
        parser.add_argument(
            "--mismatch-only",
            action="store_true",
            help="내담자·상담사·메일 링크가 서로 다른 건만 출력",
        )
        parser.add_argument(
            "--case-stale-only",
            action="store_true",
            help="Case.zoom_meeting_url만 예약 join_url과 다른 건 (정리용)",
        )
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="join_url 또는 meeting_id 없는 건만 출력",
        )

    def handle(self, *args, **options):
        engine = connection.settings_dict.get("ENGINE", "")
        db_label = "postgres" if "postgres" in engine else "sqlite/local"
        self.stdout.write(f"Database: {db_label}")
        self.stdout.write("")

        names = [n.strip() for n in (options.get("clients") or "").split(",") if n.strip()]
        date_text = (options.get("date") or "").strip()
        from_text = (options.get("from_date") or "").strip()
        to_text = (options.get("to_date") or "").strip()

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
        elif from_text or to_text:
            if from_text:
                qs = qs.filter(scheduled_at__date__gte=from_text)
            if to_text:
                qs = qs.filter(scheduled_at__date__lte=to_text)

        hour = options.get("hour")
        appointments = list(qs)
        if hour is not None:
            appointments = [
                apt
                for apt in appointments
                if timezone.localtime(apt.scheduled_at).hour == hour
            ]

        if not appointments:
            self.stdout.write(self.style.WARNING("대상 예약 없음"))
            return

        mismatch_rows = 0
        case_stale_rows = 0
        missing_rows = 0
        host_url_rows = 0
        for apt in appointments:
            case = apt.case
            zm = getattr(apt, "zoom_meeting", None)
            active_url = resolve_appointment_zoom_join_url(apt, case)
            case_url = (case.zoom_meeting_url or "").strip()
            db_join = (zm.join_url if zm else "") or ""
            db_start = (zm.start_url if zm else "") or ""
            db_meeting_id = (zm.zoom_meeting_id if zm else "") or ""
            host_email = (zm.zoom_host_email if zm else "") or ""
            host_id = host_id_for_email(host_email) or (
                "host_03" if host_email.strip() else "?"
            )
            locked = appointment_zoom_link_is_locked(apt)

            active_mid = _meeting_id_from_url(active_url)
            case_mid = _meeting_id_from_url(case_url)
            join_mid = _meeting_id_from_url(db_join)

            # 내담자·상담사·메일은 모두 resolve_appointment_zoom_join_url → active_url
            email_dashboard_match = (
                not active_url
                or not db_join
                or active_url.rstrip("/") == db_join.rstrip("/")
            )
            case_stale = (
                bool(active_url)
                and bool(case_url)
                and active_url.rstrip("/") != case_url.rstrip("/")
            )
            missing_zoom = not locked
            host_url_risk = bool(active_url) and is_zoom_host_url(active_url)

            if not email_dashboard_match:
                mismatch_rows += 1
            if case_stale:
                case_stale_rows += 1
            if missing_zoom:
                missing_rows += 1
            if host_url_risk:
                host_url_rows += 1

            if options.get("missing_only") and not missing_zoom:
                continue
            if options.get("mismatch_only") and email_dashboard_match:
                continue
            if options.get("case_stale_only") and not case_stale:
                continue

            self.stdout.write(
                f"=== {apt.client.name} | {timezone.localtime(apt.scheduled_at):%Y-%m-%d %H:%M} "
                f"| {apt.session_number}회차 ==="
            )
            self.stdout.write(f"  counselor: {apt.counselor.name if apt.counselor else '-'}")
            self.stdout.write(f"  zoom_host: {host_id} ({host_email or 'empty'})")
            self.stdout.write(f"  link_locked: {'yes' if locked else 'no'}")
            self.stdout.write(f"  zoom_meeting_id (DB): {db_meeting_id or '(empty)'}")
            self.stdout.write(
                f"  active_url (내담자·상담사·메일): {active_url or '(empty)'}"
            )
            self.stdout.write(f"  case_url (Case DB): {case_url or '(empty)'}")
            self.stdout.write(f"  db_join (ZoomMeeting): {db_join or '(empty)'}")
            if db_start:
                self.stdout.write(
                    f"  db_start (API 보관, UI 미사용): {db_start[:80]}{'…' if len(db_start) > 80 else ''}"
                )
            if host_url_risk:
                self.stdout.write(
                    self.style.ERROR(
                        "  CRITICAL: active_url이 호스트 URL — 상담사 로그인 차단 화면 위험"
                    )
                )
            self.stdout.write(
                f"  meeting_id: active={active_mid or '-'} case={case_mid or '-'} "
                f"db_join={join_mid or '-'}"
            )
            if email_dashboard_match:
                self.stdout.write(
                    self.style.SUCCESS(
                        "  내담자·상담사·메일: MATCH (동일 active_url)"
                    )
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        "  내담자·상담사·메일: MISMATCH — 조사 필요"
                    )
                )
            if case_stale:
                self.stdout.write(
                    self.style.WARNING(
                        "  Case URL만 이전 회기/옛 링크 — active_url과 다름 (정리 권장)"
                    )
                )
            elif case_url:
                self.stdout.write(self.style.SUCCESS("  Case URL: MATCH"))
            self.stdout.write("")

        self.stdout.write(
            f"Checked {len(appointments)} appointment(s), "
            f"real mismatches: {mismatch_rows}, case_url stale: {case_stale_rows}, "
            f"missing zoom: {missing_rows}, host_url risk: {host_url_rows}"
        )
        if case_stale_rows:
            self.stdout.write(
                self.style.WARNING(
                    "Case URL 정리(링크 변경 없음): "
                    "python manage.py sync_locked_case_zoom_urls --apply"
                )
            )
