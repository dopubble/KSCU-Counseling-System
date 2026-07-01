"""수퍼바이저 — 담당 기수 상담일지 열람."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import supervisor_required
from apps.counseling.cohort_journal_service import get_cohort_journals_for_supervision
from apps.counseling.journal_permissions import (
    supervision_cohorts_for_user,
    user_can_browse_cohort_journals,
    user_can_download_journal_pdf,
)
from apps.counseling.views import (
    _build_cohort_journal_entry,
    _get_pdf_password_from_request,
    _journal_client_summary,
    _pdf_file_response,
)
from apps.sessions_app.models import CounselingJournal
from apps.sessions_app.pdf import build_journal_pdf, journal_pdf_filename


@supervisor_required
def supervisor_dashboard(request):
    cohorts = supervision_cohorts_for_user(request.user)
    if cohorts is None:
        cohort_label = "전체 기수"
    elif cohorts:
        cohort_label = ", ".join(f"{c}기" for c in cohorts)
    else:
        cohort_label = "담당 기수 없음"
    return render(
        request,
        "supervisor/dashboard.html",
        {
            "cohort_label": cohort_label,
            "can_browse_journals": user_can_browse_cohort_journals(request.user),
        },
    )


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
