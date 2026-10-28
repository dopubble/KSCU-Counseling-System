"""수련확인서 출력. 상담사 본인과 관리자만 연다."""

from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.accounts.decorators import counselor_required, role_required
from apps.accounts.models import CounselorProfile, User, UserRole
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


@counselor_required
def counselor_training_certificate(request):
    """로그인한 상담사 본인의 확인서. 다른 상담사 ID는 받지 않는다."""
    if request.user.role != UserRole.COUNSELOR:
        raise Http404("상담사 프로필이 없습니다.")
    return training_certificate_response(_counselor_for_certificate(request.user.pk))


@role_required(UserRole.ADMIN)
def admin_counselor_training_certificate(request, counselor_id):
    return training_certificate_response(_counselor_for_certificate(counselor_id))
