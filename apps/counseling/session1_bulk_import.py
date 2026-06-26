"""1회기 매칭·예약 일괄 주입 — 기존 매칭 삭제 후 이름 기반 재배정."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.utils import IntegrityError
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
from apps.documents.models import SessionMaterial
from apps.scheduling.forms import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.services import confirm_appointment_with_zoom
from apps.scheduling.utils import ZoomAPIError, ZoomNotConfiguredError

ROSTER_TIMEZONE = ZoneInfo("Asia/Seoul")


@dataclass
class Session1MatchRow:
    counselor_name: str
    client_name: str
    first_session: datetime
    line_no: int = 0
    counseling_method: str | None = None
    client_email: str = ""


@dataclass
class ClearSummary:
    cases_touched: int = 0
    appointments_deleted: int = 0
    schedule_requests_deleted: int = 0
    session_materials_deleted: int = 0
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
        first_session = timezone.make_aware(naive, ROSTER_TIMEZONE)
        counseling_method = _parse_counseling_method(item.get("counseling_method"))
        client_email = (item.get("client_email") or item.get("email") or "").strip()
        rows.append(
            Session1MatchRow(
                counselor_name=counselor,
                client_name=client,
                first_session=first_session,
                line_no=line_no,
                counseling_method=counseling_method,
                client_email=client_email,
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
    application_ids = list(
        Case.objects.filter(pk__in=case_ids).values_list("application_id", flat=True)
    )

    summary.appointments_deleted = apt_qs.count()
    summary.schedule_requests_deleted = sched_qs.count()
    summary.session_materials_deleted = material_qs.count()
    summary.applications_reset = len(application_ids)

    if dry_run:
        return summary

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


def _case_for_session1_fix(client: User) -> Case | None:
    """활성 사례가 없어도 배정된 사례면 1회기 일정 수정 가능."""
    case = _active_case(client)
    if case and case.counselor_id:
        return case
    return (
        Case.objects.filter(client=client, counselor__isnull=False)
        .exclude(status=CaseStatus.CLOSED)
        .select_related("application", "counselor")
        .order_by("-opened_at")
        .first()
    )


def _resolve_client_for_ops(
    *,
    client_name: str,
    client_email: str = "",
    client_index: dict[str, list[User]] | None = None,
) -> tuple[User | None, str]:
    email = (client_email or "").strip()
    if email:
        user = User.objects.filter(role=UserRole.CLIENT, email__iexact=email).first()
        if user:
            return user, ""
        return None, f"이메일로 내담자를 찾을 수 없습니다: {email}"
    return resolve_client_by_name(client_name, client_index=client_index)


def _roster_case(client: User, counselor_name: str) -> Case | None:
    """로스터 상담사와 일치하는 활성 사례 (없으면 최신 활성 사례)."""
    matched = (
        Case.objects.filter(
            client=client,
            status=CaseStatus.ACTIVE,
            counselor__name=counselor_name,
        )
        .select_related("application", "counselor")
        .order_by("-opened_at")
        .first()
    )
    if matched is not None:
        return matched
    return _active_case(client)


def _local_slot_label(dt: datetime) -> str:
    from apps.reports.appointment_calendar import _calendar_localtime

    return _calendar_localtime(dt).strftime("%Y-%m-%d %H:%M")


def _find_roster_session1_appointments(
    client: User,
    counselor_name: str,
) -> list[Appointment]:
    """로스터 상담사·내담자 기준 1회기 후보 (전 사례)."""
    return list(
        Appointment.objects.filter(
            client=client,
            counselor__name=counselor_name,
            session_number=1,
        )
        .select_related("case", "counselor")
        .order_by("-created_at")
    )


def _pick_canonical_session1(
    appointments: list[Appointment],
    *,
    expected_at: datetime,
) -> Appointment | None:
    """로스터 일시·확정 상태에 가장 가까운 1회기 예약 선택."""
    if not appointments:
        return None
    expected_label = _local_slot_label(expected_at)

    def sort_key(apt: Appointment) -> tuple[int, int, float]:
        label_match = _local_slot_label(apt.scheduled_at) == expected_label
        confirmed = apt.status == AppointmentStatus.CONFIRMED
        created = apt.created_at.timestamp() if apt.created_at else 0.0
        return (0 if label_match else 1, 0 if confirmed else 1, -created)

    return sorted(appointments, key=sort_key)[0]


def _cancel_extra_session1_duplicates(
    canonical: Appointment,
    duplicates: list[Appointment],
    *,
    dry_run: bool,
) -> int:
    """동일 내담자·상담사의 중복 1회기 예약 정리."""
    cancelled = 0
    for apt in duplicates:
        if apt.pk == canonical.pk:
            continue
        if apt.status in (AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED):
            continue
        if dry_run:
            cancelled += 1
            continue
        apt.status = AppointmentStatus.CANCELLED
        apt.cancelled_at = timezone.now()
        apt.cancel_reason = "1회기 로스터 복구 — 중복 예약 정리"
        apt.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
        cancelled += 1
    return cancelled


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
        try:
            confirm_appointment_with_zoom(appointment, notify=False)
        except (ZoomAPIError, ZoomNotConfiguredError) as exc:
            return ImportRowResult(
                row.client_name,
                row.counselor_name,
                row.first_session,
                "error",
                str(exc),
            )
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


def full_session1_reset_clear(
    *,
    roster_client_names: frozenset[str],
    dry_run: bool = True,
) -> ClearSummary:
    """모든 활성 상담사 배정·1회기 예약 흔적 + 로스터 대상 내담자 전량 삭제."""
    from django.db.models import Q

    matched_client_ids = set(
        Case.objects.filter(status=CaseStatus.ACTIVE)
        .filter(Q(counselor_id__isnull=False) | Q(appointments__isnull=False))
        .values_list("client_id", flat=True)
        .distinct()
    )
    roster_ids = set(
        User.objects.filter(role=UserRole.CLIENT, name__in=roster_client_names).values_list(
            "pk", flat=True
        )
    )
    client_ids = matched_client_ids | roster_ids
    clients = list(User.objects.filter(pk__in=client_ids))
    return clear_matching_data(client_users=clients, dry_run=dry_run)


@dataclass
class Session1VerificationIssue:
    kind: str
    detail: str


@dataclass
class Session1VerificationReport:
    ok: bool
    expected_count: int
    active_assignment_count: int
    session1_appointment_count: int
    by_counselor: dict[str, list[str]] = field(default_factory=dict)
    issues: list[Session1VerificationIssue] = field(default_factory=list)


def verify_session1_roster(rows: list[Session1MatchRow]) -> Session1VerificationReport:
    """DB 활성 배정이 골드 스탠다드 JSON과 일치하는지 전수 검증."""
    expected_by_counselor: dict[str, list[str]] = defaultdict(list)
    expected_pairs: set[tuple[str, str]] = set()
    for row in rows:
        expected_by_counselor[row.counselor_name].append(row.client_name)
        expected_pairs.add((row.counselor_name, row.client_name))

    actual_by_counselor: dict[str, list[str]] = defaultdict(list)
    active_cases = list(
        Case.objects.filter(status=CaseStatus.ACTIVE, counselor_id__isnull=False)
        .select_related("counselor", "client")
        .order_by("counselor__name", "client__name")
    )
    issues: list[Session1VerificationIssue] = []

    for case in active_cases:
        counselor_name = (case.counselor.name or "").strip()
        client_name = (case.client.name or "").strip()
        actual_by_counselor[counselor_name].append(client_name)
        if (counselor_name, client_name) not in expected_pairs:
            issues.append(
                Session1VerificationIssue(
                    "unexpected_assignment",
                    f"{counselor_name} / {client_name} (사례 {case.case_number})",
                )
            )

    for counselor_name, client_names in sorted(expected_by_counselor.items()):
        actual_names = set(actual_by_counselor.get(counselor_name, []))
        for client_name in client_names:
            if client_name not in actual_names:
                issues.append(
                    Session1VerificationIssue(
                        "missing_assignment",
                        f"{counselor_name} / {client_name}",
                    )
                )

    session1_count = Appointment.objects.filter(session_number=1).count()
    if session1_count != len(rows):
        issues.append(
            Session1VerificationIssue(
                "appointment_count",
                f"1회기 예약 {session1_count}건 (기대 {len(rows)}건)",
            )
        )

    if len(active_cases) != len(rows):
        issues.append(
            Session1VerificationIssue(
                "assignment_count",
                f"활성 배정 {len(active_cases)}건 (기대 {len(rows)}건)",
            )
        )

    client_index = _build_name_index(UserRole.CLIENT)
    for row in rows:
        client, client_err = resolve_client_by_name(row.client_name, client_index=client_index)
        if client_err:
            continue
        case = (
            Case.objects.filter(
                client=client,
                status=CaseStatus.ACTIVE,
                counselor_id__isnull=False,
            )
            .select_related("counselor")
            .first()
        )
        if not case:
            continue
        appointment = (
            Appointment.objects.filter(case=case, session_number=1)
            .order_by("-created_at")
            .first()
        )
        if not appointment:
            issues.append(
                Session1VerificationIssue(
                    "missing_session1",
                    f"{row.counselor_name} / {row.client_name}",
                )
            )
            continue
        if appointment.status != AppointmentStatus.CONFIRMED:
            issues.append(
                Session1VerificationIssue(
                    "session1_not_confirmed",
                    f"{row.client_name}: {appointment.get_status_display()} "
                    f"({timezone.localtime(appointment.scheduled_at):%Y-%m-%d %H:%M})",
                )
            )
        expected_label = _local_slot_label(row.first_session)
        current_label = _local_slot_label(appointment.scheduled_at)
        if current_label != expected_label:
            issues.append(
                Session1VerificationIssue(
                    "session1_time_mismatch",
                    f"{row.client_name}: DB {current_label} ≠ 기대 {expected_label}",
                )
            )

    sorted_actual = {name: sorted(names) for name, names in actual_by_counselor.items()}
    return Session1VerificationReport(
        ok=not issues,
        expected_count=len(rows),
        active_assignment_count=len(active_cases),
        session1_appointment_count=session1_count,
        by_counselor=sorted_actual,
        issues=issues,
    )


def format_verification_report_markdown(
    rows: list[Session1MatchRow],
    report: Session1VerificationReport,
) -> str:
    expected_by_counselor: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        expected_by_counselor[row.counselor_name].append(row.client_name)

    lines = [
        "# 1회기 매칭 전수 검증 리포트",
        "",
        f"- **검증 결과**: {'PASS' if report.ok else 'FAIL'}",
        f"- **기대 배정**: {report.expected_count}건",
        f"- **DB 활성 배정**: {report.active_assignment_count}건",
        f"- **DB 1회기 예약**: {report.session1_appointment_count}건",
        "",
        "## 상담사별 매칭 (DB 기준)",
        "",
    ]

    all_counselors = sorted(set(expected_by_counselor) | set(report.by_counselor))
    for counselor_name in all_counselors:
        db_clients = report.by_counselor.get(counselor_name, [])
        expected_clients = sorted(expected_by_counselor.get(counselor_name, []))
        db_label = ", ".join(db_clients) if db_clients else "—"
        lines.append(f"- **{counselor_name}** — 총 {len(db_clients)}명: {db_label}")
        if db_clients != expected_clients:
            lines.append(
                f"  - (기대: {', '.join(expected_clients) if expected_clients else '—'})"
            )

    if report.issues:
        lines.extend(["", "## 불일치 항목", ""])
        for issue in report.issues:
            lines.append(f"- [{issue.kind}] {issue.detail}")

    return "\n".join(lines)


@dataclass
class Session1TimeSyncResult:
    client_name: str
    counselor_name: str
    old_at: datetime | None
    new_at: datetime | None
    status: str
    detail: str = ""


def sync_session1_times_from_roster(
    rows: list[Session1MatchRow],
    *,
    dry_run: bool = True,
    skip_availability: bool = True,
    counselor_name: str | None = None,
    client_names: frozenset[str] | None = None,
) -> list[Session1TimeSyncResult]:
    """골드 스탠다드 JSON과 DB 1회기 확정 일시 불일치만 조정."""
    from apps.scheduling.services import (
        AppointmentServiceError,
        reschedule_confirmed_appointment,
    )

    client_index = _build_name_index(UserRole.CLIENT)
    results: list[Session1TimeSyncResult] = []

    for row in rows:
        if counselor_name and row.counselor_name != counselor_name:
            continue
        if client_names and row.client_name not in client_names:
            continue

        client, client_err = resolve_client_by_name(
            row.client_name,
            client_index=client_index,
        )
        if client_err or not client:
            results.append(
                Session1TimeSyncResult(
                    row.client_name,
                    row.counselor_name,
                    None,
                    row.first_session,
                    "error",
                    client_err or "내담자 없음",
                )
            )
            continue

        case = (
            Case.objects.filter(
                client=client,
                counselor__name=row.counselor_name,
                status=CaseStatus.ACTIVE,
            )
            .select_related("counselor")
            .first()
        )
        if not case:
            results.append(
                Session1TimeSyncResult(
                    row.client_name,
                    row.counselor_name,
                    None,
                    row.first_session,
                    "error",
                    "활성 배정 없음",
                )
            )
            continue

        appointment = (
            Appointment.objects.filter(case=case, session_number=1)
            .order_by("-created_at")
            .first()
        )
        if not appointment:
            results.append(
                Session1TimeSyncResult(
                    row.client_name,
                    row.counselor_name,
                    None,
                    row.first_session,
                    "error",
                    "1회기 예약 없음",
                )
            )
            continue

        if appointment.status != AppointmentStatus.CONFIRMED:
            results.append(
                Session1TimeSyncResult(
                    row.client_name,
                    row.counselor_name,
                    timezone.localtime(appointment.scheduled_at),
                    row.first_session,
                    "skipped",
                    f"상태 {appointment.get_status_display()}",
                )
            )
            continue

        old_at = timezone.localtime(appointment.scheduled_at)
        expected_label = _local_slot_label(row.first_session)
        current_label = _local_slot_label(appointment.scheduled_at)
        if current_label == expected_label:
            results.append(
                Session1TimeSyncResult(
                    row.client_name,
                    row.counselor_name,
                    old_at,
                    timezone.localtime(row.first_session),
                    "ok",
                    "일치",
                )
            )
            continue

        if dry_run:
            results.append(
                Session1TimeSyncResult(
                    row.client_name,
                    row.counselor_name,
                    old_at,
                    timezone.localtime(row.first_session),
                    "sync",
                    f"{current_label} → {expected_label}",
                )
            )
            continue

        try:
            appointment, zoom_warning = reschedule_confirmed_appointment(
                appointment,
                new_scheduled_at=row.first_session,
                skip_availability=skip_availability,
            )
            detail = f"{current_label} → {_local_slot_label(appointment.scheduled_at)}"
            if zoom_warning:
                detail += f" (Zoom: {zoom_warning})"
            results.append(
                Session1TimeSyncResult(
                    row.client_name,
                    row.counselor_name,
                    old_at,
                    timezone.localtime(appointment.scheduled_at),
                    "synced",
                    detail,
                )
            )
        except AppointmentServiceError as exc:
            results.append(
                Session1TimeSyncResult(
                    row.client_name,
                    row.counselor_name,
                    old_at,
                    timezone.localtime(row.first_session),
                    "error",
                    str(exc),
                )
            )

    return results


@dataclass
class Session1RepairResult:
    client_name: str
    counselor_name: str
    status: str
    detail: str = ""


def _ensure_session1_pending(appointment: Appointment) -> None:
    if appointment.status == AppointmentStatus.PENDING:
        return
    appointment.status = AppointmentStatus.PENDING
    appointment.confirmed_at = None
    appointment.save(update_fields=["status", "confirmed_at", "updated_at"])


def _append_session1_repair_result(
    results: list[Session1RepairResult],
    *,
    row: Session1MatchRow,
    appointment: Appointment | None,
    session1_candidates: list[Appointment],
    roster_day: date,
    dry_run: bool,
    status: str,
    detail: str,
) -> None:
    from apps.reports.appointment_calendar import appointment_in_calendar_events

    final_detail = detail
    if appointment is not None and not dry_run:
        dup_count = _cancel_extra_session1_duplicates(
            appointment,
            session1_candidates,
            dry_run=False,
        )
        appointment.refresh_from_db()
        if dup_count:
            final_detail = f"{detail} · 중복 {dup_count}건 정리"

    if (
        appointment is not None
        and appointment.status == AppointmentStatus.CONFIRMED
        and not appointment_in_calendar_events(appointment.pk, local_day=roster_day)
    ):
        results.append(
            Session1RepairResult(
                row.client_name,
                row.counselor_name,
                "calendar_missing",
                f"{roster_day:%Y-%m-%d} 캘린더 미표시 (id={appointment.pk}, {final_detail})",
            )
        )
        return

    results.append(
        Session1RepairResult(
            row.client_name,
            row.counselor_name,
            status,
            final_detail,
        )
    )


def repair_session1_confirmations_from_roster(
    rows: list[Session1MatchRow],
    *,
    dry_run: bool = True,
    skip_availability: bool = True,
    counselor_name: str | None = None,
    client_names: frozenset[str] | None = None,
) -> list[Session1RepairResult]:
    """로스터 1회기 예약을 CONFIRMED·올바른 일시로 복구 (캘린더 누락 방지)."""
    from apps.reports.appointment_calendar import appointment_in_calendar_events
    from apps.scheduling.services import (
        AppointmentServiceError,
        reschedule_confirmed_appointment,
    )

    client_index = _build_name_index(UserRole.CLIENT)
    results: list[Session1RepairResult] = []

    for row in rows:
        if counselor_name and row.counselor_name != counselor_name:
            continue
        if client_names and row.client_name not in client_names:
            continue

        client, client_err = _resolve_client_for_ops(
            client_name=row.client_name,
            client_email=row.client_email,
            client_index=client_index,
        )
        if client_err or not client:
            results.append(
                Session1RepairResult(
                    row.client_name,
                    row.counselor_name,
                    "error",
                    client_err or f"내담자를 찾을 수 없습니다: {row.client_name!r}",
                )
            )
            continue

        case = _roster_case(client, row.counselor_name)
        if not case or not case.counselor_id:
            results.append(
                Session1RepairResult(
                    row.client_name,
                    row.counselor_name,
                    "error",
                    "활성 배정 없음",
                )
            )
            continue

        if (case.counselor.name or "").strip() != row.counselor_name:
            results.append(
                Session1RepairResult(
                    row.client_name,
                    row.counselor_name,
                    "error",
                    f"활성 배정 상담사 불일치 (DB: {case.counselor.name})",
                )
            )
            continue

        expected_label = _local_slot_label(row.first_session)
        expected_day = _local_slot_label(row.first_session).split()[0]
        roster_day = datetime.strptime(expected_day, "%Y-%m-%d").date()
        is_remote = case.counseling_method == CounselingMethod.REMOTE

        session1_candidates = _find_roster_session1_appointments(client, row.counselor_name)
        appointment = _pick_canonical_session1(
            session1_candidates,
            expected_at=row.first_session,
        )

        if appointment and appointment.case_id != case.pk:
            if dry_run:
                results.append(
                    Session1RepairResult(
                        row.client_name,
                        row.counselor_name,
                        "reassign",
                        f"다른 사례 1회기 → 현재 사례 ({_local_slot_label(appointment.scheduled_at)})",
                    )
                )
                continue
            appointment.case = case
            appointment.counselor = case.counselor
            appointment.save(update_fields=["case", "counselor", "updated_at"])

        if not appointment:
            if dry_run:
                results.append(
                    Session1RepairResult(
                        row.client_name,
                        row.counselor_name,
                        "create",
                        f"1회기 예약 생성 필요 ({expected_label})",
                    )
                )
                continue
            try:
                appointment = Appointment.objects.create(
                    case=case,
                    counselor=case.counselor,
                    client=client,
                    scheduled_at=row.first_session,
                    duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
                    status=AppointmentStatus.PENDING,
                    session_number=1,
                    request_message="1회기 로스터 복구",
                )
                if is_remote:
                    confirm_appointment_with_zoom(appointment, notify=False)
                else:
                    appointment.status = AppointmentStatus.CONFIRMED
                    appointment.confirmed_at = timezone.now()
                    appointment.save(update_fields=["status", "confirmed_at", "updated_at"])
                session1_candidates = _find_roster_session1_appointments(
                    client, row.counselor_name
                )
                _append_session1_repair_result(
                    results,
                    row=row,
                    appointment=appointment,
                    session1_candidates=session1_candidates,
                    roster_day=roster_day,
                    dry_run=False,
                    status="created",
                    detail=f"1회기 생성·확정 ({expected_label})",
                )
            except (
                AppointmentServiceError,
                ZoomAPIError,
                ZoomNotConfiguredError,
                IntegrityError,
            ) as exc:
                results.append(
                    Session1RepairResult(
                        row.client_name,
                        row.counselor_name,
                        "error",
                        str(exc),
                    )
                )
            continue

        current_label = _local_slot_label(appointment.scheduled_at)
        time_mismatch = current_label != expected_label

        if appointment.status == AppointmentStatus.CONFIRMED:
            if not time_mismatch:
                _append_session1_repair_result(
                    results,
                    row=row,
                    appointment=appointment,
                    session1_candidates=session1_candidates,
                    roster_day=roster_day,
                    dry_run=dry_run,
                    status="ok",
                    detail="일치",
                )
                continue
            if dry_run:
                results.append(
                    Session1RepairResult(
                        row.client_name,
                        row.counselor_name,
                        "reschedule",
                        f"{current_label} → {expected_label}",
                    )
                )
                continue
            try:
                appointment, zoom_warning = reschedule_confirmed_appointment(
                    appointment,
                    new_scheduled_at=row.first_session,
                    skip_availability=skip_availability,
                )
                detail = f"{current_label} → {_local_slot_label(appointment.scheduled_at)}"
                if zoom_warning:
                    detail += f" (Zoom: {zoom_warning})"
                session1_candidates = _find_roster_session1_appointments(
                    client, row.counselor_name
                )
                _append_session1_repair_result(
                    results,
                    row=row,
                    appointment=appointment,
                    session1_candidates=session1_candidates,
                    roster_day=roster_day,
                    dry_run=False,
                    status="rescheduled",
                    detail=detail,
                )
            except (AppointmentServiceError, IntegrityError) as exc:
                results.append(
                    Session1RepairResult(
                        row.client_name,
                        row.counselor_name,
                        "error",
                        str(exc),
                    )
                )
            continue

        if appointment.status not in (
            AppointmentStatus.PENDING,
            AppointmentStatus.SCHEDULED,
            AppointmentStatus.CANCELLED,
            AppointmentStatus.COMPLETED,
            AppointmentStatus.NO_SHOW,
        ):
            results.append(
                Session1RepairResult(
                    row.client_name,
                    row.counselor_name,
                    "skipped",
                    f"상태 {appointment.get_status_display()}",
                )
            )
            continue

        if appointment.status in (
            AppointmentStatus.CANCELLED,
            AppointmentStatus.COMPLETED,
            AppointmentStatus.NO_SHOW,
        ):
            action_label = "reopen"
            if dry_run:
                results.append(
                    Session1RepairResult(
                        row.client_name,
                        row.counselor_name,
                        action_label,
                        f"{appointment.get_status_display()} → CONFIRMED ({expected_label})",
                    )
                )
                continue
            _ensure_session1_pending(appointment)
            appointment.scheduled_at = row.first_session
            appointment.session_number = 1
            appointment.save(
                update_fields=["status", "confirmed_at", "scheduled_at", "session_number", "updated_at"]
            )
            time_mismatch = False

        action_label = "confirm"
        if time_mismatch:
            action_label = "confirm+reschedule"
        if dry_run:
            results.append(
                Session1RepairResult(
                    row.client_name,
                    row.counselor_name,
                    action_label,
                    f"{appointment.get_status_display()} · "
                    f"{current_label if not time_mismatch else f'{current_label} → {expected_label}'}",
                )
            )
            continue

        try:
            if time_mismatch:
                appointment.scheduled_at = row.first_session
                appointment.save(update_fields=["scheduled_at", "updated_at"])

            if is_remote:
                _ensure_session1_pending(appointment)
                confirm_appointment_with_zoom(appointment, notify=False)
            else:
                appointment.status = AppointmentStatus.CONFIRMED
                appointment.confirmed_at = timezone.now()
                appointment.save(update_fields=["status", "confirmed_at", "updated_at"])

            session1_candidates = _find_roster_session1_appointments(
                client, row.counselor_name
            )
            _append_session1_repair_result(
                results,
                row=row,
                appointment=appointment,
                session1_candidates=session1_candidates,
                roster_day=roster_day,
                dry_run=False,
                status="confirmed",
                detail=f"확정 ({_local_slot_label(appointment.scheduled_at)})",
            )
        except (
            AppointmentServiceError,
            ZoomAPIError,
            ZoomNotConfiguredError,
            IntegrityError,
        ) as exc:
            results.append(
                Session1RepairResult(
                    row.client_name,
                    row.counselor_name,
                    "error",
                    str(exc),
                )
            )

    return results


def assert_session1_roster(rows: list[Session1MatchRow]) -> Session1VerificationReport:
    report = verify_session1_roster(rows)
    if not report.ok:
        details = "; ".join(f"{i.kind}: {i.detail}" for i in report.issues)
        raise Session1ImportError(f"1회기 매칭 검증 실패 — {details}")
    return report


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
    full_reset: bool = True,
    verify: bool = False,
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

    roster_names = frozenset(row.client_name for row in rows)

    if dry_run:
        if not skip_clear:
            if full_reset:
                summary.cleared = full_session1_reset_clear(
                    roster_client_names=roster_names,
                    dry_run=True,
                )
            else:
                summary.cleared = clear_matching_data(
                    client_users=resolved_clients,
                    dry_run=True,
                )
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
            if full_reset:
                summary.cleared = full_session1_reset_clear(
                    roster_client_names=roster_names,
                    dry_run=False,
                )
            else:
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

        if verify:
            assert_session1_roster(rows)

    return summary


@dataclass
class ForceSession1Result:
    client_name: str
    status: str
    detail: str


def force_client_session1_schedule(
    *,
    client_name: str,
    scheduled_at: datetime,
    client_email: str = "",
    dry_run: bool = True,
    skip_availability: bool = True,
) -> ForceSession1Result:
    """내담자 활성·배정 사례 기준 1회기 일정 강제 설정(기존 1회기 전부 정리 후 확정)."""
    client_index = _build_name_index(UserRole.CLIENT)
    client, client_err = _resolve_client_for_ops(
        client_name=client_name,
        client_email=client_email,
        client_index=client_index,
    )
    if client_err or not client:
        return ForceSession1Result(client_name, "error", client_err or "내담자 없음")

    case = _case_for_session1_fix(client)
    if not case or not case.counselor_id:
        return ForceSession1Result(client_name, "error", "배정된 사례·상담사 없음")

    if timezone.is_naive(scheduled_at):
        scheduled_at = timezone.make_aware(scheduled_at, timezone.get_current_timezone())

    expected_label = _local_slot_label(scheduled_at)
    all_session1 = list(
        Appointment.objects.filter(client=client, session_number=1)
        .select_related("counselor", "case")
        .order_by("-created_at")
    )
    active = [
        apt
        for apt in all_session1
        if apt.status
        not in (
            AppointmentStatus.CANCELLED,
            AppointmentStatus.COMPLETED,
            AppointmentStatus.NO_SHOW,
        )
    ]

    if dry_run:
        on_case = [a for a in active if a.case_id == case.pk]
        current = on_case[0] if on_case else (active[0] if active else None)
        if (
            current
            and current.case_id == case.pk
            and _local_slot_label(current.scheduled_at) == expected_label
            and current.status == AppointmentStatus.CONFIRMED
        ):
            return ForceSession1Result(client_name, "ok", f"이미 확정 ({expected_label})")
        cancel_n = len(active)
        detail = f"기존 1회기 {cancel_n}건 취소 → {expected_label} 확정 (사례 {case.case_number})"
        return ForceSession1Result(client_name, "dry_run", detail)

    on_case = [a for a in active if a.case_id == case.pk]
    current = on_case[0] if on_case else (active[0] if active else None)
    if (
        current
        and current.case_id == case.pk
        and _local_slot_label(current.scheduled_at) == expected_label
        and current.status == AppointmentStatus.CONFIRMED
    ):
        return ForceSession1Result(client_name, "ok", f"이미 확정 ({expected_label})")

    with transaction.atomic():
        cancelled = 0
        for apt in active:
            apt.status = AppointmentStatus.CANCELLED
            apt.cancelled_at = timezone.now()
            apt.cancel_reason = "1회기 일정 강제 변경 — 기존 일정 삭제"
            apt.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])
            cancelled += 1

        appointment = Appointment.objects.create(
            case=case,
            counselor=case.counselor,
            client=client,
            scheduled_at=scheduled_at,
            duration_minutes=DEFAULT_APPOINTMENT_DURATION_MINUTES,
            status=AppointmentStatus.PENDING,
            session_number=1,
            request_message="1회기 일정 강제 설정",
        )
        if case.counseling_method == CounselingMethod.REMOTE:
            confirm_appointment_with_zoom(appointment, notify=False)
        else:
            appointment.status = AppointmentStatus.CONFIRMED
            appointment.confirmed_at = timezone.now()
            appointment.save(update_fields=["status", "confirmed_at", "updated_at"])

        detail = (
            f"기존 {cancelled}건 취소 · 확정 {_local_slot_label(appointment.scheduled_at)} "
            f"(사례 {case.case_number})"
        )

    return ForceSession1Result(client_name, "ok", detail)
