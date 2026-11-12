import os

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Q

from apps.accounts.models import ClientProfile
from apps.counseling.apply_registration import (
    EMAIL_TAKEN,
    EMAIL_UNCONFIRMED,
    email_is_registered,
    normalize_account_email,
)
from apps.counseling.constants import (
    APPLICATION_COUNSELING_METHOD_CHOICES,
    COUNSELING_TYPE_CHOICES,
    COUNSELING_TYPE_VALUES,
    normalize_counseling_types,
    normalize_weekly_availability,
    weekly_availability_tokens,
)
from apps.counseling.models import Case, CounselingMethod, TrainingEnrollment
from apps.counseling.privacy import mask_client_name
from apps.counseling.presentation_board import (
    PRESENTATION_COMMENT_ACCEPT_ATTR,
    PRESENTATION_COMMENT_ALLOWED_EXTENSIONS,
    PRESENTATION_COMMENT_ATTACHMENT_LABEL,
    PRESENTATION_COMMENT_INVALID_TYPE_MESSAGE,
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
        required=True,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date", "required": "required"},
            format="%Y-%m-%d",
        ),
        error_messages={
            "required": "생년월일을 입력해주세요.",
            "invalid": "올바른 생년월일을 입력해주세요.",
        },
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
        choices=APPLICATION_COUNSELING_METHOD_CHOICES,
        required=True,
        widget=forms.RadioSelect(attrs={"class": "counseling-method-radio"}),
        help_text="대면은 상담실 방문, 비대면은 Zoom 화상 상담입니다. 둘 다 가능하면 모두 가능을 선택해 주세요.",
        error_messages={
            "required": "상담 방식을 선택해 주세요.",
        },
    )
    weekly_availability = forms.MultipleChoiceField(
        label="상담 가능한 요일과 시간대",
        choices=weekly_availability_tokens(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        error_messages={
            "invalid_choice": "선택할 수 없는 요일 또는 시간대입니다.",
        },
    )
    other_requests = forms.CharField(
        label="기타 요청사항",
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "maxlength": "500",
            }
        ),
        error_messages={
            "max_length": "기타 요청사항은 500자 이내로 입력해 주세요.",
        },
    )
    email = forms.EmailField(
        label="이메일",
        required=False,
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "name@example.com",
                "autocomplete": "email",
            }
        ),
    )
    email_confirmed = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    password1 = forms.CharField(
        label="비밀번호",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "autocomplete": "new-password"}
        ),
    )
    password2 = forms.CharField(
        label="비밀번호 확인",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "autocomplete": "new-password"}
        ),
    )
    is_kcu_student = forms.ChoiceField(
        label="숭실사이버대학교 학생 여부",
        choices=[("yes", "예"), ("no", "아니오")],
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        required=False,
    )
    agree_service_terms = forms.BooleanField(
        label="이용약관 동의",
        required=False,
    )
    agree_privacy = forms.BooleanField(
        label="개인정보 수집 및 이용 동의",
        required=False,
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
        self.guest_signup = user is None or not getattr(user, "is_authenticated", False)
        if self.guest_signup:
            self.fields["email"].widget.attrs["required"] = "required"
            self.fields["password1"].widget.attrs["required"] = "required"
            self.fields["password2"].widget.attrs["required"] = "required"
        else:
            for name in (
                "email",
                "email_confirmed",
                "password1",
                "password2",
                "agree_service_terms",
                "agree_privacy",
                "is_kcu_student",
            ):
                self.fields.pop(name, None)
            self._lock_identity_fields(user)
        if self.is_bound and self.errors:
            for name, field in self.fields.items():
                if name in self.errors:
                    css = field.widget.attrs.get("class", "")
                    field.widget.attrs["class"] = f"{css} is-invalid".strip()

    def _lock_identity_fields(self, user):
        """로그인 사용자 — 이름·학번·학과는 회원 정보에서 고정. 생년월일은 필수."""
        snapshot = self._profile_snapshot(user)
        readonly_attrs = {
            "name": {"readonly": "readonly", "autocomplete": "name"},
            "student_id": {"readonly": "readonly"},
            "department": {"readonly": "readonly"},
        }
        for field_name in ("name", "student_id", "department"):
            field = self.fields[field_name]
            field.disabled = True
            field.required = False
            field.help_text = self.IDENTITY_HELP
            field.widget.attrs.update(readonly_attrs.get(field_name, {}))
            field.widget.attrs.setdefault("class", "form-control")

        birth_field = self.fields["birth_date"]
        birth_field.required = True
        birth_field.initial = snapshot["birth_date"]
        birth_field.widget.attrs.setdefault("class", "form-control")
        birth_field.widget.attrs.setdefault("type", "date")
        birth_field.widget.attrs["required"] = "required"
        if snapshot["birth_date"]:
            birth_field.disabled = True
            birth_field.required = False
            birth_field.help_text = self.IDENTITY_HELP
            birth_field.widget.attrs.update({"readonly": "readonly"})
            birth_field.widget.attrs.pop("required", None)

        self.fields["name"].initial = snapshot["name"]
        self.fields["student_id"].initial = snapshot["student_id"]
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

    def clean_email(self):
        if "email" not in self.fields:
            return ""
        email = normalize_account_email(self.cleaned_data.get("email") or "")
        return email

    def _clean_guest_account(self, cleaned):
        email = cleaned.get("email") or ""
        if "email" in self.errors:
            pass
        elif not email:
            self.add_error("email", "이메일을 입력해 주세요.")
        else:
            confirmed = normalize_account_email(cleaned.get("email_confirmed") or "")
            if confirmed != email:
                self.add_error("email", EMAIL_UNCONFIRMED)
            elif email_is_registered(email):
                self.add_error("email", EMAIL_TAKEN)
        password = cleaned.get("password1") or ""
        confirmation = cleaned.get("password2") or ""
        if not password:
            self.add_error("password1", "비밀번호를 입력해 주세요.")
        elif password != confirmation:
            self.add_error("password2", "비밀번호가 일치하지 않습니다.")
        else:
            try:
                validate_password(password)
            except ValidationError as exc:
                self.add_error("password1", exc)
        if not cleaned.get("agree_service_terms"):
            self.add_error("agree_service_terms", "이용약관에 동의해 주세요.")
        if not cleaned.get("agree_privacy"):
            self.add_error("agree_privacy", "개인정보 수집 및 이용에 동의해 주세요.")
        is_kcu = cleaned.get("is_kcu_student")
        if not is_kcu:
            self.add_error(
                "is_kcu_student",
                "숭실사이버대학교 학생 여부를 선택해 주세요.",
            )
        elif is_kcu == "yes":
            department = (cleaned.get("department") or "").strip()
            if not department:
                self.add_error("department", "소속 학과를 입력해 주세요.")
            cleaned["department"] = department
            cleaned["student_id"] = (cleaned.get("student_id") or "").strip()
        else:
            cleaned["department"] = ""
            cleaned["student_id"] = ""

    def clean(self):
        cleaned = super().clean()
        if self.user is None or not self.user.is_authenticated:
            if not cleaned.get("birth_date"):
                self.add_error("birth_date", "생년월일을 입력해주세요.")
            self._clean_guest_account(cleaned)
            return cleaned
        snapshot = self._profile_snapshot(self.user)
        cleaned["name"] = snapshot["name"]
        cleaned["student_id"] = snapshot["student_id"]
        cleaned["department"] = snapshot["department"]
        if snapshot["birth_date"]:
            cleaned["birth_date"] = snapshot["birth_date"]
        if not cleaned.get("birth_date") and "birth_date" not in self.errors:
            self.add_error("birth_date", "생년월일을 입력해주세요.")
        return cleaned

    def clean_weekly_availability(self):
        raw = self.cleaned_data.get("weekly_availability") or []
        try:
            value = normalize_weekly_availability(raw)
        except ValueError:
            raise forms.ValidationError("선택할 수 없는 요일 또는 시간대입니다.") from None
        if not value:
            raise forms.ValidationError(
                "상담 가능한 요일과 시간대를 하나 이상 선택해 주세요."
            )
        return value

    def clean_other_requests(self):
        return (self.cleaned_data.get("other_requests") or "").strip()

    def apply_error_step(self) -> int:
        """서버 검증 실패 시 다시 보여줄 단계. 기본정보 오류가 있으면 1단계."""
        if not self.is_bound or not self.errors:
            return 1
        step2 = {
            "counseling_method",
            "weekly_availability",
            "counseling_types",
            "reason",
            "other_requests",
        }
        names = set(self.errors)
        if names - step2:
            return 1
        if names & step2:
            return 2
        return 1

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

    assigned_method = forms.ChoiceField(
        label="실제 상담방식",
        choices=[
            (CounselingMethod.IN_PERSON, "대면 상담"),
            (CounselingMethod.REMOTE, "비대면 상담"),
        ],
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        required=False,
        help_text="내담자는 대면·비대면 모두 가능을 선택했습니다. 실제 진행 방식을 선택해 주세요.",
        error_messages={"required": "실제 상담방식을 선택해 주세요."},
    )



    def __init__(

        self,

        *args,

        counselor_profiles=None,

        active_case_counts=None,

        require_total_sessions=False,

        require_assigned_method=False,

        **kwargs,

    ):

        super().__init__(*args, **kwargs)

        self.require_total_sessions = require_total_sessions
        self.require_assigned_method = require_assigned_method

        if not require_total_sessions:

            self.fields["total_sessions"].required = False

        if not require_assigned_method:
            self.fields.pop("assigned_method", None)
        else:
            self.fields["assigned_method"].required = True

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

    def clean_assigned_method(self):
        if not self.require_assigned_method:
            return ""
        value = (self.cleaned_data.get("assigned_method") or "").strip()
        if value not in (CounselingMethod.IN_PERSON, CounselingMethod.REMOTE):
            raise forms.ValidationError("실제 상담방식을 선택해 주세요.")
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


def author_has_multiple_enrollments(author) -> bool:
    if author is None or not getattr(author, "pk", None):
        return False
    return (
        TrainingEnrollment.objects.filter(counselor_id=author.pk).order_by().values("pk")[:2].count()
        > 1
    )


def counselor_case_queryset(author, *, cohort=None):
    """상담사 본인 담당 사례. 다른 상담사 사례는 선택지에 없다.

    cohort를 넘기면 다른 Enrollment 기수의 사례는 뺀다.
    기수가 둘 이상인 상담사는 Enrollment가 없는 사례도 빼서
    어느 기수 글에나 붙이지 못하게 한다.
    Enrollment가 하나 이하인 기존 상담사는 기수 없는 사례를 그대로 둔다.
    """
    if author is None or not getattr(author, "pk", None):
        return Case.objects.none()
    queryset = Case.objects.filter(counselor=author).select_related(
        "client", "application", "training_enrollment"
    )
    if cohort is not None:
        matched = Q(training_enrollment__cohort=cohort)
        if not author_has_multiple_enrollments(author):
            matched = matched | Q(training_enrollment__isnull=True)
        queryset = queryset.filter(matched)
    return queryset.order_by("case_number")


def presentation_case_matches_cohort(case, cohort, *, author=None) -> bool:
    """연결된 사례의 Enrollment 기수가 게시글 기수와 같아야 한다."""
    if case is None or cohort is None:
        return True
    if case.training_enrollment_id:
        return case.training_enrollment.cohort == cohort
    return not author_has_multiple_enrollments(author)


def case_report_choice_label(case) -> str:
    """사례번호 · 가린 내담자 이름 · 상담분야."""
    client_name = mask_client_name(case.client.name) if case.client_id else "—"
    field = ""
    if case.application_id:
        field = case.application.get_counseling_types_display()
    parts = [case.case_number, client_name]
    if field:
        parts.append(field)
    return " · ".join(parts)


class PresentationBoardPostCaseForm(forms.Form):
    """기존 사례발표 글에 담당 사례를 연결한다."""

    case = forms.ModelChoiceField(
        label="담당 사례",
        queryset=Case.objects.none(),
        required=True,
        empty_label="담당 사례를 선택해 주세요",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, author=None, cohort=None, **kwargs):
        self.author = author
        self.cohort = cohort
        super().__init__(*args, **kwargs)
        field = self.fields["case"]
        field.queryset = counselor_case_queryset(author, cohort=cohort)
        field.label_from_instance = case_report_choice_label

    def clean_case(self):
        case = self.cleaned_data.get("case")
        if case is None or self.author is None:
            return case
        if case.counselor_id != self.author.pk:
            raise forms.ValidationError("본인 담당 사례만 연결할 수 있습니다.")
        if not presentation_case_matches_cohort(case, self.cohort, author=self.author):
            raise forms.ValidationError("게시글과 같은 수련 기수의 사례만 연결할 수 있습니다.")
        return case


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
    case = forms.ModelChoiceField(
        label="담당 사례",
        queryset=Case.objects.none(),
        required=True,
        empty_label="담당 사례를 선택해 주세요",
        widget=forms.Select(
            attrs={"class": "form-select", "id": "presentationPostCase"}
        ),
    )

    def __init__(self, *args, author_name: str = "", author=None, cohort=None, **kwargs):
        self.author_name = author_name
        self.author = author
        self.cohort = cohort
        super().__init__(*args, **kwargs)
        if author_name:
            self.fields["title"].initial = default_presentation_post_title(author_name)
        case_field = self.fields["case"]
        case_field.queryset = counselor_case_queryset(author, cohort=cohort)
        case_field.label_from_instance = case_report_choice_label

    def clean_case(self):
        case = self.cleaned_data.get("case")
        if case is None or self.author is None:
            return case
        if case.counselor_id != self.author.pk:
            raise forms.ValidationError("본인 담당 사례만 연결할 수 있습니다.")
        if not presentation_case_matches_cohort(case, self.cohort, author=self.author):
            raise forms.ValidationError("게시글과 같은 수련 기수의 사례만 연결할 수 있습니다.")
        return case

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
    """사례발표 게시판 — 사례개념화보고서 댓글 (간단 코멘트 + 파일)."""

    ALLOWED_EXTENSIONS = PRESENTATION_COMMENT_ALLOWED_EXTENSIONS
    MAX_FILE_SIZE = PresentationBoardPostForm.MAX_FILE_SIZE
    ACCEPT_ATTR = PRESENTATION_COMMENT_ACCEPT_ATTR
    INVALID_TYPE_MESSAGE = PRESENTATION_COMMENT_INVALID_TYPE_MESSAGE
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
        label=PRESENTATION_COMMENT_ATTACHMENT_LABEL,
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
                "코멘트 또는 첨부 파일 중 하나는 입력해 주세요."
            )
        cleaned["content"] = content
        return cleaned


class PresentationBoardCommentEditForm(forms.Form):
    """사례발표 게시판 — 사례개념화보고서 댓글 수정."""

    ALLOWED_EXTENSIONS = PresentationBoardCommentForm.ALLOWED_EXTENSIONS
    MAX_FILE_SIZE = PresentationBoardCommentForm.MAX_FILE_SIZE
    ACCEPT_ATTR = PresentationBoardCommentForm.ACCEPT_ATTR
    INVALID_TYPE_MESSAGE = PresentationBoardCommentForm.INVALID_TYPE_MESSAGE
    MAX_SIZE_MESSAGE = PresentationBoardCommentForm.MAX_SIZE_MESSAGE

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
        label=PRESENTATION_COMMENT_ATTACHMENT_LABEL,
        required=False,
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": ACCEPT_ATTR}
        ),
    )
    remove_file = forms.BooleanField(
        label="기존 첨부 파일 삭제",
        required=False,
    )

    def __init__(self, *args, has_existing_file=False, **kwargs):
        self.has_existing_file = has_existing_file
        super().__init__(*args, **kwargs)
        if not has_existing_file:
            del self.fields["remove_file"]

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
        remove_file = cleaned.get("remove_file", False)
        keeps_existing_file = self.has_existing_file and not remove_file
        if not content and not file_obj and not keeps_existing_file:
            raise forms.ValidationError(
                "코멘트 또는 첨부 파일 중 하나는 입력해 주세요."
            )
        cleaned["content"] = content
        return cleaned

