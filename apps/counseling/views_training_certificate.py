"""수련확인서 출력. 상담사 본인·관리자·담당 수퍼바이저.

승인 hash가 현재 확인증과 같을 때만 PDF를 내려준다.
승인은 POST이며, 상담사는 자신을 승인할 수 없다.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.accounts.decorators import counselor_required, role_required, supervisor_required
from apps.accounts.models import CounselorProfile, User, UserRole
from apps.counseling.journal_permissions import supervisor_can_access_cohort
from apps.counseling.models import TrainingEnrollment
from apps.counseling.training_enrollment import (
    resolve_dashboard_training_enrollment,
    resolve_legacy_counselor_training_enrollment,
)
from apps.counseling.training_certificate import (
    TrainingCertificateApprovalError,
    TrainingCertificateRevocationError,
    approve_training_certificate,
    get_training_certificate_approval_status,
    revoke_training_certificate,
)
from apps.counseling.training_certificate_pdf import training_certificate_response


def _counselor_for_certificate(user_id):
    counselor = get_object_or_404(
        User.objects.select_related("counselor_profile"),
        pk=user_id,
        role=UserRole.COUNSELOR,
    )
    try:
        counselor.counselor_profile
    except CounselorProfile.DoesNotExist:
        raise Http404("상담사 프로필이 없습니다.") from None
    return counselor


def issued_training_certificate_response(counselor, training_enrollment=None):
    """현재 context로 hash를 확인하고, 승인완료일 때만 그 context로 PDF를 만든다."""
    approval = get_training_certificate_approval_status(
        counselor, training_enrollment=training_enrollment
    )
    if not approval["can_issue"]:
        raise PermissionDenied("수련확인증을 출력할 수 없습니다.")
    return training_certificate_response(counselor, context=approval["context"])


def _legacy_certificate_enrollment(counselor):
    enrollment = resolve_legacy_counselor_training_enrollment(counselor)
    if enrollment is None and TrainingEnrollment.objects.filter(counselor=counselor).exists():
        raise Http404("수련 기수를 찾을 수 없습니다.")
    return enrollment


def _single_pdf_enrollment(counselor):
    """기존 counselor_id PDF는 Enrollment가 정확히 하나일 때만 그 기수다.

    여러 Enrollment가 있으면 ACTIVE가 하나여도 어느 문서인지 알 수 없으므로 고르지 않는다.
    """
    rows = list(TrainingEnrollment.objects.filter(counselor=counselor).order_by("pk")[:2])
    if len(rows) > 1:
        raise Http404("수련 기수를 찾을 수 없습니다.")
    if len(rows) == 1:
        return rows[0]
    return None


def _assert_supervisor_enrollment(user, enrollment, message):
    if enrollment is None or not supervisor_can_access_cohort(user, enrollment.cohort):
        raise PermissionDenied(message)


@counselor_required
def counselor_training_certificate(request):
    """로그인한 상담사 본인의 확인서. 다른 상담사 Enrollment는 받지 않는다."""
    if request.user.role != UserRole.COUNSELOR:
        raise Http404("상담사 프로필이 없습니다.")
    counselor = _counselor_for_certificate(request.user.pk)
    raw = request.GET.get("enrollment")
    if raw:
        selected, _, forbidden = resolve_dashboard_training_enrollment(counselor, raw)
        if forbidden or selected is None:
            raise Http404("수련 기수를 찾을 수 없습니다.")
        enrollment = selected
    else:
        enrollment = _single_pdf_enrollment(counselor)
    return issued_training_certificate_response(counselor, enrollment)


@role_required(UserRole.ADMIN)
def admin_counselor_training_certificate(request, counselor_id):
    counselor = _counselor_for_certificate(counselor_id)
    return issued_training_certificate_response(
        counselor, _single_pdf_enrollment(counselor)
    )


@role_required(UserRole.ADMIN)
def admin_enrollment_training_certificate(request, enrollment_id):
    enrollment = get_object_or_404(
        TrainingEnrollment.objects.select_related("counselor", "counselor__counselor_profile"),
        pk=enrollment_id,
        counselor__role=UserRole.COUNSELOR,
    )
    return issued_training_certificate_response(enrollment.counselor, enrollment)


@supervisor_required
def supervisor_counselor_training_certificate(request, counselor_id):
    """담당 기수 상담사 확인서. PDF 생성은 공통 training_certificate_response 재사용."""
    counselor = _counselor_for_certificate(counselor_id)
    enrollment = _single_pdf_enrollment(counselor)
    _assert_supervisor_enrollment(
        request.user, enrollment, "담당 기수 상담사만 출력할 수 있습니다."
    )
    return issued_training_certificate_response(counselor, enrollment)


@supervisor_required
def supervisor_enrollment_training_certificate(request, enrollment_id):
    enrollment = get_object_or_404(
        TrainingEnrollment.objects.select_related("counselor", "counselor__counselor_profile"),
        pk=enrollment_id,
        counselor__role=UserRole.COUNSELOR,
    )
    _assert_supervisor_enrollment(
        request.user, enrollment, "담당 기수 상담사만 출력할 수 있습니다."
    )
    return issued_training_certificate_response(enrollment.counselor, enrollment)


def _progress_redirect(counselor, enrollment, *, legacy_name, enrollment_name):
    if enrollment is not None:
        return redirect(enrollment_name, enrollment_id=enrollment.pk)
    return redirect(legacy_name, counselor_id=counselor.pk)


def _approve_and_redirect(request, counselor, *, legacy_name, enrollment_name, training_enrollment=None):
    enrollment = training_enrollment or _legacy_certificate_enrollment(counselor)
    try:
        approve_training_certificate(
            counselor, request.user, training_enrollment=enrollment
        )
    except TrainingCertificateApprovalError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "수련확인증을 승인했습니다.")
    return _progress_redirect(
        counselor,
        enrollment,
        legacy_name=legacy_name,
        enrollment_name=enrollment_name,
    )


@role_required(UserRole.ADMIN)
@require_POST
def admin_approve_training_certificate(request, counselor_id):
    counselor = _counselor_for_certificate(counselor_id)
    return _approve_and_redirect(
        request,
        counselor,
        legacy_name="admin_panel:counselor_training_progress_detail",
        enrollment_name="admin_panel:enrollment_training_progress_detail",
    )


@role_required(UserRole.ADMIN)
@require_POST
def admin_approve_enrollment_training_certificate(request, enrollment_id):
    enrollment = get_object_or_404(
        TrainingEnrollment.objects.select_related("counselor"),
        pk=enrollment_id,
        counselor__role=UserRole.COUNSELOR,
    )
    return _approve_and_redirect(
        request,
        enrollment.counselor,
        legacy_name="admin_panel:counselor_training_progress_detail",
        enrollment_name="admin_panel:enrollment_training_progress_detail",
        training_enrollment=enrollment,
    )


@supervisor_required
@require_POST
def supervisor_approve_training_certificate(request, counselor_id):
    counselor = _counselor_for_certificate(counselor_id)
    enrollment = _legacy_certificate_enrollment(counselor)
    _assert_supervisor_enrollment(
        request.user, enrollment, "담당 기수 상담사만 승인할 수 있습니다."
    )
    return _approve_and_redirect(
        request,
        counselor,
        legacy_name="supervisor:counselor_training_progress_detail",
        enrollment_name="supervisor:enrollment_training_progress_detail",
        training_enrollment=enrollment,
    )


@supervisor_required
@require_POST
def supervisor_approve_enrollment_training_certificate(request, enrollment_id):
    enrollment = get_object_or_404(
        TrainingEnrollment.objects.select_related("counselor"),
        pk=enrollment_id,
        counselor__role=UserRole.COUNSELOR,
    )
    _assert_supervisor_enrollment(
        request.user, enrollment, "담당 기수 상담사만 승인할 수 있습니다."
    )
    return _approve_and_redirect(
        request,
        enrollment.counselor,
        legacy_name="supervisor:counselor_training_progress_detail",
        enrollment_name="supervisor:enrollment_training_progress_detail",
        training_enrollment=enrollment,
    )


def _revoke_and_redirect(request, counselor, *, legacy_name, enrollment_name, training_enrollment=None):
    enrollment = training_enrollment or _legacy_certificate_enrollment(counselor)
    try:
        revoke_training_certificate(
            counselor,
            request.user,
            request.POST.get("reason", ""),
            training_enrollment=enrollment,
        )
    except TrainingCertificateRevocationError as exc:
        return HttpResponseBadRequest(exc.message)
    messages.success(request, "수련확인증 승인을 취소했습니다.")
    return _progress_redirect(
        counselor,
        enrollment,
        legacy_name=legacy_name,
        enrollment_name=enrollment_name,
    )


@role_required(UserRole.ADMIN)
@require_POST
def admin_revoke_training_certificate(request, counselor_id):
    counselor = _counselor_for_certificate(counselor_id)
    return _revoke_and_redirect(
        request,
        counselor,
        legacy_name="admin_panel:counselor_training_progress_detail",
        enrollment_name="admin_panel:enrollment_training_progress_detail",
    )


@role_required(UserRole.ADMIN)
@require_POST
def admin_revoke_enrollment_training_certificate(request, enrollment_id):
    enrollment = get_object_or_404(
        TrainingEnrollment.objects.select_related("counselor"),
        pk=enrollment_id,
        counselor__role=UserRole.COUNSELOR,
    )
    return _revoke_and_redirect(
        request,
        enrollment.counselor,
        legacy_name="admin_panel:counselor_training_progress_detail",
        enrollment_name="admin_panel:enrollment_training_progress_detail",
        training_enrollment=enrollment,
    )


@supervisor_required
@require_POST
def supervisor_revoke_training_certificate(request, counselor_id):
    counselor = _counselor_for_certificate(counselor_id)
    enrollment = _legacy_certificate_enrollment(counselor)
    _assert_supervisor_enrollment(
        request.user, enrollment, "담당 기수 상담사만 승인 취소할 수 있습니다."
    )
    return _revoke_and_redirect(
        request,
        counselor,
        legacy_name="supervisor:counselor_training_progress_detail",
        enrollment_name="supervisor:enrollment_training_progress_detail",
        training_enrollment=enrollment,
    )


@supervisor_required
@require_POST
def supervisor_revoke_enrollment_training_certificate(request, enrollment_id):
    enrollment = get_object_or_404(
        TrainingEnrollment.objects.select_related("counselor"),
        pk=enrollment_id,
        counselor__role=UserRole.COUNSELOR,
    )
    _assert_supervisor_enrollment(
        request.user, enrollment, "담당 기수 상담사만 승인 취소할 수 있습니다."
    )
    return _revoke_and_redirect(
        request,
        enrollment.counselor,
        legacy_name="supervisor:counselor_training_progress_detail",
        enrollment_name="supervisor:enrollment_training_progress_detail",
        training_enrollment=enrollment,
    )
