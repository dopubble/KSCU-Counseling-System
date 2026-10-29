"""수퍼비전 회차·출석 조회와 저장."""

from django import forms
from django.db import transaction
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
            "status",
        ]
        widgets = {
            "cohort": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "session_number": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "duration_minutes": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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


def session_roster(session: SupervisionSession) -> list[dict]:
    """기수 상담사와 기존 출석 상태를 한 번에 붙인다."""
    profiles = list(
        CounselorProfile.objects.filter(cohort=session.cohort)
        .select_related("user")
        .order_by("user__name", "user__email")
    )
    attendance_by_profile = {
        row.counselor_id: row
        for row in SupervisionAttendance.objects.filter(session=session)
    }
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
    """기수 상담사 전원을 저장한다. 체크된 상담사는 출석, 나머지는 결석이다."""
    profiles = list(CounselorProfile.objects.filter(cohort=session.cohort))
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
