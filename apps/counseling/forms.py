import os

from django import forms

from apps.accounts.models import ClientProfile, UserRole



COUNSELING_TYPE_CHOICES = [

    ("", "선택해 주세요"),

    ("개인상담", "개인상담"),

    ("학업상담", "학업상담"),

    ("진로상담", "진로상담"),

    ("대인관계", "대인관계"),

    ("가족상담", "가족상담"),

    ("심리·정서", "심리·정서"),

    ("기타", "기타"),

]





class CounselingApplyForm(forms.Form):

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

        widget=forms.TextInput(

            attrs={"class": "form-control", "placeholder": "예: 20241234"}

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

    counseling_type = forms.ChoiceField(

        label="상담 희망 분야",

        choices=COUNSELING_TYPE_CHOICES,

        widget=forms.Select(attrs={"class": "form-select"}),

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

        label="상담 신청 사유",

        widget=forms.Textarea(

            attrs={

                "class": "form-control",

                "rows": 5,

                "placeholder": "상담을 받고 싶은 내용을 간단히 작성해 주세요.",

            }

        ),

    )



    def __init__(self, *args, user=None, **kwargs):

        self.user = user

        super().__init__(*args, **kwargs)

        if user is not None and user.is_authenticated:

            self._lock_identity_fields(user)

        if self.is_bound and self.errors:

            for name, field in self.fields.items():

                if name in self.errors:

                    css = field.widget.attrs.get("class", "")

                    field.widget.attrs["class"] = f"{css} is-invalid".strip()



    def _lock_identity_fields(self, user):

        """로그인 사용자 — 이름·학번은 회원 정보에서 고정(화면 수정 불가)."""

        identity_help = "회원가입 정보와 동일하며 변경할 수 없습니다."

        for field_name, extra_attrs in (

            ("name", {"readonly": "readonly", "autocomplete": "name"}),

            ("student_id", {"readonly": "readonly"}),

        ):

            field = self.fields[field_name]

            field.disabled = True

            field.help_text = identity_help

            field.widget.attrs.update(extra_attrs)

            field.widget.attrs.setdefault("class", "form-control")

        self.fields["name"].initial = user.name

        try:

            profile = user.client_profile

        except ClientProfile.DoesNotExist:

            profile = None

        self.fields["student_id"].initial = (

            profile.student_id if profile is not None else ""

        ) or ""



    def clean(self):

        cleaned = super().clean()

        if self.user is None or not self.user.is_authenticated:

            return cleaned

        cleaned["name"] = self.user.name

        try:

            profile = self.user.client_profile

            student_id = profile.student_id or ""

        except ClientProfile.DoesNotExist:

            student_id = ""

        if (

            not student_id

            and self.user.role == UserRole.CLIENT

            and not self.user.is_superuser

        ):

            self.add_error(

                "student_id",

                "회원 정보에 학번이 등록되어 있지 않습니다. 관리자에게 문의해 주세요.",

            )

        else:

            cleaned["student_id"] = student_id

        return cleaned



    def clean_counseling_type(self):

        value = self.cleaned_data.get("counseling_type")

        if not value:

            raise forms.ValidationError("상담 희망 분야를 선택해 주세요.")

        return value





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

            specialties = (

                ", ".join(profile.specialties)

                if profile.specialties

                else "전문분야 미등록"

            )

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

