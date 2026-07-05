"""잠금(locked)된 확정 예약 Zoom 호스트 강제 재배정 — 중복 슬롯 원타임 수정용."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone
from zoneinfo import ZoneInfo

from apps.counseling.models import CounselingMethod
from apps.counseling.ops_fixup import force_appointment_zoom_host
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.remote_zoom_capacity import (
    appointment_duration_minutes,
    zoom_host_assignable_for_slot,
)
from apps.scheduling.zoom_hosts import (
    assign_host_emails_for_appointments,
    email_for_host_id,
    host_id_for_email,
)

KST = ZoneInfo("Asia/Seoul")


class Command(BaseCommand):
    help = (
        "join_url·meeting_id가 잠긴 확정 비대면 예약을 지정 호스트로 Zoom 재생성합니다.\n"
        "동시간대 중복 호스트 1건을 host_02 등으로 옮길 때 사용하세요.\n"
        "예) python manage.py reassign_locked_zoom_host "
        '--client "구현정" --session 2 --from "2026-07-08 10:00" --host host_02\n'
        "예) python manage.py reassign_locked_zoom_host "
        '--date 2026-07-08 --hour 10 --move-client "구현정" --host host_02 --apply'
    )

    def add_arguments(self, parser):
        parser.add_argument("--client", default="", help="내담자 이름")
        parser.add_argument("--client-email", default="", help="내담자 이메일 (선택)")
        parser.add_argument("--counselor", default="", help="상담사 이름 (선택)")
        parser.add_argument("--session", type=int, default=None, help="회차 번호")
        parser.add_argument(
            "--from",
            dest="from_dt",
            default="",
            help='예약 일시 YYYY-MM-DD HH:MM (KST). "--date --hour" 대신 사용 가능',
        )
        parser.add_argument("--date", default="", help="슬롯 날짜 YYYY-MM-DD (KST)")
        parser.add_argument("--hour", type=int, default=None, help="슬롯 시간 0-23 (KST)")
        parser.add_argument(
            "--move-client",
            default="",
            help="--date/--hour 슬롯에서 host_02로 옮길 내담자 이름",
        )
        parser.add_argument(
            "--host",
            default="host_02",
            help="목표 호스트 ID (기본 host_02)",
        )
        parser.add_argument(
            "--host-email",
            default="",
            help="Licensed 풀 외 이메일 (host 대신)",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="실제 Zoom API 호출 및 DB 갱신",
        )
        parser.add_argument(
            "--notify",
            action="store_true",
            help="join_url 변경 시 내담자·상담사에게 알림",
        )
        parser.add_argument(
            "--skip-capacity-check",
            action="store_true",
            help="호스트 가용 검사 생략 (비권장)",
        )

    def handle(self, *args, **options):
        if options["apply"]:
            engine = connection.settings_dict.get("ENGINE", "")
            if "sqlite" in engine:
                raise CommandError("로컬 SQLite에서는 --apply를 사용할 수 없습니다.")

        move_client = (options.get("move_client") or "").strip()
        client_name = (options.get("client") or move_client or "").strip()
        from_dt = (options.get("from_dt") or "").strip()
        date_text = (options.get("date") or "").strip()
        hour = options.get("hour")

        if date_text and hour is not None:
            self._print_slot_audit(date_text, hour)
            if not client_name:
                raise CommandError(
                    "--move-client 또는 --client 로 재배정 대상 내담자를 지정하세요."
                )
            from_dt = f"{date_text} {hour:02d}:00"

        if not client_name:
            raise CommandError("--client (또는 --move-client) 가 필요합니다.")
        if not from_dt:
            raise CommandError('--from "YYYY-MM-DD HH:MM" 또는 --date + --hour 가 필요합니다.')

        host = (options.get("host") or "").strip()
        host_email = (options.get("host_email") or "").strip()
        if not host and not host_email:
            raise CommandError("--host 또는 --host-email 중 하나는 필수입니다.")

        target_email = (host_email or email_for_host_id(host) or "").strip()
        if not target_email:
            raise CommandError(f"호스트를 해석할 수 없습니다: {host!r}")

        session_number = options.get("session")
        if session_number is None:
            session_number = self._guess_session_number(
                client_name=client_name,
                from_dt=from_dt,
                counselor_name=(options.get("counselor") or "").strip(),
            )

        appointment = self._find_appointment(
            client_name=client_name,
            client_email=(options.get("client_email") or "").strip(),
            counselor_name=(options.get("counselor") or "").strip(),
            scheduled_label=from_dt,
            session_number=session_number,
        )
        if not appointment:
            raise CommandError(
                f"대상 예약 없음: {client_name} {session_number}회기 {from_dt}"
            )

        self._print_appointment_summary(appointment, target_email, host)

        if not options.get("skip_capacity_check"):
            self._verify_target_host_available(appointment, target_email, host)

        counselor_name = (options.get("counselor") or "").strip()
        if not counselor_name and appointment.counselor:
            counselor_name = appointment.counselor.name

        result = force_appointment_zoom_host(
            client_name=client_name,
            client_email=(options.get("client_email") or appointment.client.email or ""),
            counselor_name=counselor_name,
            scheduled_label=from_dt,
            host_id=host,
            host_email=host_email or None,
            session_number=session_number,
            dry_run=not options["apply"],
            force_locked=True,
            notify_link_change=bool(options.get("notify")),
        )
        self.stdout.write(f"{result.task}: {result.status} — {result.detail}")

        if result.status == "error":
            raise CommandError(result.detail)
        if result.status == "dry_run":
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN 완료. 실제 반영: 동일 명령에 --apply 추가"
                )
            )
            return

        appointment.refresh_from_db()
        zoom = getattr(appointment, "zoom_meeting", None)
        if zoom:
            self.stdout.write(
                self.style.SUCCESS(
                    f"완료: host={host_id_for_email(zoom.zoom_host_email)} "
                    f"meeting_id={zoom.zoom_meeting_id}\n"
                    f"  join_url={zoom.join_url}"
                )
            )
        self.stdout.write(
            "검증: python manage.py audit_zoom_hosts "
            f'--date {from_dt[:10]} --hour {int(from_dt[11:13])}'
        )

    def _print_slot_audit(self, date_text: str, hour: int) -> None:
        day = datetime.strptime(date_text, "%Y-%m-%d").date()
        slot_start = datetime.combine(day, datetime.min.time(), tzinfo=KST).replace(
            hour=hour, minute=0
        )
        slot_end = slot_start + timedelta(hours=1)
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
            f"=== {date_text} {hour:02d}:00 슬롯 — REMOTE CONFIRMED {len(overlap)}건 ==="
        )
        if not overlap:
            return

        expected = assign_host_emails_for_appointments(overlap)
        dup: dict[str, list[str]] = {}
        for apt in overlap:
            zm = getattr(apt, "zoom_meeting", None)
            stored = (getattr(zm, "zoom_host_email", None) or "").strip().lower()
            exp = (expected.get(str(apt.pk), "") or "").strip().lower()
            local = timezone.localtime(apt.scheduled_at)
            self.stdout.write(
                f"  {local:%H:%M} {apt.client.name} s{apt.session_number} "
                f"stored={host_id_for_email(stored) or '-'} "
                f"expected={host_id_for_email(exp) or '-'}"
            )
            if stored:
                dup.setdefault(stored, []).append(apt.client.name)

        conflicts = {k: v for k, v in dup.items() if len(v) > 1}
        if conflicts:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("같은 호스트로 저장된 내담자:"))
            for email, names in conflicts.items():
                self.stdout.write(
                    f"  {host_id_for_email(email)} ({email}): {', '.join(names)}"
                )
        self.stdout.write("")

    def _guess_session_number(
        self,
        *,
        client_name: str,
        from_dt: str,
        counselor_name: str,
    ) -> int:
        filters = {
            "client__name": client_name,
            "status": AppointmentStatus.CONFIRMED,
            "case__counseling_method": CounselingMethod.REMOTE,
        }
        if counselor_name:
            filters["counselor__name"] = counselor_name
        qs = Appointment.objects.filter(**filters).select_related("counselor")
        matched = [
            apt
            for apt in qs
            if timezone.localtime(apt.scheduled_at).strftime("%Y-%m-%d %H:%M") == from_dt
        ]
        if len(matched) == 1:
            return matched[0].session_number or 1
        if len(matched) > 1:
            lines = ", ".join(
                f"s{a.session_number}(id={a.pk})" for a in matched
            )
            raise CommandError(
                f"{from_dt} 에 {client_name} 예약이 여러 건입니다: {lines}. "
                "--session 을 지정하세요."
            )
        raise CommandError(
            f"{client_name} 의 {from_dt} 확정 비대면 예약을 찾지 못했습니다. "
            "--session 을 지정하세요."
        )

    def _find_appointment(
        self,
        *,
        client_name: str,
        client_email: str,
        counselor_name: str,
        scheduled_label: str,
        session_number: int,
    ) -> Appointment | None:
        filters = {
            "client__name": client_name,
            "session_number": session_number,
            "status": AppointmentStatus.CONFIRMED,
            "case__counseling_method": CounselingMethod.REMOTE,
        }
        if client_email:
            filters["client__email__iexact"] = client_email
        if counselor_name:
            filters["counselor__name"] = counselor_name

        for apt in (
            Appointment.objects.filter(**filters)
            .select_related("client", "counselor", "case", "zoom_meeting")
            .order_by("-scheduled_at")
        ):
            label = timezone.localtime(apt.scheduled_at).strftime("%Y-%m-%d %H:%M")
            if label == scheduled_label:
                return apt
        return None

    def _print_appointment_summary(
        self, appointment: Appointment, target_email: str, host: str
    ) -> None:
        zoom = getattr(appointment, "zoom_meeting", None)
        stored = (getattr(zoom, "zoom_host_email", None) or "").strip()
        local = timezone.localtime(appointment.scheduled_at)
        self.stdout.write("=== 재배정 대상 ===")
        self.stdout.write(f"  내담자: {appointment.client.name}")
        self.stdout.write(f"  회차: {appointment.session_number}")
        self.stdout.write(f"  일시: {local:%Y-%m-%d %H:%M} KST")
        self.stdout.write(
            f"  상담사: {appointment.counselor.name if appointment.counselor else '-'}"
        )
        self.stdout.write(
            f"  현재 host: {host_id_for_email(stored) or '-'} ({stored or 'empty'})"
        )
        self.stdout.write(
            f"  목표 host: {host or host_id_for_email(target_email)} ({target_email})"
        )
        if zoom and zoom.join_url:
            self.stdout.write(f"  현재 join_url: {zoom.join_url}")
        self.stdout.write("")

    def _verify_target_host_available(
        self, appointment: Appointment, target_email: str, host: str
    ) -> None:
        ok, host_id = zoom_host_assignable_for_slot(
            scheduled_at=appointment.scheduled_at,
            duration_minutes=appointment_duration_minutes(appointment),
            exclude_appointment_id=appointment.pk,
            candidate_id=str(appointment.pk),
        )
        expected_email = email_for_host_id(host_id) if host_id else ""
        if expected_email.lower() != target_email.lower():
            self.stdout.write(
                self.style.WARNING(
                    f"알고리즘 기대 호스트={host_id or '?'} ({expected_email}), "
                    f"요청={host} ({target_email}) — 슬롯에 다른 호스트가 비어 있을 수 있음"
                )
            )
        elif not ok:
            raise CommandError(
                f"{host} ({target_email}) 배정 불가 — 해당 슬롯 Zoom 호스트가 만석입니다."
            )
