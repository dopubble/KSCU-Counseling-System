import os

from django import forms

from apps.accounts.models import ClientProfile
from apps.counseling.constants import (
    COUNSELING_TYPE_CHOICES,
    COUNSELING_TYPE_VALUES,
    normalize_counseling_types,
)
from apps.counseling.models import CounselingMethod
from apps.counseling.presentation_board import (
    default_presentation_post_title,
)





class CounselingApplyForm(forms.Form):
    IDENTITY_FIELD_NAMES = ("name", "student_id", "birth_date", "department")
    IDENTITY_HELP = "회원가입 정보와 동일하며 변경할 수 없습니다."

    name = forms.CharField(
        label="이름",
        max_length=100,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "홍길동"}
        ),
    )
    student_id = forms.CharField(
        label="학번",
        max_length=20,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "미입력 시 비워 둡니다"}
        ),
    )
    birth_date = forms.DateField(
        label="생년월일",
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
        ),
    )
    department = forms.CharField(
        label="소속 학과",
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "미입력 시 비워 둡니다"}
        ),
    )
    phone = forms.CharField(
        label="연락처",
        max_length=20,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "010-0000-0000"}
        ),
    )
    residence_region = forms.CharField(
        label="거주지역",
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "예: 서울시, 강원도 / 해외: 캐나다",
            }
        ),
        help_text="국내는 시·도 단위만, 해외 거주 시 국가명까지 입력해 주세요.",
    )
    clinical_diagnosis = forms.CharField(
        label="병원 진단명",
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "예: 우울증, 공황장애",
            }
        ),
    )
    current_medication = forms.CharField(
        label="현재 복용 중인 관련 약",
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "복용 중인 약이 없으면 '없음'",
            }
        ),
        help_text="정신·심리 관련 복용 약이 없으면 '없음'이라고 적어 주세요.",
    )
    occupation = forms.CharField(
        label="직업",
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "직접 입력 (선택)",
            }
        ),
    )
    counseling_types = forms.MultipleChoiceField(
        label="상담 희망 분야",
        choices=COUNSELING_TYPE_CHOICES,
        widget=forms.CheckboxSelectMultiple(
            attrs={"class": "counseling-type-checkboxes"}
        ),
    )
    counseling_method = forms.ChoiceField(
        label="상담 방식",
        choices=CounselingMethod.choices,
        required=True,
        widget=forms.RadioSelect(attrs={"class": "counseling-method-radio"}),
        help_text="대면은 상담실 방문, 비대면은 Zoom 화상 상담입니다. 원하시는 방식을 선택해 주세요.",
        error_messages={
            "required": "상담 방식을 선택해 주세요.",
        },
    )
    preferred_date = forms.DateField(
        label="희망 상담일",
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
        ),
        error_messages={
            "invalid": "날짜 형식이 올바르지 않습니다. 달력에서 날짜를 선택해 주세요.",
            "required": "희망 상담일을 선택해 주세요.",
        },
    )
    preferred_time = forms.TimeField(
        label="희망 상담 시간",
        input_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.TimeInput(
            attrs={"class": "form-control", "type": "time"},
            format="%H:%M",
        ),
        error_messages={
            "invalid": "시간 형식이 올바르지 않습니다. 예: 14:00",
            "required": "희망 상담 시간을 선택해 주세요.",
        },
    )
    reason = forms.CharField(
        label="주요 호소 문제 작성(100자 이내)",
        max_length=100,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "maxlength": "100",
                "placeholder": "주요 호소 문제를 100자 이내로 작성해 주세요.",
            }
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["counseling_types"].widget.attrs.update({"class": "form-check-input"})
        self.fields["counseling_method"].widget.attrs.update({"class": "form-check-input"})
        if user is not None and user.is_authenticated:
            self._lock_identity_fields(user)
        if self.is_bound and self.errors:
            for name, field in self.fields.items():
                if name in self.errors:
                    css = field.widget.attrs.get("class", "")
                    field.widget.attrs["class"] = f"{css} is-invalid".strip()

    def _lock_identity_fields(self, user):
        """로그인 사용자 — 이름·학번·생년월일·학과는 회원 정보에서 고정."""
        snapshot = self._profile_snapshot(user)
        readonly_attrs = {
            "name": {"readonly": "readonly", "autocomplete": "name"},
            "student_id": {"readonly": "readonly"},
            "birth_date": {"readonly": "readonly"},
            "department": {"readonly": "readonly"},
        }
        for field_name in self.IDENTITY_FIELD_NAMES:
            field = self.fields[field_name]
            field.disabled = True
            field.required = False
            field.help_text = self.IDENTITY_HELP
            field.widget.attrs.update(readonly_attrs.get(field_name, {}))
            field.widget.attrs.setdefault("class", "form-control")
            if field_name == "birth_date":
                field.widget.attrs.setdefault("type", "date")

        self.fields["name"].initial = snapshot["name"]
        self.fields["student_id"].initial = snapshot["student_id"]
        self.fields["birth_date"].initial = snapshot["birth_date"]
        self.fields["department"].initial = snapshot["department"]

    @staticmethod
    def _profile_snapshot(user):
        try:
            profile = user.client_profile
        except ClientProfile.DoesNotExist:
            profile = None
        return {
            "name": user.name,
            "student_id": (profile.student_id if profile else "") or "",
            "birth_date": profile.birth_date if profile else None,
            "department": (profile.department if profile else "") or "",
        }

    def clean(self):
        cleaned = super().clean()
        if self.user is None or not self.user.is_authenticated:
            return cleaned
        snapshot = self._profile_snapshot(self.user)
        cleaned["name"] = snapshot["name"]
        cleaned["student_id"] = snapshot["student_id"]
        cleaned["birth_date"] = snapshot["birth_date"]
        cleaned["department"] = snapshot["department"]
        return cleaned

    def clean_counseling_types(self):
        value = normalize_counseling_types(self.cleaned_data.get("counseling_types"))
        if not value:
            raise forms.ValidationError("상담 희망 분야를 하나 이상 선택해 주세요.")
        return value

    def clean_reason(self):
        value = (self.cleaned_data.get("reason") or "").strip()
        if not value:
            raise forms.ValidationError("주요 호소 문제를 작성해 주세요.")
        if len(value) > 100:
            raise forms.ValidationError("100자 이내로 작성해 주세요.")
        return value

    def clean_residence_region(self):
        value = (self.cleaned_data.get("residence_region") or "").strip()
        if not value:
            raise forms.ValidationError("거주지역을 입력해 주세요.")
        return value

    def clean_clinical_diagnosis(self):
        value = (self.cleaned_data.get("clinical_diagnosis") or "").strip()
        if not value:
            raise forms.ValidationError("병원 진단명을 입력해 주세요.")
        return value

    def clean_current_medication(self):
        value = (self.cleaned_data.get("current_medication") or "").strip()
        if not value:
            raise forms.ValidationError(
                "현재 복용 중인 관련 약을 입력해 주세요. 없으면 '없음'이라고 적어 주세요."
            )
        return value

    def clean_occupation(self):
        return (self.cleaned_data.get("occupation") or "").strip()





class CounselorMatchForm(forms.Form):

    counselor = forms.ChoiceField(

        label="담당 상담사",

        widget=forms.Select(attrs={"class": "form-select form-select-lg"}),

    )

    total_sessions = forms.IntegerField(

        label="총 상담 회기",

        min_value=1,

        max_value=99,

        initial=10,

        widget=forms.NumberInput(

            attrs={

                "class": "form-control",

                "min": 1,

                "max": 99,

                "placeholder": "예: 10",

            }

        ),

        help_text="매칭 시 계획하는 전체 상담 횟수입니다. 남은 회기는 동일 값으로 시작합니다.",

    )



    def __init__(

        self,

        *args,

        counselor_profiles=None,

        active_case_counts=None,

        require_total_sessions=False,

        **kwargs,

    ):

        super().__init__(*args, **kwargs)

        self.require_total_sessions = require_total_sessions

        if not require_total_sessions:

            self.fields["total_sessions"].required = False

        counts = active_case_counts or {}

        choices = [("", "상담사를 선택해 주세요")]

        for profile in counselor_profiles or []:

            user = profile.user

            if profile.specialties:
                if isinstance(profile.specialties, (list, tuple)):
                    specialties = ", ".join(str(item) for item in profile.specialties)
                else:
                    specialties = str(profile.specialties)
            else:
                specialties = "전문분야 미등록"

            active_n = counts.get(user.pk, 0)

            label = f"{user.name} — {specialties} · 진행 중 {active_n}건"

            if not profile.is_approved:

                label += " (승인 대기)"

            choices.append((str(user.pk), label))

        self.fields["counselor"].choices = choices



    def clean_counselor(self):

        value = self.cleaned_data.get("counselor")

        if not value:

            raise forms.ValidationError("상담사를 선택해 주세요.")

        return value



    def clean_total_sessions(self):

        value = self.cleaned_data.get("total_sessions")

        if not self.require_total_sessions:

            return value

        if value is None:

            raise forms.ValidationError("총 상담 회기를 입력해 주세요.")

        if value < 1:

            raise forms.ValidationError("총 상담 회기는 1회 이상이어야 합니다.")

        return value





class CancelRequestForm(forms.Form):

    cancel_reason = forms.CharField(

        label="취소 사유",

        min_length=5,

        max_length=1000,

        widget=forms.Textarea(

            attrs={

                "class": "form-control",

                "rows": 4,

                "placeholder": "취소가 필요한 사유를 입력해 주세요.",

            }

        ),

        error_messages={

            "required": "취소 사유를 입력해 주세요.",

            "min_length": "취소 사유를 5자 이상 입력해 주세요.",

        },

    )


class SessionScheduleChangeForm(forms.Form):
    """회기별 예약·일정 변경 — Flatpickr YYYY-MM-DD HH:MM 형식."""

    PREFERRED_DATETIME_INPUT_FORMATS = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ]

    preferred_datetime = forms.DateTimeField(
        label="희망 일시",
        required=False,
        input_formats=PREFERRED_DATETIME_INPUT_FORMATS,
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
    message = forms.CharField(
        label="요청 내용",
        required=False,
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "변경이 필요한 사유와 희망 일정을 알려 주세요. (선택 사항)",
            }
        ),
    )

    def clean_preferred_datetime(self):
        from apps.scheduling.availability import normalize_client_preferred_datetime

        return normalize_client_preferred_datetime(
            self.cleaned_data.get("preferred_datetime")
        )


class SessionMaterialUploadForm(forms.Form):
    ALLOWED_EXTENSIONS = {".pdf", ".hwp", ".doc", ".docx", ".jpg", ".jpeg"}
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    ACCEPT_ATTR = ".pdf,.hwp,.doc,.docx,.jpg,.jpeg"
    INVALID_TYPE_MESSAGE = (
        "허용되지 않는 파일 형식입니다. PDF, HWP, Word, JPG만 가능합니다."
    )
    MAX_SIZE_MESSAGE = "파일 크기는 10MB 이하여야 합니다."

    file = forms.FileField(
        label="첨부 파일",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": ".pdf,.hwp,.doc,.docx,.jpg,.jpeg",
            }
        ),
    )
    title = forms.CharField(
        label="자료명",
        required=False,
        max_length=200,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "선택 입력"}
        ),
    )

    def clean_file(self):
        file_obj = self.cleaned_data.get("file")
        if not file_obj:
            return file_obj

        ext = os.path.splitext(file_obj.name)[1].lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            raise forms.ValidationError(self.INVALID_TYPE_MESSAGE)

        if file_obj.size > self.MAX_FILE_SIZE:
            raise forms.ValidationError(self.MAX_SIZE_MESSAGE)

        return file_obj


class BoardPostForm(forms.Form):
    """게시판 게시글 작성·수정 (텍스트 + 선택적 파일)."""

    ALLOWED_EXTENSIONS = SessionMaterialUploadForm.ALLOWED_EXTENSIONS
    MAX_FILE_SIZE = SessionMaterialUploadForm.MAX_FILE_SIZE
    INVALID_TYPE_MESSAGE = SessionMaterialUploadForm.INVALID_TYPE_MESSAGE
    MAX_SIZE_MESSAGE = SessionMaterialUploadForm.MAX_SIZE_MESSAGE

    title = forms.CharField(
        label="제목",
        max_length=200,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "게시글 제목"}
        ),
    )
    content = forms.CharField(
        label="내용",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 5,
                "placeholder": "내담자에게 전달할 내용을 입력해 주세요.",
            }
        ),
    )
    file = forms.FileField(
        label="첨부 파일",
        required=False,
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": SessionMaterialUploadForm.ACCEPT_ATTR,
            }
        ),
    )

    def __init__(self, *args, existing_file=False, **kwargs):
        self.existing_file = existing_file
        super().__init__(*args, **kwargs)

    def clean_file(self):
        file_obj = self.cleaned_data.get("file")
        if not file_obj:
            return file_obj

        ext = os.path.splitext(file_obj.name)[1].lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            raise forms.ValidationError(self.INVALID_TYPE_MESSAGE)

        if file_obj.size > self.MAX_FILE_SIZE:
            raise forms.ValidationError(self.MAX_SIZE_MESSAGE)

        return file_obj

    def clean(self):
        cleaned = super().clean()
        content = (cleaned.get("content") or "").strip()
        file_obj = cleaned.get("file")
        if not content and not file_obj and not self.existing_file:
            raise forms.ValidationError(
                "내용 또는 첨부 파일 중 하나는 입력해 주세요."
            )
        cleaned["content"] = content
        return cleaned


class PresentationBoardPostForm(forms.Form):
    """사례발표 게시판 — 수퍼비전(사례발표)보고서 게시글."""

    ALLOWED_EXTENSIONS = {".pdf"}
    MAX_FILE_SIZE = 10 * 1024 * 1024
    ACCEPT_ATTR = ".pdf,application/pdf"
    INVALID_TYPE_MESSAGE = "PDF 파일만 업로드할 수 있습니다."
    MAX_SIZE_MESSAGE = "파일 크기는 10MB 이하여야 합니다."

    title = forms.CharField(
        label="제목",
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "form-control bg-light",
                "id": "presentationPostTitle",
                "readonly": "readonly",
            }
        ),
    )
    content = forms.CharField(
        label="내용",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "안내 사항이 있으면 입력해 주세요. (선택)",
            }
        ),
    )
    file = forms.FileField(
        label="수퍼비전보고서 파일",
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": ACCEPT_ATTR}
        ),
    )

    def __init__(self, *args, author_name: str = "", **kwargs):
        self.author_name = author_name
        super().__init__(*args, **kwargs)
        if author_name:
            self.fields["title"].initial = default_presentation_post_title(author_name)

    def clean_title(self):
        if self.author_name:
            return default_presentation_post_title(self.author_name)
        title = (self.cleaned_data.get("title") or "").strip()
        if not title:
            raise forms.ValidationError("제목을 입력해 주세요.")
        return title

    def clean_file(self):
        file_obj = self.cleaned_data.get("file")
        if not file_obj:
            raise forms.ValidationError("수퍼비전보고서 파일을 첨부해 주세요.")
        ext = os.path.splitext(file_obj.name)[1].lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            raise forms.ValidationError(self.INVALID_TYPE_MESSAGE)
        if file_obj.size > self.MAX_FILE_SIZE:
            raise forms.ValidationError(self.MAX_SIZE_MESSAGE)
        return file_obj


class PresentationBoardCommentForm(forms.Form):
    """사례발표 게시판 — 사례개념화보고서 댓글 (간단 코멘트 + PDF)."""

    ALLOWED_EXTENSIONS = PresentationBoardPostForm.ALLOWED_EXTENSIONS
    MAX_FILE_SIZE = PresentationBoardPostForm.MAX_FILE_SIZE
    ACCEPT_ATTR = PresentationBoardPostForm.ACCEPT_ATTR
    INVALID_TYPE_MESSAGE = PresentationBoardPostForm.INVALID_TYPE_MESSAGE
    MAX_SIZE_MESSAGE = PresentationBoardPostForm.MAX_SIZE_MESSAGE

    content = forms.CharField(
        label="코멘트",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "간단한 코멘트를 입력해 주세요. (선택)",
            }
        ),
    )
    file = forms.FileField(
        label="PDF 첨부",
        required=False,
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": ACCEPT_ATTR}
        ),
    )

    def clean_file(self):
        file_obj = self.cleaned_data.get("file")
        if not file_obj:
            return None
        ext = os.path.splitext(file_obj.name)[1].lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            raise forms.ValidationError(self.INVALID_TYPE_MESSAGE)
        if file_obj.size > self.MAX_FILE_SIZE:
            raise forms.ValidationError(self.MAX_SIZE_MESSAGE)
        return file_obj

    def clean(self):
        cleaned = super().clean()
        content = (cleaned.get("content") or "").strip()
        file_obj = cleaned.get("file")
        if not content and not file_obj:
            raise forms.ValidationError(
                "코멘트 또는 PDF 첨부 파일 중 하나는 입력해 주세요."
            )
        cleaned["content"] = content
        return cleaned

