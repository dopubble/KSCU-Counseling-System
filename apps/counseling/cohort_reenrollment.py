"""기존 상담사 계정에 더 높은 기수의 수련 등록을 추가한다.

사례, 일지, 출석, 게시글, 동의서, 승인 이력은 옮기거나 복사하지 않는다.
"""

from django.db import connection, transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User
from apps.counseling.models import (
    Case,
    CaseStatus,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.scheduling.models import Appointment, AppointmentStatus

OPEN_CASE_STATUSES = (CaseStatus.ACTIVE, CaseStatus.ON_HOLD)
UPCOMING_APPOINTMENT_STATUSES = (
    AppointmentStatus.PENDING,
    AppointmentStatus.SCHEDULED,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.CANCEL_PENDING,
)

MAX_ACTIVE_ENROLLMENTS = 2
MSG_ACTIVE_LIMIT = (
    "이미 두 기수에 참여 중이라 새 기수를 등록할 수 없습니다. "
    "한 기수를 종료한 뒤 등록해 주세요."
)
MSG_NOT_ACTIVE = "진행 중인 기수만 종료할 수 있습니다."
MSG_NO_HISTORY = "수련 등록 이력이 없는 상담사는 이 화면에서 등록할 수 없습니다."
MSG_NO_PROFILE = "상담사 프로필이 없어 새 기수에 등록할 수 없습니다."
MSG_COHORT_NUMBER = "신규 기수는 1 이상의 정수여야 합니다."
MSG_COHORT_HIGHER = "신규 기수는 기존에 등록된 가장 높은 기수보다 높아야 합니다."
MSG_COHORT_DUPLICATE = "이미 등록된 기수입니다."
MSG_CONCURRENT = "이미 다른 요청으로 이 상담사의 기수가 등록되었습니다."


class CohortReenrollmentError(Exception):
    def __init__(self, messages):
        self.messages = list(messages)
        super().__init__(" ".join(self.messages))


def parse_new_cohort(raw):
    text = str(raw or "").strip()
    if not text or not text.isdigit():
        return None
    value = int(text)
    if value < 1:
        return None
    return value


def _case_readiness(enrollment):
    if enrollment is None:
        return {
            "case_count": 0,
            "open_count": 0,
            "unsubmitted_count": 0,
            "upcoming_count": 0,
        }
    cases = Case.objects.filter(training_enrollment=enrollment)
    upcoming = Appointment.objects.filter(
        case__training_enrollment=enrollment,
        scheduled_at__gte=timezone.now(),
        status__in=UPCOMING_APPOINTMENT_STATUSES,
    )
    return {
        "case_count": cases.count(),
        "open_count": cases.filter(status__in=OPEN_CASE_STATUSES).count(),
        "unsubmitted_count": cases.filter(records_submitted_at__isnull=True).count(),
        "upcoming_count": upcoming.count(),
    }


def _active_enrollments(enrollments):
    return [item for item in enrollments if item.status == TrainingEnrollmentStatus.ACTIVE]


def describe_cohort_reenrollment(counselor):
    """확인 화면에 보여줄 현재 등록. 데이터를 바꾸지 않는다."""
    enrollments = list(
        TrainingEnrollment.objects.filter(counselor=counselor).order_by("cohort", "pk")
    )
    active = _active_enrollments(enrollments)
    highest = max(enrollments, key=lambda item: (item.cohort, item.pk)) if enrollments else None
    blockers = [MSG_ACTIVE_LIMIT] if len(active) >= MAX_ACTIVE_ENROLLMENTS else []
    return {
        "enrollments": enrollments,
        "active_enrollments": active,
        "highest": highest,
        "max_cohort": highest.cohort if highest else None,
        "suggested_cohort": (highest.cohort + 1) if highest else None,
        "readiness_rows": [
            {"enrollment": item, "readiness": _case_readiness(item)} for item in active
        ],
        "blockers": blockers,
        "eligible": bool(enrollments) and not blockers,
    }


def _lock_counselor(counselor_id):
    queryset = User.objects.filter(pk=counselor_id)
    if connection.features.has_select_for_update:
        queryset = queryset.select_for_update()
    counselor = queryset.get()
    profile_qs = CounselorProfile.objects.filter(user_id=counselor.pk)
    if connection.features.has_select_for_update:
        profile_qs = profile_qs.select_for_update()
    return counselor, profile_qs.first()


def _validate_new_cohort(enrollments, cohort):
    if cohort is None:
        raise CohortReenrollmentError([MSG_COHORT_NUMBER])
    existing = {item.cohort for item in enrollments}
    if cohort in existing:
        raise CohortReenrollmentError([MSG_COHORT_DUPLICATE])
    highest = max(existing) if existing else None
    if highest is not None and cohort <= highest:
        raise CohortReenrollmentError([MSG_COHORT_HIGHER])


def register_next_cohort(counselor, raw_cohort):
    """더 높은 기수의 ACTIVE 등록을 추가한다. 기존 기수와 기록은 바꾸지 않는다."""
    cohort = parse_new_cohort(raw_cohort)
    try:
        with transaction.atomic():
            locked, profile = _lock_counselor(counselor.pk)
            enrollments = list(
                TrainingEnrollment.objects.filter(counselor=locked).order_by("cohort", "pk")
            )
            if not enrollments:
                raise CohortReenrollmentError([MSG_NO_HISTORY])
            if profile is None:
                raise CohortReenrollmentError([MSG_NO_PROFILE])
            _validate_new_cohort(enrollments, cohort)
            if len(_active_enrollments(enrollments)) >= MAX_ACTIVE_ENROLLMENTS:
                raise CohortReenrollmentError([MSG_ACTIVE_LIMIT])
            return TrainingEnrollment.objects.create(
                counselor=locked,
                cohort=cohort,
                status=TrainingEnrollmentStatus.ACTIVE,
                started_at=timezone.now(),
                completed_at=None,
            )
    except IntegrityError as exc:
        raise CohortReenrollmentError([MSG_CONCURRENT]) from exc


def complete_training_enrollment(enrollment):
    """한 기수만 종료한다. 다른 기수, 사례, 상담기록은 바꾸지 않는다."""
    with transaction.atomic():
        locked = (
            TrainingEnrollment.objects.select_for_update()
            .select_related("counselor")
            .get(pk=enrollment.pk)
        )
        if locked.status != TrainingEnrollmentStatus.ACTIVE:
            raise CohortReenrollmentError([MSG_NOT_ACTIVE])
        locked.status = TrainingEnrollmentStatus.COMPLETED
        locked.completed_at = timezone.now()
        locked.save(update_fields=["status", "completed_at", "updated_at"])
        enrollment.status = locked.status
        enrollment.completed_at = locked.completed_at
        enrollment.active_slot = locked.active_slot
        return locked
