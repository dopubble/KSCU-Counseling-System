from django import forms

from .models import TerminationCounselingRecord


class TerminationCounselingRecordForm(forms.ModelForm):
    """상담사용 종결기록지 (마지막 회기 전용)."""

    class Meta:
        model = TerminationCounselingRecord
        fields = (
            "counseling_period",
            "main_topics",
            "termination_reason",
            "counselor_opinion",
            "post_termination_plan",
            "other_notes",
        )
        widgets = {
            "counseling_period": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 3}
            ),
            "main_topics": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 5}
            ),
            "termination_reason": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 5}
            ),
            "counselor_opinion": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 5}
            ),
            "post_termination_plan": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 5}
            ),
            "other_notes": forms.Textarea(
                attrs={"class": "form-control journal-textarea", "rows": 4}
            ),
        }
        labels = {
            "counseling_period": "상담 진행 일시",
            "main_topics": "상담받은 주요주제",
            "termination_reason": "종결(중단) 사유",
            "counselor_opinion": "내담자에 대한 상담자 소견",
            "post_termination_plan": "종결 후 계획 또는 후속조치",
            "other_notes": "기타",
        }

    def save(self, commit=True):
        record = super().save(commit=False)
        record.is_draft = False
        if commit:
            record.save()
        return record
