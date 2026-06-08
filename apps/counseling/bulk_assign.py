"""CSV 기반 내담자–상담사 매칭 일괄 처리."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.models import ApplicationStatus, Case, CaseStatus, CounselingApplication
from apps.counseling.seed_applications import create_application_for_client
from apps.counseling.services import assign_counselor, reassign_counselor

HEADER_ALIASES: dict[str, str] = {
    "email": "email",
    "이메일": "email",
    "내담자": "email",
    "내담자이메일": "email",
    "내담자 이메일": "email",
    "내담자 이메일 주소": "email",
    "client_email": "email",
    "counselor": "counselor_name",
    "counselor_name": "counselor_name",
    "상담사": "counselor_name",
    "상담자": "counselor_name",
    "상담사명": "counselor_name",
    "counselor_email": "counselor_email",
    "상담사이메일": "counselor_email",
}


@dataclass
class AssignCounselorRow:
    email: str
    counselor_name: str
    counselor_email: str = ""
    line_no: int = 0


@dataclass
class AssignCounselorResult:
    email: str
    action: str
    message: str = ""
    line_no: int = 0
    case_number: str = ""


@dataclass
class AssignCounselorSummary:
    assigned: int = 0
    reassigned: int = 0
    skipped: int = 0
    errors: int = 0
    results: list[AssignCounselorResult] = field(default_factory=list)


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
        raise ValueError("CSV 헤더가 없습니다.")
    return list(reader.fieldnames), list(reader)


def _normalize_header(name: str) -> str:
    key = (name or "").strip().lower()
    return HEADER_ALIASES.get(key, key)


def read_assign_rows(path: Path) -> list[AssignCounselorRow]:
    fieldnames, rows = _read_csv_with_encoding(path)
    normalized = {_normalize_header(h): h for h in fieldnames if h}

    email_key = normalized.get("email")
    counselor_key = normalized.get("counselor_name")
    counselor_email_key = normalized.get("counselor_email")

    if not email_key or not counselor_key:
        raise ValueError(
            "CSV에 내담자 이메일(email)과 상담사명(상담자/counselor) 컬럼이 필요합니다."
        )

    result: list[AssignCounselorRow] = []
    for line_no, row in enumerate(rows, start=2):
        email = (row.get(email_key) or "").strip().lower()
        counselor_name = (row.get(counselor_key) or "").strip()
        counselor_email = ""
        if counselor_email_key:
            counselor_email = (row.get(counselor_email_key) or "").strip().lower()
        if not email and not counselor_name:
            continue
        if not email:
            raise ValueError(f"{line_no}행: 내담자 이메일이 비어 있습니다.")
        if not counselor_name:
            raise ValueError(f"{line_no}행: 상담사명이 비어 있습니다.")
        result.append(
            AssignCounselorRow(
                email=email,
                counselor_name=counselor_name,
                counselor_email=counselor_email,
                line_no=line_no,
            )
        )
    return result


def _resolve_client(email: str) -> User | None:
    return User.objects.filter(email__iexact=email, role=UserRole.CLIENT).first()


def _resolve_counselor(name: str, email: str = "") -> tuple[User | None, str]:
    qs = User.objects.filter(role=UserRole.COUNSELOR, status=UserStatus.ACTIVE)
    if email:
        counselor = qs.filter(email__iexact=email).first()
        if not counselor:
            return None, f"상담사 이메일을 찾을 수 없습니다: {email}"
        if counselor.name.strip() != name.strip():
            return (
                counselor,
                f"상담사명 불일치 (CSV={name}, DB={counselor.name}) — 이메일 기준으로 배정합니다.",
            )
        return counselor, ""

    matches = list(qs.filter(name=name))
    if not matches:
        return None, f"상담사를 찾을 수 없습니다: {name!r}"
    if len(matches) > 1:
        emails = ", ".join(u.email for u in matches)
        return None, f"동명이인 상담사 {len(matches)}명 — counselor_email 컬럼으로 지정하세요 ({emails})"
    return matches[0], ""


def _find_assignable_application(client: User) -> CounselingApplication | None:
    assignable_statuses = (
        ApplicationStatus.RECEIVED,
        ApplicationStatus.WAITING_MATCH,
        ApplicationStatus.MATCHED,
        ApplicationStatus.IN_PROGRESS,
    )
    return (
        CounselingApplication.objects.filter(
            client=client,
            status__in=assignable_statuses,
        )
        .select_related("case")
        .order_by("-created_at")
        .first()
    )


def _active_case(client: User) -> Case | None:
    return (
        Case.objects.filter(client=client, status=CaseStatus.ACTIVE)
        .select_related("counselor", "application")
        .order_by("-opened_at")
        .first()
    )


@transaction.atomic
def _assign_row(
    row: AssignCounselorRow,
    *,
    total_sessions: int,
    create_missing_application: bool,
    skip_same: bool,
    dry_run: bool,
) -> AssignCounselorResult:
    client = _resolve_client(row.email)
    if not client:
        return AssignCounselorResult(
            row.email, "error", "등록된 내담자를 찾을 수 없습니다.", row.line_no
        )

    counselor, counselor_note = _resolve_counselor(row.counselor_name, row.counselor_email)
    if not counselor:
        return AssignCounselorResult(row.email, "error", counselor_note, row.line_no)

    active_case = _active_case(client)
    if active_case and active_case.counselor_id == counselor.pk:
        if skip_same:
            msg = counselor_note or "이미 동일 상담사가 배정되어 있습니다."
            return AssignCounselorResult(
                row.email,
                "skipped",
                msg,
                row.line_no,
                active_case.case_number,
            )

    application = None
    if active_case:
        application = active_case.application
    else:
        application = _find_assignable_application(client)

    if not application and create_missing_application:
        if dry_run:
            action = "would_assign"
            msg = f"{counselor.name} 상담사 배정 예정 (신청 생성 후)"
            if counselor_note:
                msg = f"{msg} — {counselor_note}"
            return AssignCounselorResult(row.email, action, msg, row.line_no)
        application = create_application_for_client(client)

    if not application:
        return AssignCounselorResult(
            row.email,
            "error",
            "배정 가능한 상담 신청이 없습니다. --create-application 으로 신청을 먼저 생성하세요.",
            row.line_no,
        )

    try:
        existing_case = application.case
    except Case.DoesNotExist:
        existing_case = None

    if existing_case is None and active_case:
        existing_case = active_case

    if dry_run:
        action = "would_reassign" if existing_case else "would_assign"
        msg = f"{counselor.name} 상담사"
        if counselor_note:
            msg = f"{msg} — {counselor_note}"
        return AssignCounselorResult(
            row.email,
            action,
            msg,
            row.line_no,
            existing_case.case_number if existing_case else "",
        )

    if existing_case:
        case = reassign_counselor(existing_case, counselor)
        action = "reassigned"
        msg = f"{counselor.name} 상담사로 변경 ({case.case_number})"
    else:
        case = assign_counselor(application, counselor, total_sessions=total_sessions)
        action = "assigned"
        msg = f"{counselor.name} 상담사 배정 ({case.case_number}, {case.total_sessions}회)"

    if counselor_note:
        msg = f"{msg} — {counselor_note}"

    return AssignCounselorResult(row.email, action, msg, row.line_no, case.case_number)


def assign_counselor_rows(
    rows: list[AssignCounselorRow],
    *,
    total_sessions: int = 10,
    create_missing_application: bool = False,
    skip_same: bool = True,
    dry_run: bool = False,
) -> AssignCounselorSummary:
    summary = AssignCounselorSummary()

    for row in rows:
        result = _assign_row(
            row,
            total_sessions=total_sessions,
            create_missing_application=create_missing_application,
            skip_same=skip_same,
            dry_run=dry_run,
        )
        summary.results.append(result)

        if result.action in ("assigned", "would_assign"):
            summary.assigned += 1
        elif result.action in ("reassigned", "would_reassign"):
            summary.reassigned += 1
        elif result.action == "skipped":
            summary.skipped += 1
        elif result.action == "error":
            summary.errors += 1

    return summary
