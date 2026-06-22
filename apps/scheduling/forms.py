from django import forms
from django.utils import timezone

from .constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from .models import Appointment

WEEKDAY_CHOICES = (
    (0, "월요일"),
    (1, "화요일"),
    (2, "수요일"),
    (3, "목요일"),
    (4, "금요일"),
    (5, "토요일"),
    (6, "일요일"),
)

SETTING_RECURRING = "recurring"
SETTING_DAILY = "daily"
SETTING_SPECIFIC = "specific"

WEEKDAY_DAILY_DAYS = (0, 1, 2, 3, 4)  # 월~금


class CounselorAvailabilityForm(forms.Form):
    """상담사 가용·차단 시간 등록."""

    setting_type = forms.ChoiceField(
        label="설정 구분",
        choices=(
            (SETTING_RECURRING, "매주 반복"),
            (SETTING_DAILY, "매일"),
            (SETTING_SPECIFIC, "특정 날짜"),
        ),
        initial=SETTING_RECURRING,
        widget=forms.RadioSelect(
            attrs={"class": "counselor-availability-setting-radio notranslate"},
        ),
    )
    day_of_week = forms.TypedChoiceField(
        label="요일",
        choices=WEEKDAY_CHOICES,
        coerce=int,
        required=False,
        widget=forms.Select(attrs={"class": "form-select notranslate", "translate": "no"}),
    )
    specific_date = forms.DateField(
        label="특정 날짜",
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
        ),
    )
    start_time = forms.TimeField(
        label="시작 시간",
        widget=forms.TimeInput(
            attrs={"class": "form-control", "type": "time", "step": "60"},
            format="%H:%M",
        ),
    )
    end_time = forms.TimeField(
        label="종료 시간",
        widget=forms.TimeInput(
            attrs={"class": "form-control", "type": "time", "step": "60"},
            format="%H:%M",
        ),
    )
    is_available = forms.ChoiceField(
        label="해당 시간대 상담 가능 여부",
        choices=(
            ("1", "가능"),
            ("0", "불가(차단)"),
        ),
        initial="1",
        widget=forms.RadioSelect(
            attrs={"class": "counselor-availability-status-radio notranslate"},
        ),
    )

    def clean(self):
        cleaned = super().clean()
        setting_type = cleaned.get("setting_type")
        day_of_week = cleaned.get("day_of_week")
        specific_date = cleaned.get("specific_date")
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")

        if start and end and end <= start:
            raise forms.ValidationError("종료 시간은 시작 시간보다 늦어야 합니다.")

        if setting_type == SETTING_RECURRING:
            if day_of_week is None or day_of_week == "":
                self.add_error("day_of_week", "요일을 선택해 주세요.")
            cleaned["specific_date"] = None
            cleaned["weekdays"] = [day_of_week]
        elif setting_type == SETTING_DAILY:
            cleaned["specific_date"] = None
            cleaned["day_of_week"] = None
            cleaned["weekdays"] = list(WEEKDAY_DAILY_DAYS)
        elif setting_type == SETTING_SPECIFIC:
            if not specific_date:
                self.add_error("specific_date", "특정 날짜를 선택해 주세요.")
            elif (
                cleaned.get("is_available") == "1"
                and specific_date
                and specific_date < timezone.localdate()
            ):
                self.add_error("specific_date", "오늘 이후 날짜만 등록할 수 있습니다.")
            cleaned["day_of_week"] = None
            cleaned["weekdays"] = []
        return cleaned

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and self.errors:
            for name, field in self.fields.items():
                if name in self.errors:
                    css = field.widget.attrs.get("class", "")
                    field.widget.attrs["class"] = f"{css} is-invalid".strip()


class AppointmentRequestForm(forms.ModelForm):
    """내담자 예약 신청 — 상담 희망 시간만 입력 (시간(분)은 상담사 확정 시 결정)"""

    scheduled_at = forms.DateTimeField(
        label="상담 희망 시간",
        required=True,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(
            attrs={
                "class": "form-control client-schedule-datetime-input",
                "type": "text",
                "placeholder": "날짜와 시간을 선택해 주세요",
                "autocomplete": "off",
            },
            format="%Y-%m-%d %H:%M",
        ),
    )

    class Meta:
        model = Appointment
        fields = ("scheduled_at",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and self.errors:
            for name, field in self.fields.items():
                if name in self.errors:
                    css = field.widget.attrs.get("class", "")
                    field.widget.attrs["class"] = f"{css} is-invalid".strip()

    def clean_scheduled_at(self):
        scheduled_at = self.cleaned_data.get("scheduled_at")
        if scheduled_at and scheduled_at < timezone.now():
            raise forms.ValidationError("상담 일시는 현재 시각 이후로 선택해 주세요.")
        return scheduled_at


class AppointmentScheduleForm(forms.ModelForm):
    """상담 희망/확정 일시 입력 (내담자 신청 · 상담사 수정 공통)"""

    scheduled_at = forms.DateTimeField(
        label="상담 희망 시간",
        required=True,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(
            attrs={"class": "form-control", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
    )

    class Meta:
        model = Appointment
        fields = ("scheduled_at", "duration_minutes")
        widgets = {
            "duration_minutes": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 20,
                    "max": 180,
                    "step": 10,
                    "style": "max-width: 8rem;",
                }
            ),
        }
        labels = {
            "duration_minutes": "상담 시간(분)",
        }

    def __init__(self, *args, counselor_label=False, calendar_picker=False, **kwargs):
        super().__init__(*args, **kwargs)
        if counselor_label:
            self.fields["scheduled_at"].label = "상담 일시"
        if calendar_picker:
            self.fields["scheduled_at"].widget = forms.TextInput(
                attrs={
                    "class": "form-control client-schedule-datetime-input schedule-datetime-picker",
                    "autocomplete": "off",
                    "placeholder": "날짜와 시간을 선택해 주세요",
                }
            )
        if not self.instance.pk:
            self.fields["duration_minutes"].initial = DEFAULT_APPOINTMENT_DURATION_MINUTES
        if self.is_bound and self.errors:
            for name, field in self.fields.items():
                if name in self.errors:
                    css = field.widget.attrs.get("class", "")
                    field.widget.attrs["class"] = f"{css} is-invalid".strip()

    def clean_scheduled_at(self):
        scheduled_at = self.cleaned_data.get("scheduled_at")
        if scheduled_at and scheduled_at < timezone.now():
            raise forms.ValidationError("상담 일시는 현재 시각 이후로 선택해 주세요.")
        return scheduled_at


# 하위 호환
AppointmentBookForm = AppointmentScheduleForm


class AppointmentRejectForm(forms.Form):
    """상담사 예약 반려 사유."""

    reject_reason = forms.CharField(
        label="반려 사유",
        min_length=5,
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "내담자에게 전달할 반려 사유를 입력해 주세요.",
            }
        ),
    )