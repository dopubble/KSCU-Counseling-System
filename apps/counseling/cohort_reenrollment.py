"""기존 상담사 계정에 더 높은 기수의 수련 등록을 추가한다.

사례, 일지, 출석, 게시글, 동의서, 승인 이력은 옮기거나 복사하지 않는다.
활동 이력이 없는 등록만 삭제할 수 있다.
"""

import json
import time

from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import connection, transaction
from django.db.models.deletion import ProtectedError
from django.db.utils import IntegrityError, OperationalError
from django.utils import timezone

from apps.accounts.models import CounselorProfile, User
from apps.counseling.models import (
    Case,
    CasePresentationComment,
    CasePresentationPost,
    CaseStatus,
    SupervisionAttendance,
    SupervisionAttendanceStatus,
    TrainingCertificateApproval,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.sessions_app.models import (
    CounselingJournal,
    InitialCounselingRecord,
    TerminationCounselingRecord,
)

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
MSG_CANCEL_GONE = "이미 취소되었거나 없는 등록입니다."
MSG_CANCEL_LINKED = "연결된 기록이 있어 등록을 취소할 수 없습니다."
MSG_CANCEL_BUSY = "등록 취소 요청이 겹쳤습니다. 다시 확인해 주세요."
MSG_CANCEL_ORIGINAL = (
    "최초로 등록한 기수는 취소할 수 없습니다. 추가로 등록한 기수만 취소할 수 있습니다."
)
MSG_CANCEL_NOT_ACTIVE = (
    "진행 중인 추가 등록만 취소할 수 있습니다. 종료되었거나 중단된 기수는 취소할 수 없습니다."
)


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


def enrollment_cancel_policy_blockers(enrollment) -> list[str]:
    """최초 등록과 진행 중이 아닌 등록은 취소하지 않는다."""
    reasons = []
    earliest_id = (
        TrainingEnrollment.objects.filter(counselor_id=enrollment.counselor_id)
        .order_by("cohort", "created_at", "pk")
        .values_list("pk", flat=True)
        .first()
    )
    if earliest_id is None or earliest_id == enrollment.pk:
        reasons.append(MSG_CANCEL_ORIGINAL)
    if enrollment.status != TrainingEnrollmentStatus.ACTIVE:
        reasons.append(MSG_CANCEL_NOT_ACTIVE)
    return reasons


def _activity_cancel_blockers(enrollment) -> list[str]:
    """이 등록을 지우면 사라지거나 끊길 기록, 그리고 같은 기수 실적을 모은다."""
    if enrollment.pk is None:
        return [MSG_CANCEL_LINKED]
    return _activity_blockers_for(
        enrollment.pk,
        enrollment.counselor_id,
        enrollment.cohort,
    )


def _activity_blockers_for(enrollment_id, counselor_id, cohort) -> list[str]:
    parts = []
    case_count = Case.objects.filter(training_enrollment_id=enrollment_id).count()
    if case_count:
        parts.append(f"상담사례 {case_count}건")
    appointment_count = Appointment.objects.filter(
        case__training_enrollment_id=enrollment_id
    ).count()
    if appointment_count:
        parts.append(f"예약 {appointment_count}건")
    journal_count = CounselingJournal.objects.filter(
        case__training_enrollment_id=enrollment_id
    ).count()
    if journal_count:
        parts.append(f"상담일지 {journal_count}건")
    initial_count = InitialCounselingRecord.objects.filter(
        case__training_enrollment_id=enrollment_id
    ).count()
    if initial_count:
        parts.append(f"초기상담 기록지 {initial_count}건")
    termination_count = TerminationCounselingRecord.objects.filter(
        case__training_enrollment_id=enrollment_id
    ).count()
    if termination_count:
        parts.append(f"종결기록지 {termination_count}건")
    attendance_count = SupervisionAttendance.objects.filter(
        counselor__user_id=counselor_id,
        session__cohort=cohort,
    ).exclude(
        status=SupervisionAttendanceStatus.UNCHECKED,
        checked_at__isnull=True,
    ).count()
    if attendance_count:
        parts.append(f"수퍼비전 출석 {attendance_count}건")
    presentation_count = (
        CasePresentationPost.objects.filter(
            author_id=counselor_id,
            cohort=cohort,
        ).count()
        + CasePresentationComment.objects.filter(
            author_id=counselor_id,
            post__cohort=cohort,
        ).count()
    )
    if presentation_count:
        parts.append(f"사례발표 {presentation_count}건")
    approval_count = TrainingCertificateApproval.objects.filter(
        training_enrollment_id=enrollment_id
    ).count()
    if approval_count:
        parts.append(f"수련확인증 승인 {approval_count}건")
    named_models = {Case, TrainingCertificateApproval}
    for relation in TrainingEnrollment._meta.related_objects:
        if relation.related_model in named_models:
            continue
        if relation.related_model._default_manager.filter(
            **{relation.field.attname: enrollment_id}
        ).exists():
            parts.append(str(relation.related_model._meta.verbose_name))
    if not parts:
        return []
    return [
        "이 기수에 연결된 기록이 있어 등록을 취소할 수 없습니다. ("
        + ", ".join(parts)
        + ")"
    ]


def enrollment_cancel_blockers(enrollment) -> list[str]:
    """정책 제한과 활동 이력을 함께 반환한다."""
    return [
        *enrollment_cancel_policy_blockers(enrollment),
        *_activity_cancel_blockers(enrollment),
    ]


def _wait_for_cancel_race(enrollment):
    """삭제 직전 지점. 검증 도중 생긴 기록을 다시 확인하기 전에 지난다."""
    return enrollment


def _log_enrollment_cancellation(enrollment, actor) -> None:
    if actor is None or not getattr(actor, "pk", None):
        return
    LogEntry.objects.log_action(
        user_id=actor.pk,
        content_type_id=ContentType.objects.get_for_model(TrainingEnrollment).pk,
        object_id=str(enrollment.pk),
        object_repr=f"{enrollment.cohort}기 수련 등록",
        action_flag=DELETION,
        change_message=json.dumps(
            {
                "action": "cancel_training_enrollment",
                "cohort": enrollment.cohort,
                "counselor_id": str(enrollment.counselor_id),
                "enrollment_id": str(enrollment.pk),
            },
            ensure_ascii=False,
        ),
    )


def _cancel_training_enrollment_once(enrollment, actor):
    with transaction.atomic():
        # 행을 잠그기 전에 다른 요청이 기록을 저장할 틈을 둔다.
        # 잠근 뒤의 확인에서 그 기록이 보이면 삭제하지 않는다.
        _wait_for_cancel_race(enrollment)
        queryset = TrainingEnrollment.objects.select_related("counselor")
        if connection.features.has_select_for_update:
            queryset = queryset.select_for_update()
        try:
            locked = queryset.get(pk=enrollment.pk)
        except TrainingEnrollment.DoesNotExist as exc:
            raise CohortReenrollmentError([MSG_CANCEL_GONE]) from exc
        blockers = enrollment_cancel_blockers(locked)
        if blockers:
            raise CohortReenrollmentError(blockers)
        _log_enrollment_cancellation(locked, actor)
        enrollment_id = locked.pk
        counselor_id = locked.counselor_id
        cohort = locked.cohort
        try:
            locked.delete()
        except ProtectedError as exc:
            raise CohortReenrollmentError([MSG_CANCEL_LINKED]) from exc
        if _activity_blockers_for(enrollment_id, counselor_id, cohort):
            raise CohortReenrollmentError([MSG_CANCEL_LINKED])
    return locked


def cancel_training_enrollment(enrollment, *, actor):
    """활동 이력이 없는 등록 한 건만 삭제한다. 다른 기수는 바꾸지 않는다."""
    for attempt in range(2):
        try:
            return _cancel_training_enrollment_once(enrollment, actor)
        except OperationalError:
            if attempt == 1:
                raise CohortReenrollmentError([MSG_CANCEL_BUSY])
            time.sleep(0.05)
