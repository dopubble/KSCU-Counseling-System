"""다가오는 비대면 예약 Zoom 링크 점검 — 상담 전 join_url·호스트키·누락 확인."""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.utils import is_zoom_host_key_configured
from apps.scheduling.zoom_links import (
    appointment_counselor_host_key,
    appointment_zoom_link_is_locked,
    is_zoom_host_url,
    resolve_appointment_zoom_counselor_url,
    resolve_appointment_zoom_join_url,
    verify_counselor_zoom_join_policy,
    ZoomLaunchPolicyError,
)


class Command(BaseCommand):
    help = (
        "다가오는 확정 비대면 예약의 Zoom 입장 링크를 점검합니다.\n"
        "  · active_url = join_url(/j/) — 상담사·내담자·메일 공통\n"
        "  · 호스트 URL(/s/)·join 누락·호스트키 미설정 시 경고\n"
        "예) python manage.py check_zoom_upcoming\n"
        "예) python manage.py check_zoom_upcoming --days 2 --strict"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=1,
            help="오늘 포함 앞으로 N일 (기본 1 = 오늘·내일)",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="문제 1건이라도 있으면 exit code 1",
        )

    def handle(self, *args, **options):
        days = max(1, int(options["days"] or 1))
        strict = bool(options.get("strict"))

        try:
            verify_counselor_zoom_join_policy()
        except ZoomLaunchPolicyError as exc:
            raise CommandError(f"코드 정책 위반: {exc}") from exc

        now = timezone.now()
        end = now + timedelta(days=days)
        appointments = list(
            Appointment.objects.filter(
                status=AppointmentStatus.CONFIRMED,
                case__counseling_method="REMOTE",
                scheduled_at__gte=now,
                scheduled_at__lt=end,
            )
            .select_related("case", "client", "counselor", "zoom_meeting")
            .order_by("scheduled_at", "pk")
        )

        if not appointments:
            self.stdout.write(
                self.style.NOTICE(
                    f"점검 구간 {timezone.localtime(now):%Y-%m-%d %H:%M} ~ "
                    f"{timezone.localtime(end):%Y-%m-%d %H:%M}: 대상 예약 없음"
                )
            )
            return

        host_key_global = is_zoom_host_key_configured()
        issues = 0

        self.stdout.write(
            f"점검 {len(appointments)}건 "
            f"({timezone.localtime(now):%m-%d %H:%M} ~ {timezone.localtime(end):%m-%d %H:%M} KST)"
        )
        self.stdout.write("")

        for apt in appointments:
            case = apt.case
            join_url = resolve_appointment_zoom_join_url(apt, case)
            counselor_url = resolve_appointment_zoom_counselor_url(apt, case)
            locked = appointment_zoom_link_is_locked(apt)
            override_key = appointment_counselor_host_key(apt)
            has_host_key = bool(override_key) or host_key_global
            row_issues: list[str] = []

            if not locked or not join_url:
                row_issues.append("join_url 또는 meeting_id 없음")
            if is_zoom_host_url(join_url):
                row_issues.append("active_url이 호스트 URL(/s/)")
            if counselor_url != join_url:
                row_issues.append("상담사·내담자 URL 불일치")
            if is_zoom_host_url(counselor_url):
                row_issues.append("상담사 URL이 호스트 전용")
            if join_url and not has_host_key:
                row_issues.append("호스트키 미설정(Claim Host 불가)")

            when = timezone.localtime(apt.scheduled_at)
            header = (
                f"{when:%Y-%m-%d %H:%M} | {apt.session_number}회차 | "
                f"{apt.counselor.name if apt.counselor else '-'} / {apt.client.name}"
            )
            if row_issues:
                issues += 1
                self.stdout.write(self.style.ERROR(f"FAIL {header}"))
                for msg in row_issues:
                    self.stdout.write(f"  · {msg}")
                self.stdout.write(f"  join: {join_url or '(없음)'}")
            else:
                self.stdout.write(self.style.SUCCESS(f"OK   {header}"))
                self.stdout.write(f"  join: {join_url}")
            self.stdout.write("")

        self.stdout.write(f"완료: {len(appointments)}건 중 문제 {issues}건")
        if issues and strict:
            raise CommandError(
                f"Zoom 점검 실패 {issues}건. audit_zoom_links 또는 Admin에서 join_url을 확인하세요."
            )
