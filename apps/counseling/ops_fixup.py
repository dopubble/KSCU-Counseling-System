"""운영 DB 일회성·반복 적용 수정 작업."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from apps.accounts.client_purge import purge_clients_by_name
from apps.counseling.session1_bulk_import import force_client_session1_schedule

KIM_JANGSEOYUL_NAME = "김장서율"
KIM_JANGSEOYUL_STUDENT_IDS = ("261110004", "26111004")

KIM_AREUM_NAME = "김아름"
KIM_AREUM_EMAIL = "arsui90@naver.com"
KIM_AREUM_SESSION1_AT = datetime(2026, 6, 25, 16, 0, tzinfo=ZoneInfo("Asia/Seoul"))


@dataclass
class OpsFixupLine:
    task: str
    status: str
    detail: str


def apply_ops_production_fixup_june2026(*, dry_run: bool = True) -> list[OpsFixupLine]:
    lines: list[OpsFixupLine] = []

    purge_result = purge_clients_by_name(
        KIM_JANGSEOYUL_NAME,
        student_id_variants=KIM_JANGSEOYUL_STUDENT_IDS,
        dry_run=dry_run,
    )
    if purge_result.deleted_users:
        lines.append(
            OpsFixupLine(
                "purge_kim_jangseoyul",
                "ok",
                f"삭제 {purge_result.deleted_users}명",
            )
        )
    else:
        lines.append(
            OpsFixupLine(
                "purge_kim_jangseoyul",
                "skip",
                "대상 없음(이미 삭제됨)",
            )
        )

    session_result = force_client_session1_schedule(
        client_name=KIM_AREUM_NAME,
        client_email=KIM_AREUM_EMAIL,
        scheduled_at=KIM_AREUM_SESSION1_AT,
        dry_run=dry_run,
    )
    lines.append(
        OpsFixupLine(
            "force_kim_areum_session1",
            session_result.status,
            session_result.detail,
        )
    )
    return lines
