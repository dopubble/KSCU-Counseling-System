"""상담사의 현재 수련 단위. 실적 귀속은 profile.cohort가 아니라 Enrollment다."""

import uuid

from apps.counseling.models import TrainingEnrollment, TrainingEnrollmentStatus

ACTIVE_ENROLLMENT_MISSING = "해당 상담사의 활성 수련 기수가 없습니다."
ACTIVE_ENROLLMENT_AMBIGUOUS = "해당 상담사의 활성 수련 기수가 둘 이상입니다."


class ActiveTrainingEnrollmentError(ValueError):
    """활성 수련이 없거나 둘 이상이라 하나를 고를 수 없을 때."""


def get_active_training_enrollment(counselor):
    """ACTIVE Enrollment가 하나면 그 행을 반환한다.

    없으면 None이다. 둘 이상이면 하나를 고르지 않고 오류를 낸다.
    """
    if counselor is None or not getattr(counselor, "pk", None):
        return None
    matches = list(
        TrainingEnrollment.objects.filter(
            counselor_id=counselor.pk,
            status=TrainingEnrollmentStatus.ACTIVE,
        ).order_by("pk")[:2]
    )
    if not matches:
        return None
    if len(matches) > 1:
        raise ActiveTrainingEnrollmentError(ACTIVE_ENROLLMENT_AMBIGUOUS)
    return matches[0]


def require_active_training_enrollment(counselor):
    enrollment = get_active_training_enrollment(counselor)
    if enrollment is None:
        raise ActiveTrainingEnrollmentError(ACTIVE_ENROLLMENT_MISSING)
    return enrollment


def counselor_training_enrollments(counselor):
    if counselor is None or not getattr(counselor, "pk", None):
        return []
    return list(
        TrainingEnrollment.objects.filter(counselor_id=counselor.pk).order_by(
            "-cohort", "-created_at", "-pk"
        )
    )


def resolve_dashboard_training_enrollment(counselor, raw):
    """대시보드에 표시할 수련 단위.

    반환값의 두 번째 값이 True이면 요청한 enrollment를 이 상담사가 볼 수 없다.
    """
    enrollments = counselor_training_enrollments(counselor)
    raw = (raw or "").strip()
    if raw:
        try:
            enrollment_id = uuid.UUID(raw)
        except ValueError:
            return None, enrollments, True
        selected = next((item for item in enrollments if item.pk == enrollment_id), None)
        if selected is None:
            return None, enrollments, True
        return selected, enrollments, False
    active = [
        item for item in enrollments if item.status == TrainingEnrollmentStatus.ACTIVE
    ]
    if len(active) == 1:
        return active[0], enrollments, False
    if len(active) > 1:
        return None, enrollments, True
    if enrollments:
        return enrollments[0], enrollments, False
    return None, enrollments, False
