import logging
import os
from urllib.parse import quote

logger = logging.getLogger(__name__)

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import board_manager_required, counselor_required, role_required
from apps.accounts.models import ClientProfile, User, UserRole, UserStatus

from .emailing import (
    send_cancel_approval_notification,
    send_counselor_direct_cancel_notification,
    send_cancel_rejection_notification,
    send_cancel_request_notification,
    send_new_application_notification,
    send_schedule_change_approval_notification,
    send_schedule_change_rejection_notification,
    send_schedule_change_request_notification,
)
from .forms import (
    BoardPostForm,
    CancelRequestForm,
    CounselingApplyForm,
    CounselorMatchForm,
    SessionMaterialUploadForm,
    SessionScheduleChangeForm,
)
from .client_complaint_seed import (
    presenting_complaint_categories_for_case,
    presenting_written_reason_for_case,
)
from apps.sessions_app.forms import CounselingJournalForm
from apps.sessions_app.initial_record_forms import InitialCounselingRecordForm
from apps.sessions_app.termination_record_forms import TerminationCounselingRecordForm
from apps.sessions_app.models import (
    CounselingJournal,
    InitialCounselingRecord,
    TerminationCounselingRecord,
)
from apps.documents.forms import ConsentUploadForm
from apps.documents.services.consent_service import build_consent_rows, upsert_counselor_consent
from apps.sessions_app.pdf import (
    PDF_PASSWORD_NOTICE,
    build_initial_record_pdf,
    build_journal_pdf,
    build_termination_record_pdf,
    initial_record_pdf_filename,
    journal_pdf_filename,
    termination_record_pdf_filename,
)

from apps.scheduling.availability import (
    format_local_datetime,
    get_counselor_blocked_dates,
    is_counselor_slot_available,
    serialize_counselor_availability_rules,
)
from apps.scheduling.schedule_picker import build_schedule_picker_context
from apps.scheduling.booking_calendar import build_booking_calendar_context
from apps.scheduling.display import group_availabilities_for_display
from apps.scheduling.forms import (
    AppointmentRejectForm,
    AppointmentRequestForm,
    AppointmentScheduleForm,
)
from apps.scheduling.models import Appointment, AppointmentStatus, CounselorAvailability
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from apps.scheduling.services import (
    AppointmentServiceError,
    confirm_appointment_with_zoom,
    create_and_confirm_appointment_by_counselor,
    create_appointment_request,
    ensure_pending_session_appointment,
    reject_appointment_request,
    reschedule_confirmed_appointment,
)
from apps.scheduling.utils import (
    ZoomAPIError,
    ZoomNotConfiguredError,
    get_zoom_host_key,
    is_zoom_configured,
    is_zoom_host_key_configured,
)
from apps.documents.models import SessionMaterial
from apps.counseling.cohort_journal_service import (
    get_cohort_peer_journals_by_session,
    get_counselor_cohort,
)
from apps.counseling.privacy import mask_client_summary_fields

from .cancellation_policy import (
    AppointmentOperationError,
    cancel_triggers_session_penalty,
    client_cancel_blocked,
    client_change_blocked,
    policy_messages,
)
from .application_queries import (
    client_has_open_pending_application,
    get_client_other_active_cases,
)
from .models import ApplicationStatus, Case, CaseStatus, CounselingApplication, CounselingMethod, SessionScheduleChangeRequest
from .services import (
    annotate_application_confirmed_at,
    annotate_application_has_confirmed,
    annotate_application_sequence,
    assign_counselor,
    application_has_confirmed_appointment,
    build_apply_initial_from_application,
    build_case_session_cards,
    build_case_session_cards_cached,
    build_counselor_session_views,
    sync_orphan_session_requests,
    client_can_edit_application,
    get_case_shared_materials,
    user_can_manage_board,
    client_can_delete_application,
    deserialize_apply_initial,
    finalize_completed_journal,
    get_available_counselors,
    confirmed_appointment_blocks_client_change,
    get_confirmed_appointment_for_application,
    get_counselor_active_case_counts,
    reassign_counselor,
    request_appointment_cancel,
    approve_appointment_cancel_request,
    approve_session_schedule_change_request,
    cancel_confirmed_appointment_by_counselor,
    reject_appointment_cancel_request,
    reject_session_schedule_change_request,
    get_schedule_change_requests_for_counselor,
    withdraw_appointment_cancel_request,
    withdraw_pending_session_appointment,
    serialize_apply_initial,
    sync_case_counseling_method_from_application,
)

SESSION_APPLY_PREFILL = "counseling_apply_prefill"
SESSION_APPLY_EDIT_ID = "counseling_apply_edit_id"


def _apply_page_context(request, form, *, is_edit=None):
    """상담 신청 폼 템플릿 컨텍스트 (수정·재신청 여부 포함)."""
    edit_id = request.session.get(SESSION_APPLY_EDIT_ID)
    if is_edit is None:
        is_edit = bool(edit_id)
    return {
        "form": form,
        "is_edit": is_edit,
        "is_reapply": is_edit,
        "editing_application_id": edit_id,
    }


def _get_apply_profile_snapshot(user):
    """상담 신청에 사용할 회원 고정 정보(이름·학번·생년월일·학과)."""
    profile, _ = ClientProfile.objects.get_or_create(user=user)
    return {
        "name": user.name,
        "student_id": profile.student_id or "",
        "birth_date": profile.birth_date,
        "department": profile.department or "",
    }


def _format_birth_date_for_compare(value) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value).strip()


def _apply_identity_tampered(request, user) -> bool:
    """POST 조작으로 고정 필드 변경 시도 여부."""
    expected = _get_apply_profile_snapshot(user)
    post_checks = {
        "name": expected["name"],
        "student_id": expected["student_id"],
        "department": expected["department"],
    }
    for field_name, expected_value in post_checks.items():
        if field_name in request.POST and request.POST.get(field_name, "") != expected_value:
            return True
    if "birth_date" in request.POST:
        posted = request.POST.get("birth_date", "")
        if posted != _format_birth_date_for_compare(expected["birth_date"]):
            return True
    return False


def _enforce_apply_identity(user, data: dict) -> dict:
    """저장 직전 고정 필드를 로그인 계정 값으로 강제."""
    snapshot = _get_apply_profile_snapshot(user)
    enforced = dict(data)
    enforced.update(snapshot)
    return enforced


def _prefill_apply_form(request):
    initial = {}
    if request.user.is_authenticated:
        snapshot = _get_apply_profile_snapshot(request.user)
        initial.update(snapshot)
        initial["phone"] = request.user.phone or ""
    return initial


def _user_can_submit_application(user):
    """상담 신청 제출 가능 여부 (학생 + 관리자/슈퍼유저 테스트)"""
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.role == UserRole.ADMIN:
        return True
    if user.role == UserRole.CLIENT and user.status == UserStatus.ACTIVE:
        return True
    return False


def _save_counseling_application(user, data):
    """신청서 저장 — 고정 필드는 계정 정보, 연락처만 갱신 가능."""
    snapshot = _get_apply_profile_snapshot(user)

    update_fields = []
    if data.get("phone") and user.phone != data["phone"]:
        user.phone = data["phone"]
        update_fields.append("phone")
    if update_fields:
        update_fields.append("updated_at")
        user.save(update_fields=update_fields)

    birth_date = snapshot["birth_date"]
    preferred_schedule = {
        "student_id": snapshot["student_id"],
        "birth_date": birth_date.isoformat() if birth_date else "",
        "department": snapshot["department"],
        "preferred_date": data["preferred_date"].isoformat(),
        "preferred_time": data["preferred_time"].strftime("%H:%M"),
    }
    if user.role != UserRole.CLIENT:
        preferred_schedule["test_submission"] = True
        preferred_schedule["submitter_role"] = user.role

    return CounselingApplication.objects.create(
        client=user,
        counseling_types=data["counseling_types"],
        reason=data["reason"],
        residence_region=data["residence_region"],
        clinical_diagnosis=data["clinical_diagnosis"],
        current_medication=data["current_medication"],
        occupation=data.get("occupation", ""),
        preferred_schedule=preferred_schedule,
        counseling_method=data["counseling_method"],
        status=ApplicationStatus.WAITING_MATCH,
    )


def _update_counseling_application(user, application, data):
    """기존 상담 신청 수정 — 동일 pk 유지."""
    snapshot = _get_apply_profile_snapshot(user)

    update_fields = []
    if data.get("phone") and user.phone != data["phone"]:
        user.phone = data["phone"]
        update_fields.append("phone")
    if update_fields:
        update_fields.append("updated_at")
        user.save(update_fields=update_fields)

    birth_date = snapshot["birth_date"]
    preferred_schedule = dict(application.preferred_schedule or {})
    preferred_schedule.update(
        {
            "student_id": snapshot["student_id"],
            "birth_date": birth_date.isoformat() if birth_date else "",
            "department": snapshot["department"],
            "preferred_date": data["preferred_date"].isoformat(),
            "preferred_time": data["preferred_time"].strftime("%H:%M"),
        }
    )

    application.counseling_types = data["counseling_types"]
    application.reason = data["reason"]
    application.residence_region = data["residence_region"]
    application.clinical_diagnosis = data["clinical_diagnosis"]
    application.current_medication = data["current_medication"]
    application.occupation = data.get("occupation", "")
    application.counseling_method = data["counseling_method"]
    application.preferred_schedule = preferred_schedule
    if application.status == ApplicationStatus.CANCELLED:
        application.status = ApplicationStatus.WAITING_MATCH
    application.save(
        update_fields=[
            "counseling_types",
            "reason",
            "residence_region",
            "clinical_diagnosis",
            "current_medication",
            "occupation",
            "counseling_method",
            "preferred_schedule",
            "status",
            "updated_at",
        ]
    )
    sync_case_counseling_method_from_application(application)
    return application


@login_required
def apply(request):
    """상담 신청 페이지 (학생·내담자용, 관리자 테스트 허용)"""
    edit_id = request.session.get(SESSION_APPLY_EDIT_ID)
    is_edit = bool(edit_id)

    if request.method == "POST":
        form = CounselingApplyForm(request.POST, user=request.user)

        if not _user_can_submit_application(request.user):
            if (
                request.user.role == UserRole.CLIENT
                and request.user.status != UserStatus.ACTIVE
            ):
                messages.warning(request, "계정 승인 후 상담 신청이 가능합니다.")
                return redirect("accounts:pending")
            messages.error(
                request,
                "상담 신청은 내담자(학생) 계정 또는 관리자(테스트) 계정으로 제출할 수 있습니다.",
            )
            return render(
                request,
                "counseling/apply.html",
                _apply_page_context(request, form, is_edit=is_edit),
            )

        if form.is_valid():
            if _apply_identity_tampered(request, request.user):
                messages.warning(
                    request,
                    "이름·학번·생년월일·소속 학과는 회원가입 정보로만 저장됩니다. 다른 값은 반영되지 않았습니다.",
                )
            data = _enforce_apply_identity(request.user, form.cleaned_data)

            if is_edit and edit_id:
                application = get_object_or_404(
                    CounselingApplication.objects.select_related("case"),
                    pk=edit_id,
                    client=request.user,
                )
                if not client_can_edit_application(application):
                    messages.error(
                        request,
                        "현재 상태에서는 신청 내용을 수정할 수 없습니다.",
                    )
                    request.session.pop(SESSION_APPLY_EDIT_ID, None)
                    return redirect("client:application_list")
                _update_counseling_application(request.user, application, data)
                request.session.pop(SESSION_APPLY_EDIT_ID, None)
                messages.success(request, "상담 신청 내용이 저장되었습니다.")
            else:
                if client_has_open_pending_application(request.user):
                    messages.warning(
                        request,
                        "이미 매칭 대기 중인 상담 신청이 있습니다. "
                        "신청 목록에서 확인하거나 기존 신청을 수정해 주세요.",
                    )
                    return redirect("client:application_list")
                application = _save_counseling_application(request.user, data)
                messages.success(
                    request,
                    "상담 신청이 접수되었습니다. 담당자가 확인 후 연락드립니다.",
                )
                try:
                    notified = send_new_application_notification(application)
                except Exception:
                    logger.exception(
                        "상담 신청 알림 메일 발송 중 오류 (application_id=%s)",
                        application.pk,
                    )
                    notified = False
                if not notified:
                    messages.warning(
                        request,
                        "운영 알림 메일 발송에 실패했습니다. 이메일 설정을 확인해 주세요.",
                    )

            if request.user.role == UserRole.CLIENT:
                return redirect("client:application_list")
            return redirect("admin_panel:dashboard")

        messages.error(
            request,
            "입력 내용을 확인해 주세요. 빨간색으로 표시된 항목을 수정한 뒤 "
            + ("다시 저장해 주세요." if is_edit else "다시 신청해 주세요."),
        )
    else:
        initial = _prefill_apply_form(request)
        session_prefill = request.session.pop(SESSION_APPLY_PREFILL, None)
        if session_prefill:
            for key, value in deserialize_apply_initial(session_prefill).items():
                if key not in CounselingApplyForm.IDENTITY_FIELD_NAMES:
                    initial[key] = value
        form = CounselingApplyForm(initial=initial, user=request.user)

    return render(
        request,
        "counseling/apply.html",
        _apply_page_context(request, form, is_edit=is_edit),
    )


def _get_client_profile(user):
    try:
        return user.client_profile
    except ClientProfile.DoesNotExist:
        return None


@role_required(UserRole.ADMIN)
def application_detail(request, pk):
    """상담 신청 상세 및 상담사 매칭 (관리자)"""
    application = get_object_or_404(
        CounselingApplication.objects.select_related("client"),
        pk=pk,
    )
    client_profile = _get_client_profile(application.client)

    try:
        existing_case = application.case
    except Case.DoesNotExist:
        existing_case = None

    counselors = get_available_counselors()
    active_case_counts = get_counselor_active_case_counts()
    can_assign_new = existing_case is None and application.status in (
        ApplicationStatus.RECEIVED,
        ApplicationStatus.WAITING_MATCH,
    )
    other_active_cases = list(get_client_other_active_cases(application))
    has_other_active_case = bool(other_active_cases)
    can_change_counselor = existing_case is not None
    show_match_form = can_assign_new or can_change_counselor

    if request.method == "POST":
        if not show_match_form:
            messages.warning(request, "현재 상태에서는 상담사를 배정·변경할 수 없습니다.")
            return redirect("counseling:application_detail", pk=pk)

        match_form = CounselorMatchForm(
            request.POST,
            counselor_profiles=counselors,
            active_case_counts=active_case_counts,
            require_total_sessions=can_assign_new,
        )
        if match_form.is_valid():
            counselor = get_object_or_404(
                User,
                pk=match_form.cleaned_data["counselor"],
                role=UserRole.COUNSELOR,
            )
            try:
                if existing_case:
                    case = reassign_counselor(existing_case, counselor)
                    messages.success(
                        request,
                        f"{application.client.name} 님의 담당 상담사를 "
                        f"{counselor.name} 상담사로 변경했습니다. (사례번호: {case.case_number})",
                    )
                else:
                    total_sessions = match_form.cleaned_data.get("total_sessions") or 10
                    case = assign_counselor(
                        application,
                        counselor,
                        total_sessions=total_sessions,
                    )
                    messages.success(
                        request,
                        f"{application.client.name} 님에게 {counselor.name} 상담사를 배정했습니다. "
                        f"(사례번호: {case.case_number}, 총 {case.total_sessions}회)",
                    )
            except ValueError as exc:
                logger.warning(
                    "Counselor assignment validation failed (application=%s): %s",
                    pk,
                    exc,
                )
                messages.error(request, str(exc))
                return redirect("counseling:application_detail", pk=pk)
            except IntegrityError:
                logger.exception(
                    "Counselor assignment integrity error (application=%s, counselor=%s)",
                    pk,
                    counselor.pk,
                )
                messages.error(
                    request,
                    "사례 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요. "
                    "같은 문제가 반복되면 운영자에게 문의해 주세요.",
                )
                return redirect("counseling:application_detail", pk=pk)
            return redirect("admin_panel:matching_list")
        messages.error(request, "상담사 배정에 실패했습니다. 입력 내용을 확인해 주세요.")
    else:
        initial = {}
        if existing_case and existing_case.counselor_id:
            initial["counselor"] = str(existing_case.counselor_id)
        match_form = CounselorMatchForm(
            counselor_profiles=counselors,
            active_case_counts=active_case_counts,
            require_total_sessions=can_assign_new,
            initial=initial,
        )

    schedule = application.preferred_schedule or {}
    student_id = ""
    if client_profile and client_profile.student_id:
        student_id = client_profile.student_id
    elif schedule.get("student_id"):
        student_id = schedule["student_id"]

    birth_date = ""
    if client_profile and client_profile.birth_date:
        birth_date = client_profile.birth_date.isoformat()
    elif schedule.get("birth_date"):
        birth_date = schedule["birth_date"]

    department = ""
    if client_profile and client_profile.department:
        department = client_profile.department
    elif schedule.get("department"):
        department = schedule["department"]

    return render(
        request,
        "counseling/application_detail.html",
        {
            "application": application,
            "client_profile": client_profile,
            "student_id": student_id,
            "birth_date": birth_date,
            "department": department,
            "schedule": schedule,
            "existing_case": existing_case,
            "match_form": match_form,
            "can_assign_new": can_assign_new,
            "can_change_counselor": can_change_counselor,
            "show_match_form": show_match_form,
            "has_other_active_case": has_other_active_case,
            "other_active_cases": other_active_cases,
            "counselors_count": counselors.count(),
            "active_case_counts": active_case_counts,
        },
    )


@role_required(UserRole.CLIENT)
def client_dashboard(request):
    application_qs = CounselingApplication.objects.filter(client=request.user)
    applications = list(
        annotate_application_sequence(
            application_qs.select_related("case", "case__counselor")
        ).order_by("-created_at")[:5]
    )
    for app in applications:
        app.can_delete_application = client_can_delete_application(app)
    active_cases = list(
        Case.objects.filter(client=request.user, status=CaseStatus.ACTIVE)
        .select_related("counselor", "application")
        .order_by("-opened_at")
    )
    primary_case = active_cases[0] if active_cases else None
    session_summary = None
    if primary_case:
        session_summary = {
            "remaining": primary_case.remaining_sessions,
            "total": primary_case.total_sessions,
            "label": primary_case.sessions_label,
        }
    return render(
        request,
        "client/dashboard.html",
        {
            "applications": applications,
            "application_count": application_qs.count(),
            "active_cases": active_cases,
            "session_summary": session_summary,
        },
    )


@role_required(UserRole.CLIENT)
def application_list(request):
    applications = list(
        annotate_application_has_confirmed(
            annotate_application_confirmed_at(
                annotate_application_sequence(
                    CounselingApplication.objects.filter(client=request.user).select_related(
                        "case", "case__counselor"
                    )
                )
            )
        ).order_by("-created_at")
    )
    for app in applications:
        app.can_edit_application_policy = client_can_edit_application(app)
        app.can_delete_application = client_can_delete_application(app)
    return render(
        request,
        "client/application_list.html",
        {"applications": applications},
    )


@role_required(UserRole.CLIENT)
def client_application_detail(request, pk):
    """내담자 상담 신청 상세 — 매칭된 경우 사례 상세로 이동"""
    application = get_object_or_404(
        annotate_application_sequence(
            CounselingApplication.objects.filter(client=request.user)
        ).select_related("case", "case__counselor"),
        pk=pk,
    )
    try:
        case = application.case
    except Case.DoesNotExist:
        case = None
    if case is not None:
        return redirect("client:case_detail", pk=case.pk)

    schedule = application.preferred_schedule or {}
    return render(
        request,
        "client/application_detail.html",
        {
            "application": application,
            "schedule": schedule,
            "can_edit_application": client_can_edit_application(application),
        },
    )


@role_required(UserRole.CLIENT)
@require_POST
def delete_application(request, pk):
    """매칭·예약 확정 전 상담 신청 삭제 (본인·접수/매칭대기만)."""
    application = get_object_or_404(
        CounselingApplication.objects.select_related("case", "case__counselor"),
        pk=pk,
        client=request.user,
    )
    if application.client_id != request.user.pk:
        raise PermissionDenied("본인의 상담 신청만 삭제할 수 있습니다.")

    if not client_can_delete_application(application):
        messages.error(
            request,
            "상담사 배정 또는 예약 확정이 완료된 신청은 삭제할 수 없습니다.",
        )
        next_url = request.POST.get("next") or reverse("client:dashboard")
        return redirect(next_url)

    application.delete()
    messages.success(request, "상담 신청이 삭제되었습니다.")
    next_url = request.POST.get("next") or reverse("client:dashboard")
    return redirect(next_url)


@role_required(UserRole.CLIENT)
def edit_application(request, pk):
    """상담 신청 수정 폼으로 이동 (기존 신청 pk 유지)."""
    application = get_object_or_404(
        CounselingApplication.objects.select_related("case"),
        pk=pk,
        client=request.user,
    )

    if not client_can_edit_application(application):
        confirmed = get_confirmed_appointment_for_application(application)
        if confirmed and client_change_blocked(confirmed):
            messages.error(request, policy_messages(confirmed)["change"])
        else:
            messages.error(
                request,
                "이미 확정된 상담은 수정할 수 없습니다. 변경이 필요하면 상담사에게 문의해 주세요.",
            )
        return redirect("client:application_list")

    if request.method != "POST":
        return redirect("client:application_list")

    initial = build_apply_initial_from_application(application, user=request.user)
    request.session[SESSION_APPLY_EDIT_ID] = str(application.pk)
    request.session[SESSION_APPLY_PREFILL] = serialize_apply_initial(initial)
    messages.info(
        request,
        "신청 내용을 수정한 뒤 저장하기를 눌러 주세요.",
    )
    return redirect("counseling:apply")


@role_required(UserRole.CLIENT)
def request_cancel_application(request, pk):
    """확정 예약에 대한 내담자 취소 요청."""
    application = get_object_or_404(
        CounselingApplication.objects.select_related("case"),
        pk=pk,
        client=request.user,
    )

    redirect_to = request.POST.get("next") or reverse("client:application_list")

    if request.method != "POST":
        return redirect(redirect_to)

    appointment = get_confirmed_appointment_for_application(application)
    if not appointment:
        messages.error(request, "확정된 예약이 없어 취소 요청을 할 수 없습니다.")
        return redirect(redirect_to)

    form = CancelRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, "취소 사유를 확인해 주세요.")
        for err in form.errors.get("cancel_reason", []):
            messages.error(request, err)
        return redirect(redirect_to)

    try:
        appointment = request_appointment_cancel(
            appointment, cancel_reason=form.cleaned_data["cancel_reason"]
        )
    except AppointmentOperationError as exc:
        messages.error(request, exc.message)
        return redirect(redirect_to)
    except ValueError:
        messages.warning(request, "이미 취소 요청이 접수되었거나 처리된 예약입니다.")
        return redirect(redirect_to)

    for note in getattr(appointment, "_cancel_rule_messages", []):
        messages.warning(request, note)

    appointment = Appointment.objects.select_related(
        "case",
        "case__application",
        "counselor",
        "client",
    ).get(pk=appointment.pk)

    if not send_cancel_request_notification(
        appointment, cancel_reason=form.cleaned_data["cancel_reason"]
    ):
        messages.warning(
            request,
            "취소 요청은 접수되었으나 알림 메일 발송에 실패했습니다.",
        )
    else:
        messages.success(
            request,
            "상담 취소 요청이 접수되었습니다. 담당자 확인 후 안내드리겠습니다.",
        )
    return redirect(redirect_to)


def _get_client_case(request, pk, *, active_only: bool = False):
    """내담자 본인 사례. active_only=True면 개입중(ACTIVE) 사례만."""
    filters = {"pk": pk, "client": request.user}
    if active_only:
        filters["status"] = CaseStatus.ACTIVE
    return get_object_or_404(
        Case.objects.select_related("counselor", "client", "application"),
        **filters,
    )


def _get_client_case_appointment(case, appointment_pk):
    """사례에 속한 예약(회기) — 내담자 접근용"""
    return get_object_or_404(
        Appointment.objects.select_related("counselor", "zoom_meeting"),
        pk=appointment_pk,
        case=case,
    )


def _get_session_card(case, session_number):
    return next(
        (
            c
            for c in build_case_session_cards_cached(case)
            if c.session_number == session_number
        ),
        None,
    )


def _get_session_material_for_case(case, session_number, material_pk):
    """회기·사례에 속한 첨부 자료 조회."""
    material = get_object_or_404(SessionMaterial, pk=material_pk)
    if material.case_id == case.pk and material.session_number == session_number:
        return material
    card = _get_session_card(case, session_number)
    if (
        card
        and card.appointment
        and material.appointment_id == card.appointment.pk
    ):
        return material
    raise Http404("No SessionMaterial matches the given query.")


def _session_material_file_response(material):
    if not material.file:
        raise Http404("File not found")
    return FileResponse(
        material.file.open("rb"),
        as_attachment=True,
        filename=material.get_filename(),
    )


def _delete_session_material(request, case, session_number, material_pk, *, redirect_to):
    material = _get_session_material_for_case(case, session_number, material_pk)
    if material.uploaded_by_id != request.user.id:
        messages.error(request, "본인이 업로드한 파일만 삭제할 수 있습니다.")
        return redirect(redirect_to)
    if material.file:
        material.file.delete(save=False)
    material.delete()
    messages.success(request, "파일이 삭제되었습니다.")
    return redirect(redirect_to)


@role_required(UserRole.CLIENT)
def client_case_detail(request, pk):
    """내담자 상담(사례) 상세 — 예약 신청"""
    case = _get_client_case(request, pk)
    application = case.application
    session_cards = build_case_session_cards_cached(case)
    sync_orphan_session_requests(case, session_cards)
    sessions = session_cards
    appointments_qs = Appointment.objects.filter(case=case).select_related("counselor")
    pending_appointments = (
        appointments_qs.filter(status=AppointmentStatus.PENDING)
        .order_by("session_number", "scheduled_at")
    )
    change_blocked = confirmed_appointment_blocks_client_change(application)
    can_request = (
        case.status == CaseStatus.ACTIVE
        and case.counselor_id is not None
        and not pending_appointments.exists()
        and not change_blocked
    )
    show_legacy_appointment_form = can_request and not sessions
    can_edit_application = client_can_edit_application(application)
    has_confirmed_appointment = application_has_confirmed_appointment(application)
    confirmed_appointment = get_confirmed_appointment_for_application(application)
    has_cancel_pending = appointments_qs.filter(
        status=AppointmentStatus.CANCEL_PENDING
    ).exists()
    cancel_policy = policy_messages(confirmed_appointment) if confirmed_appointment else {}
    can_request_cancel = (
        bool(confirmed_appointment)
        and not has_cancel_pending
        and not client_cancel_blocked(confirmed_appointment)
        and case.status == CaseStatus.ACTIVE
    )
    cancel_within_penalty = bool(
        confirmed_appointment and cancel_triggers_session_penalty(confirmed_appointment)
    )
    has_session_level_cancel = any(
        card.show_confirmed_session_actions for card in session_cards
    )
    show_header_cancel = can_request_cancel and not has_session_level_cancel
    shared_materials = get_case_shared_materials(case)
    picker_context = build_schedule_picker_context(case)
    counselor = case.counselor
    if counselor:
        counselor_availability_groups = group_availabilities_for_display(
            CounselorAvailability.objects.filter(counselor=counselor).order_by(
                "specific_date", "day_of_week", "start_time"
            )
        )
    else:
        counselor_availability_groups = []

    return render(
        request,
        "client/case_detail.html",
        {
            "case": case,
            "counselor": case.counselor,
            "session_cards": session_cards,
            "sessions": sessions,
            "pending_appointments": pending_appointments,
            "can_request": can_request,
            "show_legacy_appointment_form": show_legacy_appointment_form,
            "request_form": AppointmentRequestForm(),
            "application": application,
            "can_edit_application": can_edit_application,
            "has_confirmed_appointment": has_confirmed_appointment,
            "can_request_cancel": can_request_cancel,
            "show_header_cancel": show_header_cancel,
            "has_cancel_pending": has_cancel_pending,
            "cancel_request_form": CancelRequestForm(),
            "cancel_policy": cancel_policy,
            "cancel_within_penalty": cancel_within_penalty,
            "change_blocked": confirmed_appointment_blocks_client_change(application),
            "shared_materials": shared_materials,
            "counselor_availability_groups": counselor_availability_groups,
            **picker_context,
        },
    )


def _get_case_shared_material(case, material_pk):
    return get_object_or_404(
        SessionMaterial,
        pk=material_pk,
        case=case,
        is_shared=True,
    )


def _get_board_manage_case(request, pk):
    """게시판 관리용 사례 — 담당 상담사 또는 관리자."""
    qs = Case.objects.select_related("client", "application", "counselor")
    if request.user.is_superuser or request.user.role == UserRole.ADMIN:
        return get_object_or_404(qs, pk=pk)
    return get_object_or_404(qs, pk=pk, counselor=request.user)


@role_required(UserRole.CLIENT)
def client_shared_material_file(request, case_pk, material_pk):
    """사례 공유 자료실 파일 다운로드"""
    case = _get_client_case(request, case_pk)
    material = _get_case_shared_material(case, material_pk)
    return _session_material_file_response(material)


@role_required(UserRole.CLIENT)
def client_session_materials(request, case_pk, appointment_pk):
    """회기별 자료함"""
    case = _get_client_case(request, case_pk)
    appointment = _get_client_case_appointment(case, appointment_pk)
    card = next(
        (c for c in build_case_session_cards(case) if c.appointment.pk == appointment.pk),
        None,
    )
    session_number = card.session_number if card else None
    materials = SessionMaterial.objects.filter(appointment=appointment).order_by(
        "-created_at"
    )
    return render(
        request,
        "client/session_materials.html",
        {
            "case": case,
            "appointment": appointment,
            "session_number": session_number,
            "materials": materials,
        },
    )


@role_required(UserRole.CLIENT)
def client_session_material_file(request, case_pk, session_number, material_pk):
    """회기별 첨부 자료 다운로드 (회차 기준)"""
    case = _get_client_case(request, case_pk)
    material = _get_session_material_for_case(case, session_number, material_pk)
    return _session_material_file_response(material)


@role_required(UserRole.CLIENT)
def client_session_material_download(request, case_pk, appointment_pk, material_pk):
    """회기별 첨부 자료 다운로드 (예약 pk — 하위 호환)"""
    case = _get_client_case(request, case_pk)
    appointment = _get_client_case_appointment(case, appointment_pk)
    material = get_object_or_404(
        SessionMaterial,
        pk=material_pk,
        appointment=appointment,
    )
    return _session_material_file_response(material)


@role_required(UserRole.CLIENT)
@require_POST
def client_session_material_delete(request, case_pk, session_number, material_pk):
    """회기별 첨부 자료 삭제 (본인 업로드만)"""
    case = _get_client_case(request, case_pk)
    return _delete_session_material(
        request,
        case,
        session_number,
        material_pk,
        redirect_to=reverse("client:case_detail", kwargs={"pk": case.pk}),
    )


@role_required(UserRole.CLIENT)
def client_session_booking_calendar(request, case_pk, session_number):
    """회기별 예약·일정 변경 — 전체 화면 예약 캘린더."""
    case = _get_client_case(request, case_pk)
    card = _get_session_card(case, session_number)
    if not card or not card.show_schedule_change:
        messages.error(request, "이 회기에는 일정을 예약하거나 변경할 수 없습니다.")
        return redirect("client:case_detail", pk=case.pk)

    if (
        card.appointment
        and card.appointment.status != AppointmentStatus.PENDING
        and client_change_blocked(card.appointment)
    ):
        messages.error(
            request,
            "상담 예정일 24시간 이내에는 예약 변경이 불가합니다.",
        )
        return redirect("client:case_detail", pk=case.pk)

    if request.method == "POST":
        form = SessionScheduleChangeForm(request.POST)
        if not form.is_valid():
            messages.error(request, "요청 내용을 확인해 주세요.")
            return redirect(
                "client:session_booking_calendar",
                case_pk=case.pk,
                session_number=session_number,
            )

        preferred_datetime = form.cleaned_data.get("preferred_datetime")
        if not preferred_datetime:
            messages.error(request, "희망 일시를 선택해 주세요.")
            return redirect(
                "client:session_booking_calendar",
                case_pk=case.pk,
                session_number=session_number,
            )

        if case.counselor_id:
            available, availability_message = is_counselor_slot_available(
                case.counselor_id,
                preferred_datetime,
                require_full_duration=False,
            )
            if not available:
                messages.error(request, availability_message)
                return redirect(
                    "client:session_booking_calendar",
                    case_pk=case.pk,
                    session_number=session_number,
                )

        message = (form.cleaned_data.get("message") or "").strip()

        if card.is_confirmed and card.appointment:
            SessionScheduleChangeRequest.objects.filter(
                case=case,
                session_number=session_number,
            ).delete()
            schedule_request = SessionScheduleChangeRequest.objects.create(
                case=case,
                session_number=session_number,
                appointment=card.appointment,
                client=request.user,
                preferred_datetime=preferred_datetime,
                message=message,
            )
            send_schedule_change_request_notification(schedule_request)
            messages.success(
                request,
                f"{session_number}회기 일정 변경 요청이 접수되었습니다. 담당 상담사가 확인 후 안내해 드립니다.",
            )
            return redirect("client:case_detail", pk=case.pk)

        ensure_pending_session_appointment(
            case=case,
            client=request.user,
            session_number=session_number,
            scheduled_at=preferred_datetime,
            request_message=message,
        )
        had_pending = bool(
            card.appointment and card.appointment.status == AppointmentStatus.PENDING
        )
        SessionScheduleChangeRequest.objects.filter(
            case=case,
            session_number=session_number,
        ).delete()
        if had_pending:
            messages.success(
                request,
                f"{session_number}회기 예약 요청 일시가 변경되었습니다. 담당 상담사가 확인 후 확정해 드립니다.",
            )
        else:
            messages.success(
                request,
                f"{session_number}회기 상담 일정 예약이 접수되었습니다. 담당 상담사가 확인 후 확정해 드립니다.",
            )
        return redirect("client:case_detail", pk=case.pk)

    if card.is_confirmed:
        page_title = f"{session_number}회기 일정 변경"
        submit_label = "변경 요청 제출"
    elif card.appointment and card.appointment.status == AppointmentStatus.PENDING:
        page_title = f"{session_number}회기 일정 수정"
        submit_label = "예약 일시 변경"
    else:
        page_title = f"{session_number}회기 상담일정 예약"
        submit_label = "예약 요청 제출"

    return render(
        request,
        "client/session_booking_calendar.html",
        {
            "case": case,
            "session_number": session_number,
            "card": card,
            "page_title": page_title,
            "submit_label": submit_label,
            "is_confirmed_change": card.is_confirmed,
            **build_booking_calendar_context(
                case,
                appointment=card.appointment,
                session_number=session_number,
                role="client",
            ),
        },
    )


@role_required(UserRole.CLIENT)
@require_POST
def client_session_schedule_change(request, case_pk, session_number):
    """회기별 일정 변경 요청"""
    case = _get_client_case(request, case_pk)
    card = _get_session_card(case, session_number)
    if not card or not card.show_schedule_change:
        messages.error(request, "이 회기에는 일정 변경을 요청할 수 없습니다.")
        return redirect("client:case_detail", pk=case.pk)

    if (
        card.appointment
        and card.appointment.status != AppointmentStatus.PENDING
        and client_change_blocked(card.appointment)
    ):
        messages.error(
            request,
            "상담 예정일 24시간 이내에는 예약 변경이 불가합니다.",
        )
        return redirect("client:case_detail", pk=case.pk)

    form = SessionScheduleChangeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "요청 내용을 확인해 주세요.")
        return redirect("client:case_detail", pk=case.pk)

    preferred_datetime = form.cleaned_data.get("preferred_datetime")

    if not preferred_datetime:
        messages.error(request, "희망 일시를 입력해 주세요.")
        return redirect("client:case_detail", pk=case.pk)

    if case.counselor_id:
        available, availability_message = is_counselor_slot_available(
            case.counselor_id,
            preferred_datetime,
            require_full_duration=False,
        )
        if not available:
            messages.error(request, availability_message)
            return redirect("client:case_detail", pk=case.pk)

    message = (form.cleaned_data.get("message") or "").strip()

    if card.is_confirmed and card.appointment:
        SessionScheduleChangeRequest.objects.filter(
            case=case,
            session_number=session_number,
        ).delete()
        schedule_request = SessionScheduleChangeRequest.objects.create(
            case=case,
            session_number=session_number,
            appointment=card.appointment,
            client=request.user,
            preferred_datetime=preferred_datetime,
            message=message,
        )
        send_schedule_change_request_notification(schedule_request)
        messages.success(
            request,
            f"{session_number}회기 일정 변경 요청이 접수되었습니다. 담당 상담사가 확인 후 안내해 드립니다.",
        )
        return redirect("client:case_detail", pk=case.pk)

    appointment = ensure_pending_session_appointment(
        case=case,
        client=request.user,
        session_number=session_number,
        scheduled_at=preferred_datetime,
        request_message=message,
    )
    had_pending = bool(
        card.appointment and card.appointment.status == AppointmentStatus.PENDING
    )
    SessionScheduleChangeRequest.objects.filter(
        case=case,
        session_number=session_number,
    ).delete()
    if had_pending:
        messages.success(
            request,
            f"{session_number}회기 예약 요청 일시가 변경되었습니다. 담당 상담사가 확인 후 확정해 드립니다.",
        )
    else:
        messages.success(
            request,
            f"{session_number}회기 상담 일정 예약이 접수되었습니다. 담당 상담사가 확인 후 확정해 드립니다.",
        )
    return redirect("client:case_detail", pk=case.pk)


@role_required(UserRole.CLIENT)
@require_POST
def client_session_pending_withdraw(request, case_pk, appointment_pk):
    """내담자 PENDING 예약 요청 철회."""
    case = _get_client_case(request, case_pk)
    appointment = get_object_or_404(
        Appointment.objects.select_related("case"),
        pk=appointment_pk,
        case=case,
        client=request.user,
    )
    redirect_to = request.POST.get("next") or reverse(
        "client:case_detail", kwargs={"pk": case.pk}
    )

    if appointment.status != AppointmentStatus.PENDING:
        messages.error(request, "대기 중인 예약 요청만 철회할 수 있습니다.")
        return redirect(redirect_to)

    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    try:
        withdraw_pending_session_appointment(appointment)
    except ValueError:
        messages.warning(request, "이미 처리되었거나 철회할 수 없는 예약입니다.")
        return redirect(redirect_to)

    messages.success(
        request,
        f"{session_label} 예약 요청이 철회되었습니다. 다시 일정을 신청할 수 있습니다.",
    )
    return redirect(redirect_to)


@role_required(UserRole.CLIENT)
@require_POST
def client_session_appointment_cancel(request, case_pk, appointment_pk):
    """확정 회기별 내담자 예약 취소 요청."""
    case = _get_client_case(request, case_pk)
    appointment = get_object_or_404(
        Appointment.objects.select_related("case", "case__application"),
        pk=appointment_pk,
        case=case,
        client=request.user,
    )
    redirect_to = request.POST.get("next") or reverse(
        "client:case_detail", kwargs={"pk": case.pk}
    )

    if appointment.status != AppointmentStatus.CONFIRMED:
        messages.error(request, "확정된 예약만 취소 요청할 수 있습니다.")
        return redirect(redirect_to)

    if client_change_blocked(appointment):
        messages.error(
            request,
            "상담 24시간 이내에는 변경/취소가 불가합니다.",
        )
        return redirect(redirect_to)

    form = CancelRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, "취소 사유를 확인해 주세요.")
        for err in form.errors.get("cancel_reason", []):
            messages.error(request, err)
        return redirect(redirect_to)

    try:
        appointment = request_appointment_cancel(
            appointment, cancel_reason=form.cleaned_data["cancel_reason"]
        )
    except AppointmentOperationError as exc:
        messages.error(request, exc.message)
        return redirect(redirect_to)
    except ValueError:
        messages.warning(request, "이미 취소 요청이 접수되었거나 처리된 예약입니다.")
        return redirect(redirect_to)

    for note in getattr(appointment, "_cancel_rule_messages", []):
        messages.warning(request, note)

    appointment = Appointment.objects.select_related(
        "case",
        "case__application",
        "counselor",
        "client",
    ).get(pk=appointment.pk)

    if not send_cancel_request_notification(
        appointment, cancel_reason=form.cleaned_data["cancel_reason"]
    ):
        messages.warning(
            request,
            "취소 요청은 접수되었으나 알림 메일 발송에 실패했습니다.",
        )
    else:
        messages.success(
            request,
            "예약 취소 요청이 접수되었습니다. 담당자 확인 후 안내드리겠습니다.",
        )
    return redirect(redirect_to)


@role_required(UserRole.CLIENT)
@require_POST
def client_session_cancel_withdraw(request, case_pk, appointment_pk):
    """내담자 취소 요청 철회 — 예약 확정 상태로 복원."""
    case = _get_client_case(request, case_pk)
    appointment = get_object_or_404(
        Appointment.objects.select_related("case", "case__application"),
        pk=appointment_pk,
        case=case,
        client=request.user,
    )
    redirect_to = request.POST.get("next") or reverse(
        "client:case_detail", kwargs={"pk": case.pk}
    )

    try:
        withdraw_appointment_cancel_request(appointment)
    except ValueError:
        messages.error(request, "철회할 취소 요청이 없습니다.")
        return redirect(redirect_to)

    messages.success(
        request,
        "취소 요청이 철회되었습니다. 기존 예약이 유지됩니다.",
    )
    return redirect(redirect_to)


def _get_counselor_case_appointment(request, case_pk, appointment_pk):
    """담당 상담사 사례·예약."""
    case = get_object_or_404(
        Case.objects.select_related("client", "application", "counselor"),
        pk=case_pk,
        counselor=request.user,
    )
    appointment = get_object_or_404(
        Appointment.objects.select_related("client", "case"),
        pk=appointment_pk,
        case=case,
    )
    return case, appointment


def _get_counselor_schedule_change_request(request, case_pk, request_pk):
    """담당 상담사 사례·일정 변경 요청."""
    case = get_object_or_404(
        Case.objects.select_related("client", "application", "counselor"),
        pk=case_pk,
        counselor=request.user,
    )
    schedule_request = get_object_or_404(
        SessionScheduleChangeRequest.objects.select_related(
            "appointment__zoom_meeting", "client", "case"
        ),
        pk=request_pk,
        case=case,
    )
    return case, schedule_request


def _is_ajax_request(request) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _ajax_error_response(request, message: str, *, status: int = 400):
    if _is_ajax_request(request):
        return JsonResponse({"error": message}, status=status)
    messages.error(request, message)
    return None


def _counselor_session_card_response(request, case, session_number: int):
    """AJAX: 회기 카드 HTML 조각 / 일반: 사례 상세 리다이렉트."""
    if _is_ajax_request(request):
        case.refresh_from_db()
        session = next(
            (
                view
                for view in build_counselor_session_views(case)
                if view.session_number == session_number
            ),
            None,
        )
        if session is None:
            return JsonResponse({"error": "회기 정보를 찾을 수 없습니다."}, status=404)
        html = render_to_string(
            "counselor/partials/session_card.html",
            {"session": session, "case": case},
            request=request,
        )
        return HttpResponse(html)
    return redirect("counselor:case_detail", pk=case.pk)


@role_required(UserRole.CLIENT)
@require_POST
def client_session_material_upload(request, case_pk, session_number):
    """회기별 자료 첨부"""
    case = _get_client_case(request, case_pk)
    card = _get_session_card(case, session_number)
    if not card or not card.show_session_actions:
        messages.error(request, "이 회기에는 자료를 첨부할 수 없습니다.")
        return redirect("client:case_detail", pk=case.pk)

    form = SessionMaterialUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        file_errors = form.errors.get("file")
        if file_errors:
            messages.error(request, file_errors[0])
        else:
            messages.error(request, "파일을 확인해 주세요.")
        return redirect("client:case_detail", pk=case.pk)

    file_obj = form.cleaned_data["file"]
    title = (form.cleaned_data.get("title") or "").strip() or os.path.basename(
        file_obj.name.replace("\\", "/")
    )

    SessionMaterial.objects.create(
        case=case,
        session_number=session_number,
        appointment=card.appointment,
        file=file_obj,
        title=title,
        uploaded_by=request.user,
    )
    messages.success(request, f"{session_number}회기 자료가 첨부되었습니다.")
    return redirect("client:case_detail", pk=case.pk)


@role_required(UserRole.CLIENT)
def client_request_appointment(request, pk):
    """내담자 예약 신청 (PENDING)"""
    case = _get_client_case(request, pk, active_only=True)

    if request.method != "POST":
        return redirect("client:case_detail", pk=case.pk)

    if not case.counselor_id:
        messages.error(request, "담당 상담사가 배정되기 전에는 예약 신청을 할 수 없습니다.")
        return redirect("client:case_detail", pk=case.pk)

    if case.appointments.filter(status=AppointmentStatus.PENDING).exists():
        messages.warning(request, "이미 대기 중인 예약 신청이 있습니다.")
        return redirect("client:case_detail", pk=case.pk)

    form = AppointmentRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, "희망 시간을 확인해 주세요.")
        for err in form.errors.values():
            messages.error(request, err[0])
        return redirect("client:case_detail", pk=case.pk)

    try:
        create_appointment_request(
            case=case,
            client=request.user,
            scheduled_at=form.cleaned_data["scheduled_at"],
        )
    except AppointmentServiceError as exc:
        messages.error(request, str(exc))
    except IntegrityError:
        messages.error(request, "예약 신청 처리 중 오류가 발생했습니다.")
    else:
        messages.success(
            request,
            "예약 신청이 접수되었습니다. 담당 상담사가 확인 후 확정해 드립니다.",
        )

    return redirect("client:case_detail", pk=case.pk)


def _counselor_active_cases(user):
    """상담사 대시보드용 활성 사례 — 본인에게 배정된 사례만"""
    return (
        Case.objects.filter(status=CaseStatus.ACTIVE, counselor=user)
        .select_related("client", "application", "counselor")
        .order_by("-opened_at")
    )


def _get_counselor_case(request, pk):
    """배정된 담당 사례만 조회"""
    return get_object_or_404(
        Case.objects.select_related("client", "application", "counselor"),
        pk=pk,
        counselor=request.user,
    )


def _journal_counselor_for_save(request, case):
    """일지에 기록할 상담사 (슈퍼유저 테스트 시 담당 상담사 우선)"""
    if request.user.is_superuser and case.counselor_id:
        return case.counselor
    return request.user


def _next_session_number(case):
    last = case.journals.order_by("-session_number").first()
    return (last.session_number + 1) if last else 1


def user_can_download_journal_pdf(user, journal):
    """PDF 다운로드: 해당 사례 담당 상담사만"""
    if not user.is_authenticated:
        return False
    return journal.case.counselor_id == user.id


def user_can_view_journal(user, journal):
    """일지 상세 열람 (담당 상담사만)"""
    return user_can_download_journal_pdf(user, journal)


def user_can_edit_journal(user, journal):
    """일지 수정: 해당 사례 담당 상담사만"""
    return user_can_download_journal_pdf(user, journal)


def user_can_download_initial_record_pdf(user, record):
    """초기상담 기록지 PDF: 해당 사례 담당 상담사만"""
    if not user.is_authenticated:
        return False
    return record.case.counselor_id == user.id


def user_can_download_termination_record_pdf(user, record):
    """종결기록지 PDF: 해당 사례 담당 상담사만"""
    if not user.is_authenticated:
        return False
    return record.case.counselor_id == user.id


def _get_download_password_from_request(
    request,
    *,
    redirect_url: str,
    label: str = "파일",
):
    """POST pdf_password / zip_password — 4자 미만이면 None과 redirect 반환."""
    password = (
        request.POST.get("zip_password")
        or request.POST.get("pdf_password")
        or ""
    ).strip()
    if len(password) < 4:
        messages.error(request, f"{label} 암호는 4자 이상 입력해 주세요.")
        next_url = (request.POST.get("next") or redirect_url).strip()
        return None, redirect(next_url or redirect_url)
    return password, None


def _get_pdf_password_from_request(request, *, redirect_url: str):
    """POST pdf_password — 4자 미만이면 None과 redirect 반환."""
    return _get_download_password_from_request(
        request, redirect_url=redirect_url, label="PDF"
    )


def _pdf_file_response(pdf_bytes: bytes, *, filename: str, ascii_name: str) -> HttpResponse:
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    response["Content-Length"] = len(pdf_bytes)
    return response


PDF_DOWNLOAD_DISABLED_MESSAGE = "기록 저장 후 다운로드할 수 있습니다."


def _record_pdf_context(can_download: bool, download_url: str = "") -> dict:
    context = {
        "can_download_pdf": can_download,
        "pdf_download_disabled_message": PDF_DOWNLOAD_DISABLED_MESSAGE,
    }
    if can_download:
        context["pdf_download_url"] = download_url
        context["pdf_password_notice"] = PDF_PASSWORD_NOTICE
    return context


def _can_download_initial_record_pdf(request, record) -> bool:
    return bool(
        record
        and not record.is_draft
        and user_can_download_initial_record_pdf(request.user, record)
    )


def _can_download_journal_pdf(request, journal) -> bool:
    return bool(
        journal
        and not journal.is_draft
        and user_can_download_journal_pdf(request.user, journal)
    )


def _can_download_termination_record_pdf(request, record) -> bool:
    return bool(
        record
        and not record.is_draft
        and user_can_download_termination_record_pdf(request.user, record)
    )


def _get_case_journal(request, case_pk, session_number):
    """사례·회기 번호로 일지 조회 (상담사 사례 접근 권한 포함)"""
    case = _get_counselor_case(request, case_pk)
    journal = get_object_or_404(
        CounselingJournal.objects.select_related(
            "case", "case__client", "case__application", "case__counselor", "counselor"
        ),
        case=case,
        session_number=session_number,
    )
    return case, journal


def _render_journal_form(request, case, form, *, is_edit=False, journal=None):
    context = {
        "case": case,
        "form": form,
        "client_summary": _case_client_summary(case),
        "is_edit": is_edit,
        "journal": journal,
        "page_title": "상담일지 수정" if is_edit else "상담일지 작성",
        "breadcrumb_label": "일지 수정" if is_edit else "일지 작성",
        "submit_label": "저장하기" if is_edit else "일지 저장",
    }
    can_download = _can_download_journal_pdf(request, journal)
    context.update(
        _record_pdf_context(
            can_download,
            reverse(
                "counselor:journal_pdf",
                kwargs={
                    "pk": case.pk,
                    "session_number": journal.session_number,
                },
            )
            if can_download
            else "",
        )
    )
    return render(request, "counselor/journal_form.html", context)


@counselor_required
def counselor_dashboard(request):
    """상담사 전용 대시보드 — 활성 사례 목록"""
    cases = _counselor_active_cases(request.user)
    pending_appointments = (
        Appointment.objects.filter(
            counselor=request.user,
            status=AppointmentStatus.PENDING,
        )
        .select_related("case", "client", "case__application")
        .order_by("scheduled_at")
    )
    return render(
        request,
        "counselor/dashboard.html",
        {
            "cases": cases,
            "active_count": cases.count(),
            "pending_appointments": pending_appointments,
            "pending_count": pending_appointments.count(),
        },
    )


@role_required(UserRole.COUNSELOR, UserRole.ADMIN)
def counselor_case_detail(request, pk):
    """사례 상세 + 상담일지 목록"""
    case = _get_board_manage_case(request, pk)
    application = case.application
    schedule = application.preferred_schedule or {}

    session_cards = build_case_session_cards_cached(case)
    sync_orphan_session_requests(case, session_cards)

    client_profile = _get_client_profile(case.client)
    student_id = ""
    if client_profile and client_profile.student_id:
        student_id = client_profile.student_id
    elif schedule.get("student_id"):
        student_id = schedule["student_id"]

    appointments = case.appointments.select_related("zoom_meeting").order_by("-scheduled_at")

    counselor_cohort = get_counselor_cohort(request.user)
    total_sessions = max(case.total_sessions, 1)
    cohort_journals_by_session: dict[int, list] = {}
    if counselor_cohort is not None:
        raw_by_session = get_cohort_peer_journals_by_session(
            counselor_cohort,
            max_session=total_sessions,
        )
        for session_number, journals in raw_by_session.items():
            cohort_journals_by_session[session_number] = [
                _build_cohort_journal_entry(
                    journal,
                    requesting_counselor=request.user,
                    own_case_id=case.pk,
                )
                for journal in journals
            ]
    for n in range(1, total_sessions + 1):
        cohort_journals_by_session.setdefault(n, [])

    sessions = build_counselor_session_views(
        case,
        prebuilt_cards=session_cards,
        cohort_journals_by_session=cohort_journals_by_session,
    )
    upcoming_appointment = (
        case.appointments.filter(
            scheduled_at__gte=timezone.now(),
            status=AppointmentStatus.CONFIRMED,
        )
        .select_related("zoom_meeting")
        .order_by("scheduled_at")
        .first()
    )

    return render(
        request,
        "counselor/case_detail.html",
        {
            "case": case,
            "application": application,
            "presenting_complaint_categories": presenting_complaint_categories_for_case(case),
            "presenting_reason": presenting_written_reason_for_case(case),
            "student_id": student_id,
            "schedule": schedule,
            "journal_count": case.journals.count(),
            "appointments": appointments,
            "sessions": sessions,
            "upcoming_appointment": upcoming_appointment,
            "pending_for_case": case.appointments.filter(
                status=AppointmentStatus.PENDING
            ).order_by("session_number", "scheduled_at"),
            "shared_materials": get_case_shared_materials(case),
            "can_manage_board": user_can_manage_board(request.user, case),
            "pending_session_requests": case.appointments.filter(
                status=AppointmentStatus.PENDING
            ).order_by("session_number", "scheduled_at"),
            "cancel_pending_for_case": case.appointments.filter(
                status=AppointmentStatus.CANCEL_PENDING
            ).order_by("session_number", "scheduled_at"),
            "schedule_change_requests_for_case": get_schedule_change_requests_for_counselor(
                case
            ),
            "counselor_cohort": counselor_cohort,
            "is_admin_view": request.user.is_superuser
            or request.user.role == UserRole.ADMIN,
            "zoom_host_key_configured": is_zoom_host_key_configured(),
            "zoom_host_key": get_zoom_host_key() if is_zoom_host_key_configured() else "",
            "consent_rows": build_consent_rows(application),
        },
    )


@counselor_required
@require_POST
def counselor_session_appointment_confirm(request, case_pk, appointment_pk):
    """회기별 예약 확정 (+ Zoom) — 모델: scheduling.Appointment."""
    print(
        "[DEBUG confirm]",
        "case_pk=", case_pk,
        "appointment_pk(URL)=", appointment_pk,
        "POST=", dict(request.POST),
        "body=", request.body[:500],
    )
    posted_appointment_id = request.POST.get("appointment_id")
    if posted_appointment_id and str(posted_appointment_id) != str(appointment_pk):
        print(
            "[DEBUG confirm] POST appointment_id mismatch:",
            posted_appointment_id,
            "vs URL",
            appointment_pk,
        )
        appointment_pk = posted_appointment_id

    case, appointment = _get_counselor_case_appointment(
        request, case_pk, appointment_pk
    )
    session_number = appointment.session_number or 1
    posted_session_id = request.POST.get("session_id")
    print(
        "[DEBUG confirm] Appointment found:",
        appointment.pk,
        "status=", appointment.status,
        "session_number=", session_number,
        "posted_session_id=", posted_session_id,
    )

    if appointment.status != AppointmentStatus.PENDING:
        err = _ajax_error_response(request, "대기 중인 예약만 확정할 수 있습니다.")
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    if case.counseling_method == CounselingMethod.REMOTE and not is_zoom_configured():
        err = _ajax_error_response(
            request,
            "Zoom API가 설정되지 않아 비대면 예약을 확정할 수 없습니다. .env 설정을 확인해 주세요.",
        )
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    try:
        confirm_appointment_with_zoom(appointment)
        case.refresh_from_db()
        session_label = (
            f"{appointment.session_number}회기"
            if appointment.session_number
            else "상담"
        )
        success_msg = (
            f"{session_label} 예약이 확정되었습니다. "
            f"({appointment.scheduled_at:%Y-%m-%d %H:%M})"
        )
        if case.zoom_meeting_url:
            success_msg += " Zoom 회의가 생성되었습니다."
        if _is_ajax_request(request):
            response = _counselor_session_card_response(
                request, case, session_number
            )
            response["X-Session-Message"] = success_msg
            return response
        messages.success(request, success_msg)
    except ZoomNotConfiguredError as exc:
        err = _ajax_error_response(request, str(exc))
        if err:
            return err
    except ZoomAPIError as exc:
        err = _ajax_error_response(request, str(exc))
        if err:
            return err
    except AppointmentServiceError as exc:
        err = _ajax_error_response(request, str(exc))
        if err:
            return err
    except IntegrityError:
        err = _ajax_error_response(
            request,
            "선택한 시간에 이미 다른 확정 예약이 있습니다.",
        )
        if err:
            return err
    except ValidationError as exc:
        err = _ajax_error_response(
            request,
            exc.messages[0] if getattr(exc, "messages", None) else str(exc),
        )
        if err:
            return err
    except Exception:
        logger.exception(
            "Session appointment confirm failed (case=%s, appointment=%s)",
            case.pk,
            appointment.pk,
        )
        err = _ajax_error_response(
            request,
            "예약 확정 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        )
        if err:
            return err
    return redirect("counselor:case_detail", pk=case.pk)


@counselor_required
@require_POST
def counselor_session_appointment_reject(request, case_pk, appointment_pk):
    """회기별 예약 반려."""
    case, appointment = _get_counselor_case_appointment(
        request, case_pk, appointment_pk
    )
    session_number = appointment.session_number or 1

    if appointment.status != AppointmentStatus.PENDING:
        err = _ajax_error_response(request, "대기 중인 예약만 반려할 수 있습니다.")
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    form = AppointmentRejectForm(request.POST)
    if not form.is_valid():
        err = _ajax_error_response(request, "반려 사유를 확인해 주세요.")
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    reason = form.cleaned_data["reject_reason"]
    try:
        reject_appointment_request(appointment, reason=reason)
    except AppointmentServiceError as exc:
        err = _ajax_error_response(request, str(exc))
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    success_msg = f"{session_label} 예약 요청을 반려했습니다."
    if _is_ajax_request(request):
        response = _counselor_session_card_response(request, case, session_number)
        response["X-Session-Message"] = success_msg
        return response
    messages.success(request, success_msg)
    return redirect("counselor:case_detail", pk=case.pk)


@counselor_required
@require_POST
def counselor_session_cancel_approve(request, case_pk, appointment_pk):
    """회기별 취소 요청 승인 — 예약 취소 확정."""
    case, appointment = _get_counselor_case_appointment(
        request, case_pk, appointment_pk
    )
    session_number = appointment.session_number or 1

    if appointment.status != AppointmentStatus.CANCEL_PENDING:
        err = _ajax_error_response(request, "취소 대기 중인 예약만 승인할 수 있습니다.")
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    try:
        approve_appointment_cancel_request(appointment)
    except ValueError:
        err = _ajax_error_response(request, "이미 처리된 취소 요청입니다.")
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    send_cancel_approval_notification(appointment)
    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    success_msg = f"{session_label} 취소 요청을 승인했습니다."
    if _is_ajax_request(request):
        response = _counselor_session_card_response(request, case, session_number)
        response["X-Session-Message"] = success_msg
        return response
    messages.success(request, success_msg)
    return redirect("counselor:case_detail", pk=case.pk)


@counselor_required
@require_POST
def counselor_session_cancel_reject(request, case_pk, appointment_pk):
    """회기별 취소 요청 반려 — 예약 확정 유지."""
    case, appointment = _get_counselor_case_appointment(
        request, case_pk, appointment_pk
    )
    session_number = appointment.session_number or 1

    if appointment.status != AppointmentStatus.CANCEL_PENDING:
        err = _ajax_error_response(request, "취소 대기 중인 예약만 반려할 수 있습니다.")
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    form = AppointmentRejectForm(request.POST)
    if not form.is_valid():
        err = _ajax_error_response(request, "반려 사유를 확인해 주세요.")
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    reason = form.cleaned_data["reject_reason"]
    try:
        reject_appointment_cancel_request(appointment, reason=reason)
    except AppointmentOperationError as exc:
        err = _ajax_error_response(request, exc.message)
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)
    except ValueError:
        err = _ajax_error_response(request, "이미 처리된 취소 요청입니다.")
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    send_cancel_rejection_notification(appointment, reason=reason)
    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    success_msg = f"{session_label} 취소 요청을 반려했습니다. 예약이 유지됩니다."
    if _is_ajax_request(request):
        response = _counselor_session_card_response(request, case, session_number)
        response["X-Session-Message"] = success_msg
        return response
    messages.success(request, success_msg)
    return redirect("counselor:case_detail", pk=case.pk)


@counselor_required
@require_POST
def counselor_session_appointment_cancel(request, case_pk, appointment_pk):
    """상담사 — 확정 예약 직접 취소."""
    case, appointment = _get_counselor_case_appointment(
        request, case_pk, appointment_pk
    )
    session_number = appointment.session_number or 1

    form = CancelRequestForm(request.POST)
    if not form.is_valid():
        err = _ajax_error_response(request, "취소 사유를 확인해 주세요.")
        if err:
            return err
        messages.error(request, "취소 사유를 확인해 주세요.")
        return redirect("counselor:case_detail", pk=case.pk)

    cancel_reason = form.cleaned_data["cancel_reason"]
    try:
        appointment = cancel_confirmed_appointment_by_counselor(
            appointment,
            cancel_reason=cancel_reason,
        )
    except AppointmentOperationError as exc:
        err = _ajax_error_response(request, exc.message)
        if err:
            return err
        messages.error(request, exc.message)
        return redirect("counselor:case_detail", pk=case.pk)

    send_counselor_direct_cancel_notification(
        appointment,
        cancel_reason=cancel_reason,
    )
    session_label = (
        f"{appointment.session_number}회기"
        if appointment.session_number
        else "상담"
    )
    success_msg = f"{session_label} 예약을 취소했습니다."
    if _is_ajax_request(request):
        response = _counselor_session_card_response(request, case, session_number)
        response["X-Session-Message"] = success_msg
        return response
    messages.success(request, success_msg)
    return redirect("counselor:case_detail", pk=case.pk)


@counselor_required
@require_POST
def counselor_session_schedule_change_approve(request, case_pk, request_pk):
    """회기별 확정 일정 변경 요청 승인."""
    case, schedule_request = _get_counselor_schedule_change_request(
        request, case_pk, request_pk
    )
    session_number = schedule_request.session_number

    try:
        appointment, old_scheduled_at, zoom_warning = approve_session_schedule_change_request(
            schedule_request
        )
    except AppointmentOperationError as exc:
        err = _ajax_error_response(request, exc.message)
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    send_schedule_change_approval_notification(
        appointment,
        old_scheduled_at=old_scheduled_at,
        new_scheduled_at=appointment.scheduled_at,
    )
    session_label = f"{session_number}회기"
    success_msg = (
        f"{session_label} 일정 변경을 승인했습니다. "
        f"({format_local_datetime(old_scheduled_at)} → "
        f"{format_local_datetime(appointment.scheduled_at)})"
    )
    if zoom_warning:
        success_msg += (
            " 다만 Zoom 회의 일정은 자동 갱신되지 않았습니다. "
            "Zoom 앱 Scope(meeting:update:meeting) 설정을 확인해 주세요."
        )
    if _is_ajax_request(request):
        response = _counselor_session_card_response(request, case, session_number)
        response["X-Session-Message"] = success_msg
        return response
    messages.success(request, success_msg)
    return redirect("counselor:case_detail", pk=case.pk)


@counselor_required
@require_POST
def counselor_session_schedule_change_reject(request, case_pk, request_pk):
    """회기별 확정 일정 변경 요청 반려 — 기존 일정 유지."""
    case, schedule_request = _get_counselor_schedule_change_request(
        request, case_pk, request_pk
    )
    session_number = schedule_request.session_number

    form = AppointmentRejectForm(request.POST)
    if not form.is_valid():
        err = _ajax_error_response(request, "반려 사유를 확인해 주세요.")
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    reason = form.cleaned_data["reject_reason"]
    try:
        appointment, preferred_datetime = reject_session_schedule_change_request(
            schedule_request,
            reason=reason,
        )
    except AppointmentOperationError as exc:
        err = _ajax_error_response(request, exc.message)
        if err:
            return err
        return redirect("counselor:case_detail", pk=case.pk)

    send_schedule_change_rejection_notification(
        appointment,
        preferred_datetime=preferred_datetime,
        reason=reason,
    )
    session_label = f"{session_number}회기"
    success_msg = f"{session_label} 일정 변경 요청을 반려했습니다. 기존 일정이 유지됩니다."
    if _is_ajax_request(request):
        response = _counselor_session_card_response(request, case, session_number)
        response["X-Session-Message"] = success_msg
        return response
    messages.success(request, success_msg)
    return redirect("counselor:case_detail", pk=case.pk)


@board_manager_required
@require_POST
def counselor_board_post_create(request, case_pk):
    """게시판 게시글 작성 (상담사·관리자)"""
    case = _get_board_manage_case(request, case_pk)
    if not user_can_manage_board(request.user, case):
        raise PermissionDenied("게시판 작성 권한이 없습니다.")

    form = BoardPostForm(request.POST, request.FILES)
    if not form.is_valid():
        for field, errors in form.errors.items():
            if errors:
                messages.error(request, errors[0])
                break
        else:
            messages.error(request, "입력 내용을 확인해 주세요.")
        return redirect("counselor:case_detail", pk=case.pk)

    file_obj = form.cleaned_data.get("file")
    SessionMaterial.objects.create(
        case=case,
        title=form.cleaned_data["title"].strip(),
        content=form.cleaned_data.get("content") or "",
        file=file_obj,
        uploaded_by=request.user,
        is_shared=True,
    )
    messages.success(request, "게시판에 글이 등록되었습니다.")
    return redirect("counselor:case_detail", pk=case.pk)


@board_manager_required
@require_POST
def counselor_board_post_edit(request, case_pk, material_pk):
    """게시판 게시글 수정 (상담사·관리자)"""
    case = _get_board_manage_case(request, case_pk)
    if not user_can_manage_board(request.user, case):
        raise PermissionDenied("게시판 수정 권한이 없습니다.")

    material = _get_case_shared_material(case, material_pk)
    form = BoardPostForm(
        request.POST,
        request.FILES,
        existing_file=material.has_attachment,
    )
    if not form.is_valid():
        for field, errors in form.errors.items():
            if errors:
                messages.error(request, errors[0])
                break
        else:
            messages.error(request, "입력 내용을 확인해 주세요.")
        return redirect("counselor:case_detail", pk=case.pk)

    material.title = form.cleaned_data["title"].strip()
    material.content = form.cleaned_data.get("content") or ""
    new_file = form.cleaned_data.get("file")
    if new_file:
        if material.file:
            material.file.delete(save=False)
        material.file = new_file
    material.save()
    messages.success(request, "게시글이 수정되었습니다.")
    return redirect("counselor:case_detail", pk=case.pk)


@board_manager_required
def counselor_shared_material_file(request, case_pk, material_pk):
    """게시판 첨부 파일 다운로드 (상담사·관리자)"""
    case = _get_board_manage_case(request, case_pk)
    material = _get_case_shared_material(case, material_pk)
    return _session_material_file_response(material)


@board_manager_required
@require_POST
def counselor_shared_material_delete(request, case_pk, material_pk):
    """게시판 게시글 삭제 (상담사·관리자)"""
    case = _get_board_manage_case(request, case_pk)
    if not user_can_manage_board(request.user, case):
        raise PermissionDenied("게시판 삭제 권한이 없습니다.")

    material = _get_case_shared_material(case, material_pk)
    if material.file:
        material.file.delete(save=False)
    material.delete()
    messages.success(request, "게시글이 삭제되었습니다.")
    return redirect("counselor:case_detail", pk=case.pk)


@counselor_required
@require_POST
def counselor_cohort_journal_pdf(request, journal_pk):
    """동기 기수 상담일지 PDF 다운로드 (암호화)."""
    journal = get_object_or_404(
        CounselingJournal.objects.select_related(
            "case",
            "case__client",
            "case__application",
            "counselor",
        ),
        pk=journal_pk,
    )
    fallback = (request.POST.get("next") or "").strip() or reverse("counselor:dashboard")
    if not user_can_download_cohort_journal(request.user, journal):
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


@counselor_required
@require_POST
def counselor_update_session_status(request, case_pk, appointment_pk):
    """상담사 회기 상태 변경 (진행중 ↔ 완료)."""
    case = _get_counselor_case(request, case_pk)
    appointment = get_object_or_404(
        Appointment.objects.select_related("case"),
        pk=appointment_pk,
        case=case,
    )
    new_status = request.POST.get("status", "").strip()
    allowed = {
        "CONFIRMED": AppointmentStatus.CONFIRMED,
        "COMPLETED": AppointmentStatus.COMPLETED,
    }
    if new_status not in allowed:
        messages.error(request, "변경할 수 없는 상태입니다.")
        return redirect("counselor:case_detail", pk=case.pk)

    if appointment.status not in (
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.COMPLETED,
    ):
        messages.error(request, "확정된 회기만 상태를 변경할 수 있습니다.")
        return redirect("counselor:case_detail", pk=case.pk)

    target = allowed[new_status]
    if appointment.status == target:
        return redirect("counselor:case_detail", pk=case.pk)

    appointment.status = target
    appointment.save(update_fields=["status", "updated_at"])
    label = "진행중" if target == AppointmentStatus.CONFIRMED else "완료"
    messages.success(request, f"{appointment.scheduled_at:%Y-%m-%d %H:%M} 회기 상태가 「{label}」으로 변경되었습니다.")
    return redirect("counselor:case_detail", pk=case.pk)


@counselor_required
@require_POST
def counselor_consent_upload(request, case_pk, doc_type):
    """필수 동의서(오프라인 스캔) 업로드·재업로드."""
    case = _get_counselor_case(request, case_pk)
    form = ConsentUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        file_errors = form.errors.get("file")
        if file_errors:
            messages.error(request, file_errors[0])
        else:
            messages.error(request, "파일을 확인해 주세요.")
        return redirect("counselor:case_detail", pk=case.pk)

    try:
        upsert_counselor_consent(
            case=case,
            doc_type=doc_type,
            file_obj=form.cleaned_data["file"],
            uploaded_by=request.user,
        )
    except ValueError:
        raise Http404 from None

    messages.success(request, "동의서가 제출되었습니다.")
    return redirect("counselor:case_detail", pk=case.pk)


@counselor_required
@require_POST
def counselor_session_material_upload(request, case_pk, session_number):
    """회기별 자료 첨부 (상담사)."""
    case = _get_counselor_case(request, case_pk)
    card = _get_session_card(case, session_number)
    if not card or not card.show_session_actions:
        messages.error(request, "이 회기에는 자료를 첨부할 수 없습니다.")
        return redirect("counselor:case_detail", pk=case.pk)

    form = SessionMaterialUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        file_errors = form.errors.get("file")
        if file_errors:
            messages.error(request, file_errors[0])
        else:
            messages.error(request, "파일을 확인해 주세요.")
        return redirect("counselor:case_detail", pk=case.pk)

    file_obj = form.cleaned_data["file"]
    title = (form.cleaned_data.get("title") or "").strip() or os.path.basename(
        file_obj.name.replace("\\", "/")
    )

    SessionMaterial.objects.create(
        case=case,
        session_number=session_number,
        appointment=card.appointment,
        file=file_obj,
        title=title,
        uploaded_by=request.user,
    )
    messages.success(request, f"{session_number}회기 자료가 첨부되었습니다.")
    return redirect("counselor:case_detail", pk=case.pk)


@counselor_required
def counselor_session_material_file(request, case_pk, session_number, material_pk):
    """회기별 첨부 자료 다운로드 (상담사)"""
    case = _get_counselor_case(request, case_pk)
    material = _get_session_material_for_case(case, session_number, material_pk)
    return _session_material_file_response(material)


@counselor_required
@require_POST
def counselor_session_material_delete(request, case_pk, session_number, material_pk):
    """회기별 첨부 자료 삭제 (본인 업로드만)"""
    case = _get_counselor_case(request, case_pk)
    return _delete_session_material(
        request,
        case,
        session_number,
        material_pk,
        redirect_to=reverse("counselor:case_detail", kwargs={"pk": case.pk}),
    )


@counselor_required
def case_book_appointment(request, pk):
    """
    (레거시) 사례별 예약 URL.
    대기 중(PENDING) 예약이 있으면 확정·관리 화면으로 이동합니다.
    """
    case = _get_counselor_case(request, pk)
    pending = (
        case.appointments.filter(status=AppointmentStatus.PENDING)
        .order_by("-created_at")
        .first()
    )
    if pending:
        return redirect("counselor:appointment_manage", pk=pending.pk)
    messages.info(
        request,
        "대기 중인 예약 신청이 없습니다. 회기별 「일정 입력 및 확정」으로 "
        "직접 예약하거나, 내담자 신청을 기다릴 수 있습니다.",
    )
    return redirect("counselor:case_detail", pk=case.pk)


@counselor_required
def counselor_session_appointment_book(request, case_pk, session_number):
    """상담사 — 내담자 신청 없이 회기 일정 입력·확정."""
    case = _get_counselor_case(request, case_pk)
    card = _get_session_card(case, session_number)
    if not card or not card.show_counselor_direct_booking:
        messages.error(request, "이 회기는 일정을 직접 확정할 수 없습니다.")
        return redirect("counselor:case_detail", pk=case.pk)

    if request.method == "POST":
        form = AppointmentScheduleForm(
            request.POST,
            counselor_label=True,
            calendar_picker=True,
        )
        if not form.is_valid():
            messages.error(request, "입력 내용을 확인해 주세요.")
        elif (
            case.counseling_method == CounselingMethod.REMOTE
            and not is_zoom_configured()
        ):
            messages.error(
                request,
                "Zoom API가 설정되지 않아 비대면 예약을 확정할 수 없습니다. .env 설정을 확인해 주세요.",
            )
        else:
            try:
                appointment, zoom = create_and_confirm_appointment_by_counselor(
                    case=case,
                    session_number=session_number,
                    scheduled_at=form.cleaned_data["scheduled_at"],
                    duration_minutes=form.cleaned_data["duration_minutes"],
                )
                success_msg = (
                    f"{session_number}회기 예약이 확정되었습니다. "
                    f"({appointment.scheduled_at:%Y-%m-%d %H:%M})"
                )
                if zoom:
                    success_msg += " Zoom 회의가 생성되었습니다."
                messages.success(request, success_msg)
                return redirect("counselor:case_detail", pk=case.pk)
            except ZoomNotConfiguredError as exc:
                messages.error(request, str(exc))
            except ZoomAPIError as exc:
                messages.error(request, str(exc))
            except AppointmentServiceError as exc:
                messages.error(request, str(exc))
            except IntegrityError:
                messages.error(
                    request,
                    "선택한 시간에 이미 다른 확정 예약이 있습니다.",
                )
            except ValidationError as exc:
                messages.error(
                    request,
                    exc.messages[0] if getattr(exc, "messages", None) else str(exc),
                )
            except Exception:
                logger.exception(
                    "Counselor session book failed (case=%s, session=%s)",
                    case.pk,
                    session_number,
                )
                messages.error(
                    request,
                    "예약 확정 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                )
    else:
        form = AppointmentScheduleForm(counselor_label=True, calendar_picker=True)

    return render(
        request,
        "counselor/session_booking_calendar.html",
        {
            "case": case,
            "session_number": session_number,
            "form": form,
            "zoom_configured": is_zoom_configured(),
            "default_duration_minutes": form.fields["duration_minutes"].initial
            or DEFAULT_APPOINTMENT_DURATION_MINUTES,
            **build_booking_calendar_context(
                case,
                session_number=session_number,
                role="counselor",
            ),
        },
    )


@counselor_required
def counselor_session_appointment_reschedule(request, case_pk, session_number):
    """상담사 — 확정 회기 일정 변경 (과거 일정 포함)."""
    case = _get_counselor_case(request, case_pk)
    card = _get_session_card(case, session_number)
    if not card or not card.show_counselor_direct_reschedule:
        messages.error(request, "이 회기는 일정을 변경할 수 없습니다.")
        return redirect("counselor:case_detail", pk=case.pk)

    appointment = card.appointment
    if appointment is None or appointment.status != AppointmentStatus.CONFIRMED:
        messages.error(request, "확정된 예약만 일정을 변경할 수 있습니다.")
        return redirect("counselor:case_detail", pk=case.pk)

    if request.method == "POST":
        form = AppointmentScheduleForm(
            request.POST,
            counselor_label=True,
            calendar_picker=True,
        )
        if not form.is_valid():
            messages.error(request, "입력 내용을 확인해 주세요.")
        elif (
            case.counseling_method == CounselingMethod.REMOTE
            and not is_zoom_configured()
        ):
            messages.error(
                request,
                "Zoom API가 설정되지 않아 비대면 일정을 변경할 수 없습니다. .env 설정을 확인해 주세요.",
            )
        else:
            old_scheduled_at = appointment.scheduled_at
            try:
                appointment, zoom_warning = reschedule_confirmed_appointment(
                    appointment,
                    new_scheduled_at=form.cleaned_data["scheduled_at"],
                )
            except AppointmentServiceError as exc:
                messages.error(request, str(exc))
            else:
                success_msg = (
                    f"{session_number}회기 일정이 변경되었습니다. "
                    f"({format_local_datetime(old_scheduled_at)} → "
                    f"{format_local_datetime(appointment.scheduled_at)})"
                )
                if zoom_warning:
                    success_msg += f" (Zoom: {zoom_warning})"
                messages.success(request, success_msg)
                return redirect("counselor:case_detail", pk=case.pk)
    else:
        form = AppointmentScheduleForm(
            counselor_label=True,
            calendar_picker=True,
            initial={
                "scheduled_at": timezone.localtime(appointment.scheduled_at),
                "duration_minutes": appointment.duration_minutes,
            },
        )

    return render(
        request,
        "counselor/session_booking_calendar.html",
        {
            "case": case,
            "session_number": session_number,
            "form": form,
            "zoom_configured": is_zoom_configured(),
            "default_duration_minutes": appointment.duration_minutes
            or DEFAULT_APPOINTMENT_DURATION_MINUTES,
            "booking_is_reschedule": True,
            "booking_page_title": f"{session_number}회기 — 일정 변경",
            "booking_page_c3": f"{session_number}회기 일정 변경",
            "booking_submit_label": "일정 변경 확정",
            "booking_help_text": (
                "현재 확정 일정을 다른 날짜·시간으로 변경합니다. "
                "이미 지난 예약도 변경할 수 있습니다."
            ),
            **build_booking_calendar_context(
                case,
                appointment=appointment,
                session_number=session_number,
                role="counselor",
            ),
        },
    )


def _case_client_summary(case):
    """일지 작성 화면용 내담자·사례 요약"""
    application = case.application
    schedule = application.preferred_schedule or {}
    client_profile = _get_client_profile(case.client)
    student_id = ""
    if client_profile and client_profile.student_id:
        student_id = client_profile.student_id
    elif schedule.get("student_id"):
        student_id = schedule["student_id"]
    return {
        "client_name": case.client.name,
        "student_id": student_id,
        "phone": case.client.phone or "—",
        "email": case.client.email,
        "counseling_type": application.counseling_type,
        "case_number": case.case_number,
        "preferred_date": schedule.get("preferred_date", ""),
        "preferred_time": schedule.get("preferred_time", ""),
    }


@counselor_required
def journal_create(request, pk):
    """새 상담일지 작성"""
    case = _get_counselor_case(request, pk)

    if request.method == "POST":
        form = CounselingJournalForm(request.POST, case=case)
        if form.is_valid():
            journal = form.save(commit=False)
            journal.case = case
            journal.counselor = _journal_counselor_for_save(request, case)
            journal.save()
            finalize_completed_journal(journal)
            messages.success(
                request,
                f"{journal.session_number}회기 상담일지가 저장되었습니다.",
            )
            return redirect("counselor:case_detail", pk=case.pk)
        messages.error(request, "입력 내용을 확인해 주세요.")
    else:
        initial = {"session_number": _next_session_number(case)}
        session_param = request.GET.get("session")
        if session_param:
            try:
                initial["session_number"] = int(session_param)
            except (TypeError, ValueError):
                pass
        form = CounselingJournalForm(
            case=case,
            initial=initial,
        )

    return _render_journal_form(request, case, form, is_edit=False)


@counselor_required
def journal_detail(request, pk, session_number):
    """상담일지 상세 (/case/<uuid>/journal/<회기>)"""
    case, journal = _get_case_journal(request, pk, session_number)
    if not user_can_view_journal(request.user, journal):
        raise PermissionDenied("이 상담일지를 열람할 권한이 없습니다.")
    return render(
        request,
        "counselor/journal_detail.html",
        {
            "journal": journal,
            "case": case,
            "client_summary": _case_client_summary(case),
            "journal_breadcrumb_label": f"{journal.session_number}회기 상담일지",
            "can_edit": user_can_edit_journal(request.user, journal),
            **_record_pdf_context(
                _can_download_journal_pdf(request, journal),
                reverse(
                    "counselor:journal_pdf",
                    kwargs={"pk": case.pk, "session_number": journal.session_number},
                ),
            ),
        },
    )


@counselor_required
@require_POST
def journal_pdf(request, pk, session_number):
    """상담일지 PDF 다운로드 (사용자 입력 암호로 암호화)."""
    case, journal = _get_case_journal(request, pk, session_number)
    fallback = reverse(
        "counselor:journal_detail",
        kwargs={"pk": case.pk, "session_number": session_number},
    )
    if not user_can_download_journal_pdf(request.user, journal):
        raise PermissionDenied("PDF 다운로드 권한이 없습니다.")
    pdf_password, redirect_response = _get_pdf_password_from_request(
        request, redirect_url=fallback
    )
    if redirect_response:
        return redirect_response

    client_summary = _journal_client_summary(case, mask_private=False)
    pdf_bytes = build_journal_pdf(
        journal,
        client_summary=client_summary,
        user_password=pdf_password,
    )
    filename = journal_pdf_filename(journal)
    ascii_name = f"journal_{case.case_number}_{journal.session_number}.pdf".replace(
        "/", "-"
    )
    return _pdf_file_response(pdf_bytes, filename=filename, ascii_name=ascii_name)


@counselor_required
def journal_edit(request, pk, session_number):
    """상담일지 수정"""
    case, journal = _get_case_journal(request, pk, session_number)
    if not user_can_edit_journal(request.user, journal):
        raise PermissionDenied("이 상담일지를 수정할 권한이 없습니다.")

    if request.method == "POST":
        form = CounselingJournalForm(request.POST, instance=journal, case=case)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.case = case
            if not updated.counselor_id:
                updated.counselor = _journal_counselor_for_save(request, case)
            updated.save()
            finalize_completed_journal(updated)
            messages.success(
                request,
                f"{updated.session_number}회기 상담일지가 수정되었습니다.",
            )
            return redirect("counselor:case_detail", pk=case.pk)
        messages.error(request, "입력 내용을 확인해 주세요.")
    else:
        form = CounselingJournalForm(instance=journal, case=case)

    return _render_journal_form(request, case, form, is_edit=True, journal=journal)


def _initial_record_client_summary(case):
    """초기상담 기록지 상단 — 매칭된 내담자 정보."""
    application = case.application
    schedule = application.preferred_schedule or {}
    client_profile = _get_client_profile(case.client)

    birth_date = ""
    if client_profile and client_profile.birth_date:
        birth_date = client_profile.birth_date.strftime("%Y-%m-%d")
    elif schedule.get("birth_date"):
        birth_date = schedule["birth_date"]

    gender = ""
    if client_profile and client_profile.gender:
        gender = client_profile.gender
    elif schedule.get("gender"):
        gender = schedule["gender"]

    occupation = (schedule.get("occupation") or schedule.get("job") or "").strip()
    if not occupation and client_profile and client_profile.department:
        occupation = client_profile.department

    return {
        "client_name": case.client.name,
        "gender": gender or "—",
        "birth_date": birth_date or "—",
        "occupation": occupation or "—",
        "phone": case.client.phone or "—",
        "email": case.client.email or "—",
        "case_number": case.case_number,
    }


def _journal_client_summary(case, *, mask_private: bool = False) -> dict:
    """상담일지 PDF·동기 목록용 내담자 요약."""
    extended = _initial_record_client_summary(case)
    base = _case_client_summary(case)
    summary = {
        "client_name": extended["client_name"],
        "student_id": base.get("student_id") or "—",
        "gender": extended["gender"],
        "birth_date": extended["birth_date"],
        "occupation": extended["occupation"],
        "phone": extended["phone"],
        "email": extended["email"],
        "counseling_type": base.get("counseling_type", ""),
        "case_number": base.get("case_number", case.case_number),
    }
    if mask_private:
        return mask_client_summary_fields(summary)
    return summary


def user_can_download_cohort_journal(user, journal) -> bool:
    """동기 기수 상담일지 PDF — 같은 기수만."""
    if not user.is_authenticated or journal.is_draft:
        return False
    if user.is_superuser:
        return True
    if user.role != UserRole.COUNSELOR:
        return False
    cohort = get_counselor_cohort(user)
    journal_cohort = get_counselor_cohort(journal.counselor)
    return cohort is not None and cohort == journal_cohort


def _build_cohort_journal_entry(journal, *, requesting_counselor, own_case_id):
    case = journal.case
    mask_private = case.counselor_id != requesting_counselor.pk
    summary = _journal_client_summary(case, mask_private=mask_private)
    updated = journal.updated_at or journal.created_at
    if updated and timezone.is_aware(updated):
        updated = timezone.localtime(updated)
    return {
        "journal_id": journal.pk,
        "counselor_name": journal.counselor.name if journal.counselor_id else "—",
        "case_number": case.case_number,
        "client_name": summary["client_name"],
        "gender": summary["gender"],
        "birth_date": summary["birth_date"],
        "occupation": summary["occupation"],
        "phone": summary["phone"],
        "email": summary["email"],
        "student_id": summary.get("student_id", "—"),
        "updated_at": updated.strftime("%m-%d %H:%M") if updated else "—",
        "is_own_case": case.pk == own_case_id,
        "session_number": journal.session_number,
    }


def _get_case_initial_record(request, case_pk):
    case = _get_counselor_case(request, case_pk)
    try:
        record = case.initial_counseling_record
    except InitialCounselingRecord.DoesNotExist:
        record = None
    return case, record


def _session1_appointment_for_case(case):
    return (
        case.appointments.filter(session_number=1)
        .exclude(status__in=[AppointmentStatus.CANCELLED])
        .order_by("-created_at")
        .first()
    )


def _ensure_case_counselor_assigned(case):
    """내담자·상담사 매칭(담당 배정) 후 기록지 작성 허용."""
    if not case.counselor_id:
        raise PermissionDenied("담당 상담사가 배정된 후에 작성할 수 있습니다.")


def _ensure_initial_record_allowed(case):
    """1회기 초기상담 기록지 — 매칭 후 작성 허용."""
    _ensure_case_counselor_assigned(case)


def _ensure_termination_record_allowed(case):
    """마지막 회기 종결기록지 — 매칭 후 작성 허용."""
    _ensure_case_counselor_assigned(case)
    if not case.total_sessions or case.total_sessions < 1:
        raise PermissionDenied("설정된 회기 정보가 없어 종결기록지를 작성할 수 없습니다.")


def _render_initial_record_form(request, case, form, *, is_edit=False, record=None):
    context = {
        "case": case,
        "form": form,
        "client_summary": _initial_record_client_summary(case),
        "is_edit": is_edit,
        "record": record,
        "page_title": "초기상담 기록지 수정" if is_edit else "초기상담 기록지 작성",
        "breadcrumb_label": "초기상담 기록지",
        "submit_label": "저장하기",
    }
    can_download = _can_download_initial_record_pdf(request, record)
    context.update(
        _record_pdf_context(
            can_download,
            reverse("counselor:initial_record_pdf", kwargs={"pk": case.pk})
            if can_download
            else "",
        )
    )
    return render(request, "counselor/initial_record_form.html", context)


@counselor_required
def initial_record_create(request, pk):
    """1회기 초기상담 기록지 작성 (상담사 전용)."""
    case = _get_counselor_case(request, pk)
    _ensure_initial_record_allowed(case)

    if InitialCounselingRecord.objects.filter(case=case).exists():
        record = case.initial_counseling_record
        if record.is_draft:
            return redirect("counselor:initial_record_edit", pk=case.pk)
        return redirect("counselor:initial_record_detail", pk=case.pk)

    appointment = _session1_appointment_for_case(case)

    if request.method == "POST":
        form = InitialCounselingRecordForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            record.case = case
            record.counselor = request.user
            record.save()
            messages.success(request, "초기상담 기록지가 저장되었습니다.")
            return redirect("counselor:case_detail", pk=case.pk)
        messages.error(request, "입력 내용을 확인해 주세요.")
    else:
        initial = {}
        if appointment and appointment.scheduled_at:
            local_dt = timezone.localtime(appointment.scheduled_at)
            initial["session_start_datetime"] = local_dt.strftime("%Y-%m-%dT%H:%M")
        form = InitialCounselingRecordForm(initial=initial)

    return _render_initial_record_form(request, case, form, is_edit=False)


@counselor_required
def initial_record_detail(request, pk):
    """초기상담 기록지 상세 (상담사 전용)."""
    case, record = _get_case_initial_record(request, pk)
    if not record:
        return redirect("counselor:initial_record_create", pk=case.pk)
    if record.is_draft:
        return redirect("counselor:initial_record_edit", pk=case.pk)

    return render(
        request,
        "counselor/initial_record_detail.html",
        {
            "case": case,
            "record": record,
            "client_summary": _initial_record_client_summary(case),
            "can_edit": record.counselor_id == request.user.pk
            or case.counselor_id == request.user.pk,
            **_record_pdf_context(
                _can_download_initial_record_pdf(request, record),
                reverse("counselor:initial_record_pdf", kwargs={"pk": case.pk}),
            ),
        },
    )


@counselor_required
@require_POST
def initial_record_pdf(request, pk):
    """초기상담 기록지 PDF 다운로드 (사용자 입력 암호로 암호화)."""
    case, record = _get_case_initial_record(request, pk)
    fallback = reverse("counselor:initial_record_detail", kwargs={"pk": case.pk})
    if not record or record.is_draft:
        raise Http404("저장된 초기상담 기록지가 없습니다.")
    if not user_can_download_initial_record_pdf(request.user, record):
        raise PermissionDenied("PDF 다운로드 권한이 없습니다.")

    pdf_password, redirect_response = _get_pdf_password_from_request(
        request, redirect_url=fallback
    )
    if redirect_response:
        return redirect_response

    client_summary = _initial_record_client_summary(case)
    pdf_bytes = build_initial_record_pdf(
        record,
        client_summary=client_summary,
        user_password=pdf_password,
    )
    filename = initial_record_pdf_filename(record)
    ascii_name = f"initial_record_{case.case_number}.pdf".replace("/", "-")
    return _pdf_file_response(pdf_bytes, filename=filename, ascii_name=ascii_name)


@counselor_required
def initial_record_edit(request, pk):
    """초기상담 기록지 수정 (상담사 전용)."""
    case, record = _get_case_initial_record(request, pk)
    _ensure_initial_record_allowed(case)
    if not record:
        return redirect("counselor:initial_record_create", pk=case.pk)

    if request.method == "POST":
        form = InitialCounselingRecordForm(request.POST, instance=record)
        if form.is_valid():
            updated = form.save(commit=False)
            if not updated.counselor_id:
                updated.counselor = request.user
            updated.save()
            messages.success(request, "초기상담 기록지가 저장되었습니다.")
            return redirect("counselor:case_detail", pk=case.pk)
        messages.error(request, "입력 내용을 확인해 주세요.")
    else:
        form = InitialCounselingRecordForm(instance=record)

    return _render_initial_record_form(
        request, case, form, is_edit=True, record=record
    )


def _get_case_termination_record(request, case_pk):
    case = _get_counselor_case(request, case_pk)
    try:
        record = case.termination_counseling_record
    except TerminationCounselingRecord.DoesNotExist:
        record = None
    return case, record


def _default_termination_counseling_period(case) -> str:
    """종결기록지 — 확정·완료된 회기 일시 목록."""
    lines: list[str] = []
    for apt in case.appointments.filter(
        status__in=(AppointmentStatus.CONFIRMED, AppointmentStatus.COMPLETED),
    ).order_by("session_number", "scheduled_at"):
        label = (
            f"{apt.session_number}회기"
            if apt.session_number
            else apt.scheduled_at.strftime("%Y-%m-%d")
        )
        lines.append(f"{label}: {apt.scheduled_at:%Y-%m-%d %H:%M}")
    return "\n".join(lines)


def _termination_record_client_summary(case):
    return _initial_record_client_summary(case)


def _render_termination_record_form(request, case, form, *, is_edit=False, record=None):
    context = {
        "case": case,
        "form": form,
        "client_summary": _termination_record_client_summary(case),
        "is_edit": is_edit,
        "record": record,
        "page_title": "종결기록지 수정" if is_edit else "종결기록지 작성",
        "breadcrumb_label": "종결기록지",
        "submit_label": "저장하기",
        "total_sessions": case.total_sessions,
    }
    can_download = _can_download_termination_record_pdf(request, record)
    context.update(
        _record_pdf_context(
            can_download,
            reverse("counselor:termination_record_pdf", kwargs={"pk": case.pk})
            if can_download
            else "",
        )
    )
    return render(request, "counselor/termination_record_form.html", context)


@counselor_required
def termination_record_create(request, pk):
    """마지막 회기 종결기록지 작성 (상담사 전용)."""
    case = _get_counselor_case(request, pk)
    _ensure_termination_record_allowed(case)

    if TerminationCounselingRecord.objects.filter(case=case).exists():
        record = case.termination_counseling_record
        if record.is_draft:
            return redirect("counselor:termination_record_edit", pk=case.pk)
        return redirect("counselor:termination_record_detail", pk=case.pk)

    if request.method == "POST":
        form = TerminationCounselingRecordForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            record.case = case
            record.counselor = request.user
            record.save()
            messages.success(request, "종결기록지가 저장되었습니다.")
            return redirect("counselor:case_detail", pk=case.pk)
        messages.error(request, "입력 내용을 확인해 주세요.")
    else:
        form = TerminationCounselingRecordForm(
            initial={
                "counseling_period": _default_termination_counseling_period(case),
            }
        )

    return _render_termination_record_form(request, case, form, is_edit=False)


@counselor_required
def termination_record_detail(request, pk):
    """종결기록지 상세 (상담사 전용)."""
    case, record = _get_case_termination_record(request, pk)
    if not record:
        return redirect("counselor:termination_record_create", pk=case.pk)
    if record.is_draft:
        return redirect("counselor:termination_record_edit", pk=case.pk)

    return render(
        request,
        "counselor/termination_record_detail.html",
        {
            "case": case,
            "record": record,
            "client_summary": _termination_record_client_summary(case),
            "total_sessions": case.total_sessions,
            "can_edit": record.counselor_id == request.user.pk
            or case.counselor_id == request.user.pk,
            **_record_pdf_context(
                _can_download_termination_record_pdf(request, record),
                reverse("counselor:termination_record_pdf", kwargs={"pk": case.pk}),
            ),
        },
    )


@counselor_required
@require_POST
def termination_record_pdf(request, pk):
    """종결기록지 PDF 다운로드 (사용자 입력 암호로 암호화)."""
    case, record = _get_case_termination_record(request, pk)
    fallback = reverse("counselor:termination_record_detail", kwargs={"pk": case.pk})
    if not record or record.is_draft:
        raise Http404("저장된 종결기록지가 없습니다.")
    if not user_can_download_termination_record_pdf(request.user, record):
        raise PermissionDenied("PDF 다운로드 권한이 없습니다.")

    pdf_password, redirect_response = _get_pdf_password_from_request(
        request, redirect_url=fallback
    )
    if redirect_response:
        return redirect_response

    client_summary = _termination_record_client_summary(case)
    pdf_bytes = build_termination_record_pdf(
        record,
        client_summary=client_summary,
        user_password=pdf_password,
    )
    filename = termination_record_pdf_filename(record)
    ascii_name = f"termination_record_{case.case_number}.pdf".replace("/", "-")
    return _pdf_file_response(pdf_bytes, filename=filename, ascii_name=ascii_name)


@counselor_required
def termination_record_edit(request, pk):
    """종결기록지 수정 (상담사 전용)."""
    case, record = _get_case_termination_record(request, pk)
    _ensure_termination_record_allowed(case)
    if not record:
        return redirect("counselor:termination_record_create", pk=case.pk)

    if request.method == "POST":
        form = TerminationCounselingRecordForm(request.POST, instance=record)
        if form.is_valid():
            updated = form.save(commit=False)
            if not updated.counselor_id:
                updated.counselor = request.user
            updated.save()
            messages.success(request, "종결기록지가 저장되었습니다.")
            return redirect("counselor:case_detail", pk=case.pk)
        messages.error(request, "입력 내용을 확인해 주세요.")
    else:
        form = TerminationCounselingRecordForm(instance=record)

    return _render_termination_record_form(
        request, case, form, is_edit=True, record=record
    )
