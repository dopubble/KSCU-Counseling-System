"""내담자 계정에 상담 신청(매칭대기)을 일괄 생성."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from django.utils import timezone

from apps.accounts.models import ClientProfile, User, UserRole, UserStatus
from apps.counseling.constants import DEFAULT_COUNSELING_TYPES, normalize_counseling_types
from apps.counseling.models import ApplicationStatus, CounselingApplication

DEFAULT_REASON = "관리자 일괄 접수 (내담자 사전 등록)"
DEFAULT_PREFERRED_TIME = time(10, 0)
DEFAULT_DAYS_AHEAD = 7


@dataclass
class SeedApplicationRow:
    email: str
    counseling_types: list[str] = field(default_factory=lambda: list(DEFAULT_COUNSELING_TYPES))
    reason: str = DEFAULT_REASON
    preferred_date: date | None = None
    preferred_time: time | None = None
    line_no: int = 0


@dataclass
class SeedApplicationResult:
    email: str
    action: str
    message: str = ""
    line_no: int = 0
    application_id: str | None = None


@dataclass
class SeedApplicationSummary:
    created: int = 0
    skipped: int = 0
    errors: int = 0
    results: list[SeedApplicationResult] = field(default_factory=list)


def _default_preferred_date() -> date:
    return timezone.localdate() + timedelta(days=DEFAULT_DAYS_AHEAD)


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"날짜 형식 오류: {text!r} (YYYY-MM-DD)")


def _parse_time(value: str) -> time | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"시간 형식 오류: {text!r} (HH:MM)")


def _read_csv_with_encoding(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"CSV 인코딩을 읽을 수 없습니다: {path}")

    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise ValueError(f"CSV 헤더가 없습니다: {path}")

    normalized_fieldnames = [
        (name or "").strip().lower() for name in reader.fieldnames
    ]
    rows: list[dict[str, str]] = []
    for line_no, row in enumerate(reader, start=2):
        normalized = {
            normalized_fieldnames[i]: (value or "").strip()
            for i, value in enumerate(row.values())
        }
        rows.append({"__line_no__": str(line_no), **normalized})
    return normalized_fieldnames, rows


def _column(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value:
            return value.strip()
    return ""


def read_seed_rows(path: Path) -> list[SeedApplicationRow]:
    _, rows = _read_csv_with_encoding(path)
    if not rows:
        return []

    result: list[SeedApplicationRow] = []
    for row in rows:
        line_no = int(row.get("__line_no__", "0") or 0)
        email = _column(row, "email", "이메일", "e-mail")
        if not email:
            raise ValueError(f"{line_no}행: email(이메일) 컬럼이 비어 있습니다.")

        types_raw = _column(row, "counseling_types", "counseling_type", "상담유형", "상담 유형")
        counseling_types = normalize_counseling_types(types_raw) if types_raw else list(DEFAULT_COUNSELING_TYPES)
        reason = _column(row, "reason", "상담사유", "사유") or DEFAULT_REASON

        preferred_date_raw = _column(row, "preferred_date", "희망일", "희망일자")
        preferred_time_raw = _column(row, "preferred_time", "희망시간", "희망 시간")

        preferred_date = _parse_date(preferred_date_raw) if preferred_date_raw else None
        preferred_time = _parse_time(preferred_time_raw) if preferred_time_raw else None

        result.append(
            SeedApplicationRow(
                email=email.lower(),
                counseling_types=counseling_types,
                reason=reason,
                preferred_date=preferred_date,
                preferred_time=preferred_time,
                line_no=line_no,
            )
        )
    return result


def client_has_pending_application(client: User) -> bool:
    """접수·매칭대기 중인 신청이 있으면 True."""
    return CounselingApplication.objects.filter(
        client=client,
        status__in=(
            ApplicationStatus.RECEIVED,
            ApplicationStatus.WAITING_MATCH,
        ),
    ).exists()


def client_has_active_counseling(client: User) -> bool:
    """담당 상담사가 배정된 진행 중 사례가 있으면 True."""
    from apps.counseling.models import Case, CaseStatus

    return Case.objects.filter(
        client=client,
        status=CaseStatus.ACTIVE,
        counselor_id__isnull=False,
    ).exists()


def _profile_snapshot(user: User) -> dict:
    profile, _ = ClientProfile.objects.get_or_create(user=user)
    return {
        "student_id": profile.student_id or "",
        "birth_date": profile.birth_date,
        "department": profile.department or "",
    }


def create_application_for_client(
    client: User,
    *,
    counseling_types: list[str] | None = None,
    reason: str = DEFAULT_REASON,
    preferred_date: date | None = None,
    preferred_time: time | None = None,
    admin_seeded: bool = True,
) -> CounselingApplication:
    """내담자 1명에게 상담 신청서를 생성 (웹 /counseling/apply/ 와 동일한 필드 구조)."""
    if client.role != UserRole.CLIENT:
        raise ValueError(f"내담자 계정이 아닙니다: {client.email}")

    types = normalize_counseling_types(counseling_types or DEFAULT_COUNSELING_TYPES)
    if not types:
        raise ValueError("상담 유형이 비어 있습니다.")

    snapshot = _profile_snapshot(client)
    birth_date = snapshot["birth_date"]
    preferred_date = preferred_date or _default_preferred_date()
    preferred_time = preferred_time or DEFAULT_PREFERRED_TIME

    preferred_schedule = {
        "student_id": snapshot["student_id"],
        "birth_date": birth_date.isoformat() if birth_date else "",
        "department": snapshot["department"],
        "preferred_date": preferred_date.isoformat(),
        "preferred_time": preferred_time.strftime("%H:%M"),
    }
    if admin_seeded:
        preferred_schedule["admin_seeded"] = True

    return CounselingApplication.objects.create(
        client=client,
        counseling_types=types,
        reason=reason,
        preferred_schedule=preferred_schedule,
        status=ApplicationStatus.WAITING_MATCH,
    )


def seed_application_rows(
    rows: list[SeedApplicationRow],
    *,
    skip_existing: bool = True,
    dry_run: bool = False,
) -> SeedApplicationSummary:
    summary = SeedApplicationSummary()

    for row in rows:
        try:
            client = User.objects.filter(email__iexact=row.email).first()
            if not client:
                summary.errors += 1
                summary.results.append(
                    SeedApplicationResult(
                        row.email,
                        "error",
                        "등록된 사용자를 찾을 수 없습니다.",
                        row.line_no,
                    )
                )
                continue

            if client.role != UserRole.CLIENT:
                summary.errors += 1
                summary.results.append(
                    SeedApplicationResult(
                        row.email,
                        "error",
                        f"내담자가 아닌 계정입니다 (role={client.role}).",
                        row.line_no,
                    )
                )
                continue

            if client.status != UserStatus.ACTIVE:
                summary.errors += 1
                summary.results.append(
                    SeedApplicationResult(
                        row.email,
                        "error",
                        f"비활성 계정입니다 (status={client.status}).",
                        row.line_no,
                    )
                )
                continue

            if skip_existing and client_has_pending_application(client):
                summary.skipped += 1
                summary.results.append(
                    SeedApplicationResult(
                        row.email,
                        "skipped",
                        "이미 접수/매칭대기 신청이 있습니다.",
                        row.line_no,
                    )
                )
                continue

            if skip_existing and client_has_active_counseling(client):
                summary.skipped += 1
                summary.results.append(
                    SeedApplicationResult(
                        row.email,
                        "skipped",
                        "이미 진행 중인 상담(상담사 배정 완료)이 있습니다.",
                        row.line_no,
                    )
                )
                continue

            types_label = ", ".join(row.counseling_types)
            if dry_run:
                summary.created += 1
                summary.results.append(
                    SeedApplicationResult(
                        row.email,
                        "would_create",
                        f"{types_label} / 매칭대기",
                        row.line_no,
                    )
                )
                continue

            application = create_application_for_client(
                client,
                counseling_types=row.counseling_types,
                reason=row.reason,
                preferred_date=row.preferred_date,
                preferred_time=row.preferred_time,
            )
            summary.created += 1
            summary.results.append(
                SeedApplicationResult(
                    row.email,
                    "created",
                    "",
                    row.line_no,
                    application_id=str(application.pk),
                )
            )
        except Exception as exc:
            summary.errors += 1
            summary.results.append(
                SeedApplicationResult(row.email, "error", str(exc), row.line_no)
            )

    return summary


def build_rows_for_all_active_clients() -> list[SeedApplicationRow]:
    clients = User.objects.filter(role=UserRole.CLIENT, status=UserStatus.ACTIVE).order_by(
        "email"
    )
    return [
        SeedApplicationRow(email=client.email.lower(), line_no=idx)
        for idx, client in enumerate(clients, start=1)
    ]
