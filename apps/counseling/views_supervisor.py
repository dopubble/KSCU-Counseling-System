"""수퍼바이저 — 담당 기수 상담일지·초기상담 기록지 열람."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import supervisor_required
from apps.accounts.models import User
from apps.counseling.models import CaseStatus
from apps.counseling.services import case_operation_label
from apps.counseling.cohort_journal_service import (
    get_cohort_initial_records_for_supervision,
    get_counselor_journals_for_supervision,
    get_supervision_counselors_by_cohort,
)
from apps.counseling.journal_permissions import (
    build_supervisor_dashboard_context,
    supervision_cohorts_for_user,
    user_can_browse_cohort_initial_records,
    user_can_browse_cohort_journals,
    user_can_browse_counselor_journals,
    user_can_download_initial_counseling_record_pdf,
    user_can_download_journal_pdf,
    user_can_view_initial_counseling_record,
)
from apps.counseling.privacy import mask_client_name
from apps.counseling.views import (
    _get_pdf_password_from_request,
    _journal_client_summary,
    _pdf_file_response,
    _record_pdf_context,
)
from apps.scheduling.availability import format_local_datetime
from apps.sessions_app.models import CounselingJournal, InitialCounselingRecord
from apps.sessions_app.pdf import (
    build_initial_record_pdf,
    build_journal_pdf,
    initial_record_pdf_filename,
    journal_pdf_filename,
)


@supervisor_required
def supervisor_dashboard(request):
    return render(
        request,
        "supervisor/dashboard.html",
        build_supervisor_dashboard_context(request.user),
    )


def _supervision_cohort_label(allowed_cohorts: list[int] | None) -> str:
    if allowed_cohorts is None:
        return "전체 기수"
    return ", ".join(f"{c}기" for c in allowed_cohorts)


def _build_cohort_initial_record_entry(record, *, requesting_user):
    case = record.case
    mask_private = case.counselor_id != requesting_user.pk
    summary = _journal_client_summary(case, mask_private=mask_private)
    updated = record.updated_at or record.created_at
    if updated and timezone.is_aware(updated):
        updated = timezone.localtime(updated)
    return {
        "record_id": record.pk,
        "counselor_name": record.counselor.name if record.counselor_id else "—",
        "case_number": case.case_number,
        "client_name": summary["client_name"],
        "gender": summary["gender"],
        "birth_date": summary["birth_date"],
        "occupation": summary["occupation"],
        "updated_at": updated.strftime("%m-%d %H:%M") if updated else "—",
    }


def _supervisor_journal_session_datetime(journal):
    """상담일 원본. 일지 일시가 없으면 연결된 예약 일시를 쓴다."""
    session_at = journal.session_datetime
    if session_at is None and journal.appointment_id:
        session_at = journal.appointment.scheduled_at
    return session_at


def _supervisor_journal_session_at(journal):
    return format_local_datetime(_supervisor_journal_session_datetime(journal))


def _optional_related(instance, name):
    try:
        return getattr(instance, name)
    except ObjectDoesNotExist:
        return None


def _case_record_summary(case) -> dict:
    """사례기록 표시값. 초안은 완료 기록이 아니다."""
    initial = _optional_related(case, "initial_counseling_record")
    termination = _optional_related(case, "termination_counseling_record")
    initial_completed = initial is not None and not initial.is_draft
    termination_completed = termination is not None and not termination.is_draft
    if termination_completed:
        termination_label = "작성완료"
    elif case.status == CaseStatus.CLOSED:
        termination_label = "미작성"
    else:
        termination_label = "종결 전"
    if initial_completed:
        initial_session_at = format_local_datetime(initial.session_start_datetime)
    else:
        initial_session_at = "—"
    return {
        "case_progress_label": case_operation_label(case),
        "initial_can_view": initial_completed,
        "initial_record_id": initial.pk if initial_completed else None,
        "initial_session_at": initial_session_at,
        "termination_label": termination_label,
    }


def _supervisor_counselor_journal_row(journal) -> dict:
    return {
        "journal_id": journal.pk,
        "session_number": journal.session_number,
        "session_label": f"{journal.session_number}회기",
        "session_at": _supervisor_journal_session_at(journal),
        "status_label": "작성완료",
    }


def _supervisor_counselor_case_groups(journals) -> list[dict]:
    """완료 일지를 Case 단위로 묶는다. 조회는 호출 전에 한 번만 한다."""
    grouped: dict = {}
    groups: list[dict] = []
    for journal in journals:
        if not journal.case_id:
            continue
        group = grouped.get(journal.case_id)
        if group is None:
            client = journal.case.client
            group = {
                "case_id": journal.case_id,
                "client_name": mask_client_name(client.name if client else ""),
                "journal_count": 0,
                "latest_session_at": None,
                "latest_session_label": "—",
                "journals": [],
                **_case_record_summary(journal.case),
            }
            grouped[journal.case_id] = group
            groups.append(group)
        row = _supervisor_counselor_journal_row(journal)
        group["journals"].append(row)
        group["journal_count"] += 1
        session_at = _supervisor_journal_session_datetime(journal)
        if session_at is not None and (
            group["latest_session_at"] is None or session_at > group["latest_session_at"]
        ):
            group["latest_session_at"] = session_at
            group["latest_session_label"] = row["session_at"]

    for group in groups:
        group["journals"].sort(key=lambda row: row["session_number"])

    groups.sort(
        key=lambda group: (
            group["latest_session_at"] is None,
            -(group["latest_session_at"].timestamp())
            if group["latest_session_at"] is not None
            else 0,
            str(group["case_id"]),
        )
    )
    return groups


@supervisor_required
def supervisor_cohort_journals(request):
    if not user_can_browse_cohort_journals(request.user):
        raise PermissionDenied("상담일지 열람 권한이 없습니다.")

    allowed_cohorts = supervision_cohorts_for_user(request.user)
    return render(
        request,
        "supervisor/cohort_journals.html",
        {
            "cohort_groups": get_supervision_counselors_by_cohort(
                allowed_cohorts=allowed_cohorts,
            ),
            "cohort_label": _supervision_cohort_label(allowed_cohorts),
        },
    )


@supervisor_required
def supervisor_counselor_journals(request, counselor_id):
    counselor = get_object_or_404(
        User.objects.select_related("counselor_profile"),
        pk=counselor_id,
    )
    if not user_can_browse_counselor_journals(request.user, counselor):
        raise PermissionDenied("상담일지 열람 권한이 없습니다.")

    profile = getattr(counselor, "counselor_profile", None)
    cohort = profile.cohort if profile is not None else None
    case_groups = _supervisor_counselor_case_groups(
        get_counselor_journals_for_supervision(counselor)
    )
    return render(
        request,
        "supervisor/counselor_journals.html",
        {
            "counselor": counselor,
            "cohort_label": f"{cohort}기" if cohort else "",
            "page_title": (
                f"[{cohort}기] {counselor.name} 상담일지" if cohort else f"{counselor.name} 상담일지"
            ),
            "case_groups": case_groups,
            "case_count": len(case_groups),
            "completed_session_count": sum(group["journal_count"] for group in case_groups),
            "pdf_url_name": "supervisor:journal_pdf",
        },
    )


@supervisor_required
def supervisor_cohort_initial_records(request):
    if not user_can_browse_cohort_initial_records(request.user):
        raise PermissionDenied("초기상담 기록지 열람 권한이 없습니다.")

    allowed_cohorts = supervision_cohorts_for_user(request.user)
    records = get_cohort_initial_records_for_supervision(
        allowed_cohorts=allowed_cohorts,
    )
    entries = [
        _build_cohort_initial_record_entry(record, requesting_user=request.user)
        for record in records
    ]

    return render(
        request,
        "supervisor/cohort_initial_records.html",
        {
            "entries": entries,
            "cohort_label": _supervision_cohort_label(allowed_cohorts),
            "pdf_url_name": "supervisor:initial_record_pdf",
        },
    )


@supervisor_required
def supervisor_initial_record_detail(request, record_pk):
    record = get_object_or_404(
        InitialCounselingRecord.objects.select_related(
            "case",
            "case__client",
            "case__application",
            "counselor",
            "counselor__counselor_profile",
        ),
        pk=record_pk,
    )
    if not user_can_view_initial_counseling_record(request.user, record):
        raise PermissionDenied("초기상담 기록지 열람 권한이 없습니다.")
    if record.is_draft:
        raise PermissionDenied("저장 완료된 초기상담 기록지만 열람할 수 있습니다.")

    case = record.case
    mask_private = case.counselor_id != request.user.pk
    client_summary = _journal_client_summary(case, mask_private=mask_private)
    return render(
        request,
        "supervisor/initial_record_detail.html",
        {
            "record": record,
            "case": case,
            "client_summary": client_summary,
            **_record_pdf_context(
                True,
                reverse(
                    "supervisor:initial_record_pdf",
                    kwargs={"record_pk": record.pk},
                ),
            ),
        },
    )


@supervisor_required
@require_POST
def supervisor_journal_pdf(request, journal_pk):
    journal = get_object_or_404(
        CounselingJournal.objects.select_related(
            "case",
            "case__client",
            "case__application",
            "counselor",
            "counselor__counselor_profile",
        ),
        pk=journal_pk,
    )
    fallback = (request.POST.get("next") or "").strip() or reverse(
        "supervisor:cohort_journals"
    )
    if not user_can_download_journal_pdf(request.user, journal):
        raise PermissionDenied("상담일지 PDF 다운로드 권한이 없습니다.")
    if journal.is_draft:
        messages.error(request, "저장 완료된 상담일지만 다운로드할 수 있습니다.")
        return redirect(fallback)

    pdf_password, redirect_response = _get_pdf_password_from_request(
        request, redirect_url=fallback
    )
    if redirect_response:
        return redirect_response

    mask_private = journal.case.counselor_id != request.user.pk
    client_summary = _journal_client_summary(journal.case, mask_private=mask_private)
    pdf_bytes = build_journal_pdf(
        journal,
        client_summary=client_summary,
        user_password=pdf_password,
    )
    filename = journal_pdf_filename(journal)
    ascii_name = f"journal_{journal.case.case_number}_{journal.session_number}.pdf".replace(
        "/", "-"
    )
    return _pdf_file_response(pdf_bytes, filename=filename, ascii_name=ascii_name)


@supervisor_required
@require_POST
def supervisor_initial_record_pdf(request, record_pk):
    record = get_object_or_404(
        InitialCounselingRecord.objects.select_related(
            "case",
            "case__client",
            "case__application",
            "counselor",
            "counselor__counselor_profile",
        ),
        pk=record_pk,
    )
    fallback = (request.POST.get("next") or "").strip() or reverse(
        "supervisor:cohort_initial_records"
    )
    if not user_can_download_initial_counseling_record_pdf(request.user, record):
        raise PermissionDenied("초기상담 기록지 PDF 다운로드 권한이 없습니다.")
    if record.is_draft:
        messages.error(request, "저장 완료된 초기상담 기록지만 다운로드할 수 있습니다.")
        return redirect(fallback)

    pdf_password, redirect_response = _get_pdf_password_from_request(
        request, redirect_url=fallback
    )
    if redirect_response:
        return redirect_response

    mask_private = record.case.counselor_id != request.user.pk
    client_summary = _journal_client_summary(record.case, mask_private=mask_private)
    pdf_bytes = build_initial_record_pdf(
        record,
        client_summary=client_summary,
        user_password=pdf_password,
    )
    filename = initial_record_pdf_filename(record)
    ascii_name = f"initial_record_{record.case.case_number}.pdf".replace("/", "-")
    return _pdf_file_response(pdf_bytes, filename=filename, ascii_name=ascii_name)
