"""수련확인서 출력. 상담사 본인·관리자·담당 수퍼바이저.

승인 hash가 현재 확인증과 같을 때만 PDF를 내려준다.
승인은 POST이며, 상담사는 자신을 승인할 수 없다.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.accounts.decorators import counselor_required, role_required, supervisor_required
from apps.accounts.models import CounselorProfile, User, UserRole
from apps.counseling.journal_permissions import user_can_browse_counselor_journals
from apps.counseling.training_certificate import (
    TrainingCertificateApprovalError,
    approve_training_certificate,
    get_training_certificate_approval_status,
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


def issued_training_certificate_response(counselor):
    """현재 context로 hash를 확인하고, 승인완료일 때만 그 context로 PDF를 만든다."""
    approval = get_training_certificate_approval_status(counselor)
    if not approval["can_issue"]:
        raise PermissionDenied("수련확인증을 출력할 수 없습니다.")
    return training_certificate_response(counselor, context=approval["context"])


@counselor_required
def counselor_training_certificate(request):
    """로그인한 상담사 본인의 확인서. 다른 상담사 ID는 받지 않는다."""
    if request.user.role != UserRole.COUNSELOR:
        raise Http404("상담사 프로필이 없습니다.")
    return issued_training_certificate_response(
        _counselor_for_certificate(request.user.pk)
    )


@role_required(UserRole.ADMIN)
def admin_counselor_training_certificate(request, counselor_id):
    return issued_training_certificate_response(_counselor_for_certificate(counselor_id))


@supervisor_required
def supervisor_counselor_training_certificate(request, counselor_id):
    """담당 기수 상담사 확인서. PDF 생성은 공통 training_certificate_response 재사용."""
    counselor = _counselor_for_certificate(counselor_id)
    if not user_can_browse_counselor_journals(request.user, counselor):
        raise PermissionDenied("담당 기수 상담사만 출력할 수 있습니다.")
    return issued_training_certificate_response(counselor)


def _approve_and_redirect(request, counselor, detail_name):
    try:
        approve_training_certificate(counselor, request.user)
    except TrainingCertificateApprovalError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "수련확인증을 승인했습니다.")
    return redirect(detail_name, counselor_id=counselor.pk)


@role_required(UserRole.ADMIN)
@require_POST
def admin_approve_training_certificate(request, counselor_id):
    counselor = _counselor_for_certificate(counselor_id)
    return _approve_and_redirect(
        request,
        counselor,
        "admin_panel:counselor_training_progress_detail",
    )


@supervisor_required
@require_POST
def supervisor_approve_training_certificate(request, counselor_id):
    counselor = _counselor_for_certificate(counselor_id)
    if not user_can_browse_counselor_journals(request.user, counselor):
        raise PermissionDenied("담당 기수 상담사만 승인할 수 있습니다.")
    return _approve_and_redirect(
        request,
        counselor,
        "supervisor:counselor_training_progress_detail",
    )
