from django import forms
from django.utils import timezone

from .models import InitialCounselingRecord


class InitialCounselingRecordForm(forms.ModelForm):
    """상담사용 초기상담 기록지 (1회기 전용)."""

    session_start_datetime = forms.DateTimeField(
        label="상담 시작 일시",
        required=True,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(
            attrs={"class": "form-control", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
    )

    class Meta:
        model = InitialCounselingRecord
        fields = (
            "session_start_datetime",
            "presented_problems_summary",
            "functioning_impact",
            "relational_history",
            "clinical_history",
            "theological_evaluation",
            "clinical_strategy",
            "other_notes",
        )
        widgets = {
            "presented_problems_summary": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 5}
            ),
            "functioning_impact": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 5}
            ),
            "relational_history": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 5}
            ),
            "clinical_history": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 5}
            ),
            "theological_evaluation": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 5}
            ),
            "clinical_strategy": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 5}
            ),
            "other_notes": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 4}
            ),
        }
        labels = {
            "presented_problems_summary": (
                "제시된 문제들, 중심 주제, 패턴, 현재 주의를 요하는 내담자의 상태를 요약하면?"
            ),
            "functioning_impact": (
                "현재와 과거의 기능: 현재의 문제들이 자신의 행동이나 대인관계에 영향을 미치는 방식은?"
            ),
            "relational_history": (
                "내담자의 관계적 역사: 관련된 개인적, 가족적, 공동체적·문화적 역사는? "
                "(필요하면 가계도 이용)"
            ),
            "clinical_history": (
                "내담자의 임상적 역사: 관련된 신체적, 상담·치료적, 정신의학적 역사는?"
            ),
            "theological_evaluation": (
                "신학적 평가: 종교성, 소속된 종교단체, 종교적 신념 및 행위 등 신학적 진단을 한다면?"
            ),
            "clinical_strategy": (
                "임상적 전략: 현재의 진단 및 최초의 임상적 개입과 차후 임상 계획은 (단기 및 장기)?"
            ),
            "other_notes": "기타",
        }

    def save(self, commit=True):
        record = super().save(commit=False)
        record.is_draft = False
        if commit:
            record.save()
        return record

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.session_start_datetime:
            local_dt = timezone.localtime(self.instance.session_start_datetime)
            self.initial["session_start_datetime"] = local_dt.strftime("%Y-%m-%dT%H:%M")
