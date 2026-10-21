from django import forms
from django.utils import timezone

from .models import CounselingJournal, normalize_session_categories


class CounselingJournalForm(forms.ModelForm):
    """상담사용 상담일지 작성 폼 (SOAP 구조)"""

    session_datetime = forms.DateTimeField(
        label="상담 일시",
        required=True,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(
            attrs={"class": "form-control", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
    )
    counseling_content = forms.CharField(
        label="상담 내용",
        required=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control journal-textarea",
                "rows": 10,
                "placeholder": "이번 회기에서 다룬 주제, 내담자의 주요 발화·정서·행동 등을 구체적으로 기록해 주세요.",
            }
        ),
    )
    counselor_observation = forms.CharField(
        label="상담자 관찰",
        required=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control journal-textarea",
                "rows": 8,
                "placeholder": "상담 중 관찰한 태도, 비언어적 단서, 관계 형성, 변화 등을 기록해 주세요.",
            }
        ),
    )
    clinical_assessment = forms.CharField(
        label="임상적 평가",
        required=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control journal-textarea",
                "rows": 6,
                "placeholder": "현재 상태에 대한 전문적 평가, 위험도, 강점·자원 등을 기록해 주세요.",
            }
        ),
    )
    follow_up_plan = forms.CharField(
        label="향후 계획",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control journal-textarea",
                "rows": 4,
                "placeholder": "다음 회기 계획, 과제, 의뢰 사항 등 (선택)",
            }
        ),
    )

    class Meta:
        model = CounselingJournal
        fields = ("session_number",)
        widgets = {
            "session_number": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "style": "max-width: 8rem;"}
            ),
        }
        labels = {
            "session_number": "회기",
        }

    def __init__(self, *args, case=None, **kwargs):
        self.case = case
        super().__init__(*args, **kwargs)

        # UUID 기본값 때문에 신규 인스턴스도 pk가 채워져 있으므로 _state.adding으로 판별한다.
        self.is_new = self.instance._state.adding

        if not self.is_new:
            if self.instance.session_datetime:
                self.fields["session_datetime"].initial = timezone.localtime(
                    self.instance.session_datetime
                )
            self.fields["counseling_content"].initial = self.instance.subjective
            self.fields["counselor_observation"].initial = self.instance.objective
            self.fields["clinical_assessment"].initial = self.instance.assessment
            self.fields["follow_up_plan"].initial = self.instance.plan

        if self.is_bound and self.errors:
            for name, field in self.fields.items():
                if name in self.errors:
                    css = field.widget.attrs.get("class", "")
                    field.widget.attrs["class"] = f"{css} is-invalid".strip()

    @property
    def session_category_values(self) -> list[str]:
        """읽기 전용 상담 구분 — 신규는 신청 정보, 수정은 저장된 스냅샷."""
        if not self.is_new:
            return self.instance.session_category_list
        if self.case:
            return normalize_session_categories(self.case.application.counseling_types)
        return []

    def clean_session_number(self):
        session_number = self.cleaned_data.get("session_number")
        if self.case and session_number is not None:
            qs = CounselingJournal.objects.filter(
                case=self.case, session_number=session_number
            )
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    f"{session_number}회기 일지가 이미 있습니다. 다른 회차를 입력해 주세요."
                )
        return session_number

    def save(self, commit=True):
        journal = super().save(commit=False)
        if self.is_new:
            # 상담 구분은 POST 값이 아니라 신청 정보에서 서버 측으로 스냅샷한다.
            categories = self.session_category_values
            journal.session_categories = categories
            journal.session_category = categories[0] if categories else ""
        journal.session_datetime = self.cleaned_data["session_datetime"]
        journal.subjective = self.cleaned_data["counseling_content"]
        journal.objective = self.cleaned_data["counselor_observation"]
        journal.assessment = self.cleaned_data["clinical_assessment"]
        journal.plan = self.cleaned_data.get("follow_up_plan", "")
        journal.is_draft = False
        if commit:
            journal.save()
            from apps.counseling.services import finalize_completed_journal

            finalize_completed_journal(journal)
        return journal
