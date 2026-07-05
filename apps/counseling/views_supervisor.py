"""수퍼바이저 — 담당 기수 상담일지·초기상담 기록지 열람."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import supervisor_required
from apps.counseling.cohort_journal_service import (
    get_cohort_initial_records_for_supervision,
    get_cohort_journals_for_supervision,
)
from apps.counseling.journal_permissions import (
    build_supervisor_dashboard_context,
    supervision_cohorts_for_user,
    user_can_browse_cohort_initial_records,
    user_can_browse_cohort_journals,
    user_can_download_initial_counseling_record_pdf,
    user_can_download_journal_pdf,
    user_can_view_initial_counseling_record,
)
from apps.counseling.views import (
    _build_cohort_journal_entry,
    _get_pdf_password_from_request,
    _journal_client_summary,
    _pdf_file_response,
    _record_pdf_context,
)
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


@supervisor_required
def supervisor_cohort_journals(request):
    if not user_can_browse_cohort_journals(request.user):
        raise PermissionDenied("상담일지 열람 권한이 없습니다.")

    allowed_cohorts = supervision_cohorts_for_user(request.user)
    raw_by_session = get_cohort_journals_for_supervision(
        allowed_cohorts=allowed_cohorts,
        max_session=20,
    )

    sessions = []
    for session_number in sorted(raw_by_session):
        journals = raw_by_session[session_number]
        sessions.append(
            {
                "session_number": session_number,
                "session_label": f"{session_number}회기",
                "entries": [
                    _build_cohort_journal_entry(
                        journal,
                        requesting_user=request.user,
                        mask_private=True,
                    )
                    for journal in journals
                ],
            }
        )

    if allowed_cohorts is None:
        cohort_label = "전체 기수"
    else:
        cohort_label = ", ".join(f"{c}기" for c in allowed_cohorts)

    return render(
        request,
        "supervisor/cohort_journals.html",
        {
            "sessions": sessions,
            "cohort_label": cohort_label,
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
