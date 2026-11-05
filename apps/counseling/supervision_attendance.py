"""수퍼비전 회차·출석 조회와 저장."""

from django import forms
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.accounts.models import CounselorProfile
from apps.counseling.journal_permissions import (
    supervisor_can_access_cohort,
    user_is_platform_admin,
)
from apps.counseling.models import (
    SupervisionAttendance,
    SupervisionAttendanceStatus,
    SupervisionSession,
    SupervisionSessionStatus,
    TrainingEnrollment,
    TrainingEnrollmentStatus,
)
from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES


class SupervisionSessionForm(forms.ModelForm):
    scheduled_at = forms.DateTimeField(
        label="일시",
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(
            attrs={"class": "form-control", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
    )

    class Meta:
        model = SupervisionSession
        fields = [
            "cohort",
            "session_number",
            "title",
            "scheduled_at",
            "duration_minutes",
            "counseling_method",
            "status",
        ]
        widgets = {
            "cohort": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "session_number": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "duration_minutes": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "counseling_method": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "counseling_method": "진행 방식",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["counseling_method"].required = True
        if not self.instance.pk:
            self.fields["duration_minutes"].initial = DEFAULT_APPOINTMENT_DURATION_MINUTES
            self.fields["status"].initial = SupervisionSessionStatus.SCHEDULED
        elif self.instance.scheduled_at:
            self.initial["scheduled_at"] = timezone.localtime(
                self.instance.scheduled_at
            ).strftime("%Y-%m-%dT%H:%M")

    def clean_cohort(self):
        cohort = self.cleaned_data["cohort"]
        if cohort < 1:
            raise forms.ValidationError("기수는 1 이상이어야 합니다.")
        if (
            self.instance.pk
            and self.instance.cohort != cohort
            and SupervisionAttendance.objects.filter(session_id=self.instance.pk).exists()
        ):
            raise forms.ValidationError(
                "출석 기록이 있는 회차의 기수는 바꿀 수 없습니다."
            )
        return cohort

    def clean_session_number(self):
        number = self.cleaned_data["session_number"]
        if number < 1:
            raise forms.ValidationError("회차는 1 이상이어야 합니다.")
        return number

    def clean_duration_minutes(self):
        minutes = self.cleaned_data["duration_minutes"]
        if minutes < 1:
            raise forms.ValidationError("인정시간은 1분 이상이어야 합니다.")
        return minutes


def user_can_edit_supervision_sessions(user) -> bool:
    return user_is_platform_admin(user)


def session_has_recorded_attendance(session: SupervisionSession) -> bool:
    """출석 또는 결석이 저장됐거나, 확인 시각이 있으면 회차를 지울 수 없다."""
    return session.attendances.exclude(
        status=SupervisionAttendanceStatus.UNCHECKED,
        checked_at__isnull=True,
    ).exists()


def attendance_cohorts_for_user(user) -> list[int]:
    """관리자는 회차·상담사 기수를 모두, 수퍼바이저는 담당 기수만."""
    if user_is_platform_admin(user):
        profile_cohorts = CounselorProfile.objects.exclude(cohort=None).values_list(
            "cohort", flat=True
        )
        session_cohorts = SupervisionSession.objects.values_list("cohort", flat=True)
        return sorted({int(value) for value in (*profile_cohorts, *session_cohorts)})
    from apps.counseling.journal_permissions import get_supervisor_assigned_cohorts

    return get_supervisor_assigned_cohorts(user)


def assert_cohort_access(user, cohort: int) -> None:
    from django.core.exceptions import PermissionDenied

    if not supervisor_can_access_cohort(user, cohort):
        raise PermissionDenied("이 기수의 수퍼비전 출석을 관리할 수 없습니다.")


def _new_attendance_profiles(cohort: int):
    """이 기수에 새로 넣을 상담사.

    진행 중인 Enrollment가 있으면 그 기수다.
    Enrollment가 하나도 없는 상담사만 프로필 기수를 쓴다.
    완료·중단 Enrollment만 있는 상담사는 새 명단에 넣지 않는다.
    """
    active_users = TrainingEnrollment.objects.filter(
        cohort=cohort,
        status=TrainingEnrollmentStatus.ACTIVE,
    ).values("counselor_id")
    has_enrollment = TrainingEnrollment.objects.filter(counselor_id=OuterRef("user_id"))
    return CounselorProfile.objects.annotate(has_enrollment=Exists(has_enrollment)).filter(
        Q(user_id__in=active_users) | Q(has_enrollment=False, cohort=cohort)
    )


def _roster_profiles(session: SupervisionSession):
    """기존 출석 행과 새 대상자를 합친다. 기존 행은 현재 기수와 상관없이 남긴다."""
    attendances = list(
        SupervisionAttendance.objects.filter(session=session).select_related(
            "counselor",
            "counselor__user",
        )
    )
    attendance_by_profile = {row.counselor_id: row for row in attendances}
    profiles = {
        profile.pk: profile
        for profile in _new_attendance_profiles(session.cohort).select_related("user")
    }
    for row in attendances:
        profiles.setdefault(row.counselor_id, row.counselor)
    ordered = sorted(profiles.values(), key=lambda profile: (profile.user.name, profile.user.email))
    return ordered, attendance_by_profile


def session_roster(session: SupervisionSession) -> list[dict]:
    """회차 명단과 기존 출석 상태를 한 번에 붙인다. 조회만 하고 행을 만들지 않는다."""
    profiles, attendance_by_profile = _roster_profiles(session)
    roster = []
    for profile in profiles:
        attendance = attendance_by_profile.get(profile.pk)
        roster.append(
            {
                "profile": profile,
                "name": profile.user.name,
                "status": (
                    attendance.status
                    if attendance
                    else SupervisionAttendanceStatus.UNCHECKED
                ),
            }
        )
    return roster


def save_session_attendance(session: SupervisionSession, user, posted) -> int:
    """화면에 나온 명단만 저장한다. 체크된 상담사는 출석, 나머지는 결석이다.

    명단에 없는 상담사의 기존 행은 지우거나 다른 회차로 옮기지 않는다.
    """
    profiles, _attendance_by_profile = _roster_profiles(session)
    saved = 0
    now = timezone.now()
    with transaction.atomic():
        for profile in profiles:
            present = f"attendance_{profile.pk}" in posted
            SupervisionAttendance.objects.update_or_create(
                session=session,
                counselor=profile,
                defaults={
                    "status": (
                        SupervisionAttendanceStatus.PRESENT
                        if present
                        else SupervisionAttendanceStatus.ABSENT
                    ),
                    "checked_by": user,
                    "checked_at": now,
                },
            )
            saved += 1
    return saved
