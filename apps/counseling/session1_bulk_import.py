"""1회기 매칭·예약 일괄 주입 — 기존 매칭 삭제 후 이름 기반 재배정."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User, UserRole, UserStatus
from apps.counseling.bulk_assign import _find_assignable_application, _resolve_counselor
from apps.counseling.models import (
    ApplicationStatus,
    Case,
    CaseStatus,
    CounselingApplication,
    CounselingMethod,
    SessionScheduleChangeRequest,
)
from apps.counseling.services import _counseling_method_for_client
from apps.counseling.seed_applications import create_application_for_client
from apps.counseling.services import assign_counselor, reassign_counselor
from apps.documents.models import CounselorAssignmentSubmission, SessionMaterial
from apps.scheduling.forms import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import confirm_appointment_with_zoom


@dataclass
class Session1MatchRow:
    counselor_name: str
    client_name: str
    first_session: datetime
    line_no: int = 0
    counseling_method: str | None = None


@dataclass
class ClearSummary:
    cases_touched: int = 0
    appointments_deleted: int = 0
    schedule_requests_deleted: int = 0
    session_materials_deleted: int = 0
    assignments_deleted: int = 0
    applications_reset: int = 0


@dataclass
class ImportRowResult:
    client_name: str
    counselor_name: str
    first_session: datetime | None
    action: str
    message: str = ""
    case_number: str = ""


@dataclass
class ImportSummary:
    cleared: ClearSummary = field(default_factory=ClearSummary)
    assigned: int = 0
    reassigned: int = 0
    session1_created: int = 0
    session1_confirmed: int = 0
    errors: int = 0
    results: list[ImportRowResult] = field(default_factory=list)


class Session1ImportError(Exception):
    """일괄 주입 전체 실패 (트랜잭션 롤백용)."""


def load_session1_matches(path: Path) -> list[Session1MatchRow]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("JSON은 배열이어야 합니다.")

    rows: list[Session1MatchRow] = []
    for line_no, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{line_no}번째 항목: 객체가 아닙니다.")
        counselor = (item.get("counselor") or "").strip()
        client = (item.get("client") or "").strip()
        first_session_raw = (item.get("first_session") or "").strip()
        if not counselor or not client or not first_session_raw:
            raise ValueError(f"{line_no}번째 항목: counselor, client, first_session 필요")
        try:
            naive = datetime.strptime(first_session_raw, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError(
                f"{line_no}번째 first_session 형식 오류: {first_session_raw!r} (YYYY-MM-DD HH:MM)"
            ) from exc
        first_session = timezone.make_aware(naive, timezone.get_current_timezone())
        counseling_method = _parse_counseling_method(item.get("counseling_method"))
        rows.append(
            Session1MatchRow(
                counselor_name=counselor,
                client_name=client,
                first_session=first_session,
                line_no=line_no,
                counseling_method=counseling_method,
            )
        )

    _validate_match_rows(rows)
    return rows


def _parse_counseling_method(raw) -> str | None:
    text = (raw or "").strip().upper()
    if not text:
        return None
    if text in {"REMOTE", "ONLINE", "비대면"}:
        return CounselingMethod.REMOTE
    if text in {"IN_PERSON", "OFFLINE", "대면"}:
        return CounselingMethod.IN_PERSON
    raise ValueError(
        f"counseling_method 값 오류: {raw!r} (REMOTE/IN_PERSON 또는 비대면/대면)"
    )


def _validate_match_rows(rows: list[Session1MatchRow]) -> None:
    if not rows:
        raise ValueError("매칭 데이터가 비어 있습니다.")

    client_names = [r.client_name for r in rows]
    if len(client_names) != len(set(client_names)):
        dupes = sorted({n for n in client_names if client_names.count(n) > 1})
        raise ValueError(f"내담자 중복: {', '.join(dupes)}")

    counselor_slots: dict[tuple[str, datetime], str] = {}
    for row in rows:
        key = (row.counselor_name, row.first_session)
        if key in counselor_slots:
            raise ValueError(
                f"상담사 {row.counselor_name} 1회기 시간 중복: "
                f"{timezone.localtime(row.first_session):%Y-%m-%d %H:%M} "
                f"({counselor_slots[key]} vs {row.client_name})"
            )
        counselor_slots[key] = row.client_name


def _build_name_index(role: str) -> dict[str, list[User]]:
    index: dict[str, list[User]] = defaultdict(list)
    qs = User.objects.filter(role=role, status=UserStatus.ACTIVE).order_by("email")
    for user in qs:
        name = (user.name or "").strip()
        if name:
            index[name].append(user)
    return index


def resolve_client_by_name(
    name: str,
    *,
    client_index: dict[str, list[User]] | None = None,
) -> tuple[User | None, str]:
    index = client_index or _build_name_index(UserRole.CLIENT)
    matches = index.get(name.strip(), [])
    if not matches:
        return None, f"내담자를 찾을 수 없습니다: {name!r}"
    if len(matches) > 1:
        emails = ", ".join(u.email for u in matches)
        return None, f"동명이인 내담자 {len(matches)}명 — 이메일로 구분 필요 ({emails})"
    return matches[0], ""


def validate_rows_resolvable(rows: list[Session1MatchRow]) -> list[str]:
    """이름 → User 조회 가능 여부 사전 검증. 오류 메시지 목록 반환."""
    client_index = _build_name_index(UserRole.CLIENT)
    errors: list[str] = []
    for row in rows:
        _, client_err = resolve_client_by_name(row.client_name, client_index=client_index)
        if client_err:
            errors.append(f"{row.line_no}행 {row.client_name}: {client_err}")
        counselor, counselor_err = _resolve_counselor(row.counselor_name)
        if not counselor:
            errors.append(f"{row.line_no}행 {row.counselor_name}: {counselor_err}")
    return errors


def _case_ids_for_clients(client_ids: list) -> list:
    return list(Case.objects.filter(client_id__in=client_ids).values_list("pk", flat=True))


def clear_matching_data(
    *,
    client_users: list[User],
    dry_run: bool = True,
) -> ClearSummary:
    """대상 내담자의 매칭·예약·회기 부가 데이터 삭제 후 상담사 배정 해제."""
    summary = ClearSummary()
    if not client_users:
        return summary

    client_ids = [u.pk for u in client_users]
    case_ids = _case_ids_for_clients(client_ids)
    summary.cases_touched = len(case_ids)
    if not case_ids:
        return summary

    apt_qs = Appointment.objects.filter(case_id__in=case_ids)
    sched_qs = SessionScheduleChangeRequest.objects.filter(case_id__in=case_ids)
    material_qs = SessionMaterial.objects.filter(case_id__in=case_ids, is_shared=False)
    assign_qs = CounselorAssignmentSubmission.objects.filter(case_id__in=case_ids)
    application_ids = list(
        Case.objects.filter(pk__in=case_ids).values_list("application_id", flat=True)
    )

    summary.appointments_deleted = apt_qs.count()
    summary.schedule_requests_deleted = sched_qs.count()
    summary.session_materials_deleted = material_qs.count()
    summary.assignments_deleted = assign_qs.count()
    summary.applications_reset = len(application_ids)

    if dry_run:
        return summary

    assign_qs.delete()
    material_qs.delete()
    sched_qs.delete()
    apt_qs.delete()

    Case.objects.filter(pk__in=case_ids).update(
        counselor=None,
        zoom_meeting_url="",
        status=CaseStatus.ACTIVE,
        remaining_sessions=F("total_sessions"),
    )
    CounselingApplication.objects.filter(pk__in=application_ids).update(
        status=ApplicationStatus.WAITING_MATCH,
    )
    return summary


def _active_case(client: User) -> Case | None:
    return (
        Case.objects.filter(client=client, status=CaseStatus.ACTIVE)
        .select_related("application", "counselor")
        .order_by("-opened_at")
        .first()
    )


def _import_one_row(
    row: Session1MatchRow,
    *,
    client_index: dict[str, list[User]],
    total_sessions: int,
    create_missing_application: bool,
    with_zoom: bool,
    dry_run: bool,
) -> ImportRowResult:
    client, client_err = resolve_client_by_name(row.client_name, client_index=client_index)
    if not client:
        return ImportRowResult(
            row.client_name,
            row.counselor_name,
            row.first_session,
            "error",
            client_err,
        )

    counselor, counselor_err = _resolve_counselor(row.counselor_name)
    if not counselor:
        return ImportRowResult(
            row.client_name,
            row.counselor_name,
            row.first_session,
            "error",
            counselor_err,
        )

    if dry_run:
        action = "would_import"
        msg = (
            f"{counselor.name} · 1회기 {timezone.localtime(row.first_session):%Y-%m-%d %H:%M}"
        )
        if counselor_err:
            msg = f"{msg} — {counselor_err}"
        return ImportRowResult(
            row.client_name,
            row.counselor_name,
            row.first_session,
            action,
            msg,
        )

    application = _find_assignable_application(client)
    active_case = _active_case(client)
    if application is None and active_case:
        application = active_case.application
    if application is None and create_missing_application:
        application = create_application_for_client(client)
    if application is None:
        return ImportRowResult(
            row.client_name,
            row.counselor_name,
            row.first_session,
            "error",
            "배정 가능한 상담 신청이 없습니다. --create-application 사용",
        )

    try:
        existing_case = application.case
    except Case.DoesNotExist:
        existing_case = active_case

    if existing_case:
        case = reassign_counselor(existing_case, counselor)
        action = "reassigned"
    else:
        case = assign_counselor(application, counselor, total_sessions=total_sessions)
        action = "assigned"

    case.total_sessions = total_sessions
    case.remaining_sessions = total_sessions
    case.counseling_method = row.counseling_method or _counseling_method_for_client(client)
    case.status = CaseStatus.ACTIVE
    case.closed_at = None
    case.save(
        update_fields=[
            "total_sessions",
            "remaining_sessions",
            "counseling_method",
            "status",
            "closed_at",
            "counselor",
        ]
    )

    appointment = Appointment.objects.create(
        case=case,
        counselor=counselor,
        client=client,
        scheduled_at=row.first_session,
        duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
        status=AppointmentStatus.PENDING,
        session_number=1,
        request_message="관리자 일괄 1회기 매칭 주입",
    )

    use_zoom = with_zoom and case.counseling_method == CounselingMethod.REMOTE
    if use_zoom:
        confirm_appointment_with_zoom(appointment, notify=False)
    else:
        appointment.status = AppointmentStatus.CONFIRMED
        appointment.confirmed_at = timezone.now()
        appointment.save(update_fields=["status", "confirmed_at", "updated_at"])

    msg = f"{case.case_number} · 1회기 {timezone.localtime(row.first_session):%Y-%m-%d %H:%M}"
    if counselor_err:
        msg = f"{msg} — {counselor_err}"

    return ImportRowResult(
        row.client_name,
        row.counselor_name,
        row.first_session,
        action,
        msg,
        case.case_number,
    )


@dataclass
class DeactivateCounselorSummary:
    counselor_name: str
    cases_cleared: int = 0
    clients: list[str] = field(default_factory=list)
    deactivated: bool = False


def deactivate_counselor_by_name(
    name: str,
    *,
    dry_run: bool = True,
) -> DeactivateCounselorSummary:
    """상담사 계정 비활성화 및 배정 내담자 매칭·예약 데이터 정리."""
    summary = DeactivateCounselorSummary(counselor_name=name.strip())
    counselor = User.objects.filter(role=UserRole.COUNSELOR, name=summary.counselor_name).first()
    if not counselor:
        return summary

    cases = list(
        Case.objects.filter(counselor=counselor, status=CaseStatus.ACTIVE).select_related("client")
    )
    clients = [case.client for case in cases if case.client_id]
    summary.cases_cleared = len(cases)
    summary.clients = [(client.name or "").strip() for client in clients if client]

    if dry_run:
        return summary

    if clients:
        clear_matching_data(client_users=clients, dry_run=False)

    counselor.status = UserStatus.INACTIVE
    counselor.is_active = False
    counselor.save(update_fields=["status", "is_active", "updated_at"])
    summary.deactivated = True
    return summary


def import_session1_matches(
    rows: list[Session1MatchRow],
    *,
    total_sessions: int = 10,
    create_missing_application: bool = True,
    with_zoom: bool = False,
    dry_run: bool = True,
    skip_clear: bool = False,
) -> ImportSummary:
    """매칭 데이터 삭제 후 상담사 배정 + 1회기 확정 예약 생성."""
    summary = ImportSummary()
    client_index = _build_name_index(UserRole.CLIENT)

    validation_errors = validate_rows_resolvable(rows)
    if validation_errors:
        summary.errors = len(validation_errors)
        for msg in validation_errors:
            summary.results.append(
                ImportRowResult("", "", None, "error", msg),
            )
        return summary

    resolved_clients: list[User] = []
    for row in rows:
        client, _ = resolve_client_by_name(row.client_name, client_index=client_index)
        if client:
            resolved_clients.append(client)

    if not skip_clear:
        summary.cleared = clear_matching_data(
            client_users=resolved_clients,
            dry_run=dry_run,
        )

    if dry_run:
        for row in rows:
            result = _import_one_row(
                row,
                client_index=client_index,
                total_sessions=total_sessions,
                create_missing_application=create_missing_application,
                with_zoom=with_zoom,
                dry_run=True,
            )
            summary.results.append(result)
            if result.action == "would_import":
                summary.session1_created += 1
        return summary

    with transaction.atomic():
        if not skip_clear:
            summary.cleared = clear_matching_data(
                client_users=resolved_clients,
                dry_run=False,
            )

        for row in rows:
            result = _import_one_row(
                row,
                client_index=client_index,
                total_sessions=total_sessions,
                create_missing_application=create_missing_application,
                with_zoom=with_zoom,
                dry_run=False,
            )
            summary.results.append(result)
            if result.action == "error":
                summary.errors += 1
                raise Session1ImportError(
                    f"{row.client_name} / {row.counselor_name}: {result.message}"
                )
            if result.action == "assigned":
                summary.assigned += 1
            elif result.action == "reassigned":
                summary.reassigned += 1
            summary.session1_created += 1
            summary.session1_confirmed += 1

    return summary
