"""1회성 — legacy sscukscu@gmail.com Zoom → host_02(sedulife) 재생성.

17건 UUID allowlist만 대상. host_02 충돌 검사 없음.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.counseling.models import CounselingMethod
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.utils import (
    ZoomAPIError,
    ZoomNotConfiguredError,
    delete_zoom_meeting,
    is_zoom_configured,
)
from apps.scheduling.zoom_hosts import email_for_host_id
from apps.sessions_app.models import ZoomMeeting

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

LEGACY_HOST_EMAIL = "sscukscu@gmail.com"

SCHEDULED_FROM_KST = timezone.make_aware(datetime(2026, 8, 19, 0, 0, 0), KST)

ALLOWED_APPOINTMENT_IDS: frozenset[UUID] = frozenset(
    {
        UUID("2f0d0208-89aa-488e-8e45-bfbc4d9af490"),
        UUID("df22511c-eec4-4e8e-9f5a-2c33d5fe1a11"),
        UUID("e70ce560-22d3-40b8-91a0-5fa92bd176cd"),
        UUID("5fcdea5f-563b-4808-adda-0cbf483316ad"),
        UUID("dda729f0-7dad-4870-be9e-725be752fb85"),
        UUID("dde01a00-7fce-489a-b544-a408e74c2b19"),
        UUID("216ed821-7e21-4a1d-972a-7eb9824eb139"),
        UUID("1149bf71-92d2-427e-94f4-7da00ac45878"),
        UUID("563a5e44-fb4e-4e6f-b58a-4927348fa20d"),
        UUID("f310f504-e034-4811-9bb3-fb95bb844d7b"),
        UUID("3d1a9a94-33d1-4515-8ac9-a6d979fb1e2f"),
        UUID("d618598c-de9b-4723-9cb7-bcb0aea5c833"),
        UUID("7ad2bd7b-aa39-4aa9-88e0-045e3be6bf8e"),
        UUID("4527bfe1-4dce-426f-8f87-8e5838518156"),
        UUID("95794817-20d7-4f79-9404-6296d09bf763"),
        UUID("996c970d-0224-4ef6-a1c9-4327fb900bc8"),
        UUID("131e2d67-cbca-42bc-9c0c-fc9f6d9fe463"),
    }
)

ACTION_RECREATE = "RECREATE_ZOOM_ON_HOST2"


def target_host_email() -> str:
    """host_02 Licensed 이메일 (현재 sedulife@mail.kcu.ac)."""
    return (email_for_host_id("host_02") or "sedulife@mail.kcu.ac").strip()


@dataclass
class MigrationTarget:
    appointment_id: UUID
    appointment: Appointment | None
    valid: bool
    invalid_reason: str = ""
    current_host: str = ""
    current_meeting_id: str = ""
    current_join_url: str = ""


def _validate_appointment(appointment: Appointment | None, appointment_id: UUID) -> MigrationTarget:
    if appointment is None:
        return MigrationTarget(
            appointment_id=appointment_id,
            appointment=None,
            valid=False,
            invalid_reason="Appointment not found in DB",
        )

    if appointment.status != AppointmentStatus.CONFIRMED:
        return MigrationTarget(
            appointment_id=appointment_id,
            appointment=appointment,
            valid=False,
            invalid_reason=f"status={appointment.status!r} (expected CONFIRMED)",
        )

    if appointment.case.counseling_method != CounselingMethod.REMOTE:
        return MigrationTarget(
            appointment_id=appointment_id,
            appointment=appointment,
            valid=False,
            invalid_reason=(
                f"counseling_method={appointment.case.counseling_method!r} "
                "(expected REMOTE)"
            ),
        )

    if appointment.scheduled_at < SCHEDULED_FROM_KST:
        scheduled_kst = timezone.localtime(appointment.scheduled_at, KST)
        return MigrationTarget(
            appointment_id=appointment_id,
            appointment=appointment,
            valid=False,
            invalid_reason=(
                f"scheduled_at={scheduled_kst} before cutoff "
                f"{SCHEDULED_FROM_KST.astimezone(KST)}"
            ),
        )

    zoom = getattr(appointment, "zoom_meeting", None)
    if zoom is None:
        return MigrationTarget(
            appointment_id=appointment_id,
            appointment=appointment,
            valid=False,
            invalid_reason="ZoomMeeting missing",
        )

    meeting_id = (zoom.zoom_meeting_id or "").strip()
    join_url = (zoom.join_url or "").strip()
    host = (zoom.zoom_host_email or "").strip()

    if not meeting_id:
        return MigrationTarget(
            appointment_id=appointment_id,
            appointment=appointment,
            valid=False,
            invalid_reason="zoom_meeting_id empty",
        )

    if host.lower() != LEGACY_HOST_EMAIL.lower():
        return MigrationTarget(
            appointment_id=appointment_id,
            appointment=appointment,
            valid=False,
            invalid_reason=(
                f"zoom_host_email={host!r} (expected {LEGACY_HOST_EMAIL!r})"
            ),
            current_host=host,
            current_meeting_id=meeting_id,
            current_join_url=join_url,
        )

    return MigrationTarget(
        appointment_id=appointment_id,
        appointment=appointment,
        valid=True,
        current_host=host,
        current_meeting_id=meeting_id,
        current_join_url=join_url,
    )


def build_migration_plan() -> list[MigrationTarget]:
    """allowlist 17건 preflight (READ-ONLY)."""
    appointments = {
        row.pk: row
        for row in Appointment.objects.filter(pk__in=ALLOWED_APPOINTMENT_IDS)
        .select_related("client", "counselor", "case", "zoom_meeting")
        .order_by("scheduled_at")
    }

    plan: list[MigrationTarget] = []
    for appointment_id in sorted(ALLOWED_APPOINTMENT_IDS, key=str):
        plan.append(_validate_appointment(appointments.get(appointment_id), appointment_id))
    return plan


def format_dry_run_block(item: MigrationTarget) -> str:
    target = target_host_email()
    lines = [
        "---",
        f"appointment UUID: {item.appointment_id}",
    ]
    apt = item.appointment
    if apt is None:
        lines.append("client: (not found)")
        lines.append("counselor: (not found)")
        lines.append("scheduled_at (KST): (not found)")
    else:
        lines.extend(
            [
                f"client: {apt.client.name}",
                f"counselor: {apt.counselor.name}",
                f"scheduled_at (KST): {timezone.localtime(apt.scheduled_at, KST)}",
            ]
        )
    lines.extend(
        [
            f"current zoom_host_email: {item.current_host or '(empty)'}",
            f"current zoom_meeting_id: {item.current_meeting_id or '(empty)'}",
            f"current join_url: {item.current_join_url or '(empty)'}",
            f"target host: {target}",
            f"action: {ACTION_RECREATE if item.valid else 'SKIP (invalid)'}",
        ]
    )
    if not item.valid:
        lines.append(f"invalid_reason: {item.invalid_reason}")
    return "\n".join(lines)


def apply_migration_item(
    item: MigrationTarget,
    *,
    notify_link_change: bool = False,
) -> dict:
    """단건 apply — Zoom 생성 → DB 검증 → 구 회의 삭제 (보상 포함)."""
    target = target_host_email()
    executed_at = timezone.now().isoformat()
    backup: dict = {
        "appointment_id": str(item.appointment_id),
        "scheduled_at": "",
        "old_host": item.current_host,
        "new_host": target,
        "old_zoom_meeting_id": item.current_meeting_id,
        "old_join_url": item.current_join_url,
        "new_zoom_meeting_id": "",
        "new_join_url": "",
        "db_saved": False,
        "old_zoom_deleted": False,
        "executed_at": executed_at,
        "status": "skipped",
        "error_message": "",
    }

    if not item.valid or item.appointment is None:
        backup["error_message"] = item.invalid_reason or "invalid target"
        return backup

    if not is_zoom_configured():
        backup["status"] = "error"
        backup["error_message"] = "Zoom API not configured"
        return backup

    apt = item.appointment
    backup["scheduled_at"] = timezone.localtime(apt.scheduled_at, KST).isoformat()

    stored_host = (item.current_host or "").strip().lower()
    if stored_host != LEGACY_HOST_EMAIL.lower():
        backup["status"] = "error"
        backup["error_message"] = (
            f"preflight host mismatch at apply time: {item.current_host!r}"
        )
        return backup

    old_meeting_id = item.current_meeting_id
    new_meeting_id = ""

    from apps.scheduling.services import _create_zoom_meeting_for_appointment

    try:
        _create_zoom_meeting_for_appointment(
            apt,
            host_user_email=target,
            notify_link_change=notify_link_change,
        )

        refreshed = ZoomMeeting.objects.filter(appointment_id=apt.pk).first()
        if refreshed is None:
            raise ZoomAPIError("ZoomMeeting row missing after save")

        new_meeting_id = (refreshed.zoom_meeting_id or "").strip()
        new_join = (refreshed.join_url or "").strip()
        new_host = (refreshed.zoom_host_email or "").strip()

        if not new_meeting_id or not new_join:
            raise ZoomAPIError("new Zoom meeting_id/join_url missing after save")

        if new_host.lower() != target.lower():
            raise ZoomAPIError(
                f"zoom_host_email mismatch after save: {new_host!r} != {target!r}"
            )

        if new_meeting_id == old_meeting_id:
            raise ZoomAPIError(
                "new meeting_id equals old meeting_id — aborting without delete"
            )

        backup.update(
            {
                "new_zoom_meeting_id": new_meeting_id,
                "new_join_url": new_join,
                "db_saved": True,
                "status": "success",
            }
        )

        if old_meeting_id:
            try:
                delete_zoom_meeting(old_meeting_id)
                backup["old_zoom_deleted"] = True
            except Exception as exc:
                backup["status"] = "success_old_delete_failed"
                backup["error_message"] = f"old meeting delete failed: {exc}"
                logger.error(
                    "legacy_sscukscu migration: old Zoom delete failed apt=%s old_id=%s: %s",
                    apt.pk,
                    old_meeting_id,
                    exc,
                )

        return backup

    except Exception as exc:
        if new_meeting_id:
            try:
                delete_zoom_meeting(new_meeting_id)
            except Exception as cleanup_exc:
                logger.error(
                    "legacy_sscukscu migration compensation delete failed new_id=%s: %s",
                    new_meeting_id,
                    cleanup_exc,
                )
        backup.update(
            {
                "new_zoom_meeting_id": new_meeting_id or "",
                "new_join_url": "",
                "db_saved": False,
                "status": "error",
                "error_message": str(exc),
            }
        )
        return backup


def apply_migration_plan(
    plan: list[MigrationTarget],
    *,
    notify_link_change: bool = False,
    output_dir: Path | None = None,
) -> list[dict]:
    if not is_zoom_configured():
        raise ZoomNotConfiguredError(
            "Zoom API 설정이 없습니다. ZOOM_* 환경 변수를 확인해 주세요."
        )

    results: list[dict] = []
    for item in plan:
        if not item.valid:
            continue
        results.append(
            apply_migration_item(item, notify_link_change=notify_link_change)
        )

    if output_dir and results:
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        json_path = output_dir / f"legacy_sscukscu_migration_{stamp}.json"
        csv_path = output_dir / f"legacy_sscukscu_migration_{stamp}.csv"
        json_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        fieldnames = [
            "appointment_id",
            "scheduled_at",
            "old_host",
            "new_host",
            "old_zoom_meeting_id",
            "new_zoom_meeting_id",
            "old_join_url",
            "new_join_url",
            "db_saved",
            "old_zoom_deleted",
            "executed_at",
            "status",
            "error_message",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(results)

    return results
