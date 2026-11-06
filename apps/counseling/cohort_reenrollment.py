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

MSG_OPEN_CASES = (
    "진행 중인 상담사례가 있어 새 기수에 등록할 수 없습니다. 기존 사례를 먼저 정리해 주세요."
)
MSG_UNSUBMITTED = "최종 제출되지 않은 상담기록이 있어 새 기수에 등록할 수 없습니다."
MSG_UPCOMING = "기존 기수의 예정된 상담 예약이 있어 새 기수에 등록할 수 없습니다."
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


def _active_enrollment(enrollments):
    active = [
        item for item in enrollments if item.status == TrainingEnrollmentStatus.ACTIVE
    ]
    if len(active) > 1:
        raise CohortReenrollmentError([MSG_CONCURRENT])
    return active[0] if active else None


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


def readiness_blockers(readiness):
    messages = []
    if readiness["open_count"]:
        messages.append(f"{MSG_OPEN_CASES} ({readiness['open_count']}건)")
    if readiness["unsubmitted_count"]:
        messages.append(f"{MSG_UNSUBMITTED} ({readiness['unsubmitted_count']}건)")
    if readiness["upcoming_count"]:
        messages.append(f"{MSG_UPCOMING} ({readiness['upcoming_count']}건)")
    return messages


def describe_cohort_reenrollment(counselor):
    """확인 화면에 보여줄 현재 등록과 차단 사유. 데이터를 바꾸지 않는다."""
    enrollments = list(
        TrainingEnrollment.objects.filter(counselor=counselor).order_by("-cohort", "-pk")
    )
    active = _active_enrollment(enrollments) if enrollments else None
    highest = enrollments[0] if enrollments else None
    readiness = _case_readiness(active)
    return {
        "enrollments": enrollments,
        "active": active,
        "highest": highest,
        "max_cohort": highest.cohort if highest else None,
        "suggested_cohort": (highest.cohort + 1) if highest else None,
        "readiness": readiness,
        "blockers": readiness_blockers(readiness) if active is not None else [],
        "eligible": bool(enrollments),
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
    """ACTIVE가 있으면 완료로 닫고, 더 높은 기수의 ACTIVE 등록을 만든다."""
    cohort = parse_new_cohort(raw_cohort)
    try:
        with transaction.atomic():
            locked, profile = _lock_counselor(counselor.pk)
            enrollments = list(
                TrainingEnrollment.objects.filter(counselor=locked).order_by(
                    "-cohort", "-pk"
                )
            )
            if not enrollments:
                raise CohortReenrollmentError([MSG_NO_HISTORY])
            if profile is None:
                raise CohortReenrollmentError([MSG_NO_PROFILE])
            _validate_new_cohort(enrollments, cohort)
            active = _active_enrollment(enrollments)
            if active is not None:
                blockers = readiness_blockers(_case_readiness(active))
                if blockers:
                    raise CohortReenrollmentError(blockers)
                now = timezone.now()
                active.status = TrainingEnrollmentStatus.COMPLETED
                active.completed_at = now
                active.save(update_fields=["status", "completed_at", "updated_at"])
            else:
                now = timezone.now()
            created = TrainingEnrollment.objects.create(
                counselor=locked,
                cohort=cohort,
                status=TrainingEnrollmentStatus.ACTIVE,
                started_at=now,
                completed_at=None,
            )
            profile.cohort = cohort
            profile.save(update_fields=["cohort", "updated_at"])
            return created
    except IntegrityError as exc:
        raise CohortReenrollmentError([MSG_CONCURRENT]) from exc
