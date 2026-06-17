from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.contrib.auth.password_validation import validate_password

from .models import CounselorProfile, ClientProfile, User, UserRole, UserStatus


class SignUpForm(UserCreationForm):
    role = forms.ChoiceField(
        label="가입 유형",
        choices=[(UserRole.CLIENT, "내담자"), (UserRole.COUNSELOR, "상담사")],
        widget=forms.RadioSelect,
    )
    name = forms.CharField(label="이름", max_length=100)
    phone = forms.CharField(label="휴대폰", max_length=20, required=False)
    birth_date = forms.DateField(
        label="생년월일",
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
        ),
        input_formats=["%Y-%m-%d"],
    )
    is_kcu_student = forms.ChoiceField(
        label="숭실사이버대학교 학생 여부",
        choices=[("yes", "예"), ("no", "아니오")],
        widget=forms.RadioSelect,
        required=False,
    )
    student_id = forms.CharField(
        label="학번",
        max_length=20,
        required=False,
        help_text="선택 사항입니다. 재학생인 경우 입력해 주세요.",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "예: 20241234 (선택)"},
        ),
    )
    department = forms.CharField(
        label="소속 학과",
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "예: 상담심리학과",
                "id": "id_department",
            },
        ),
    )
    agree_terms = forms.BooleanField(
        label="이용약관 및 개인정보 처리방침에 동의합니다.",
        required=True,
    )

    class Meta:
        model = User
        fields = ("email", "name", "phone", "role", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("email", "name", "phone", "password1", "password2"):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("class", "form-control")

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"].strip())
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("이미 가입된 이메일입니다.")
        return email

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        if role != UserRole.CLIENT:
            cleaned["department"] = ""
            return cleaned

        if not cleaned.get("birth_date"):
            self.add_error("birth_date", "생년월일을 입력해 주세요.")

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
        else:
            cleaned["department"] = ""

        cleaned["student_id"] = (cleaned.get("student_id") or "").strip()
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.name = self.cleaned_data["name"]
        user.phone = self.cleaned_data.get("phone", "")
        user.role = self.cleaned_data["role"]
        user.status = (
            UserStatus.ACTIVE if user.role == UserRole.CLIENT else UserStatus.PENDING
        )
        if commit:
            user.save()
            if user.role == UserRole.CLIENT:
                is_kcu = self.cleaned_data.get("is_kcu_student") == "yes"
                ClientProfile.objects.create(
                    user=user,
                    birth_date=self.cleaned_data["birth_date"],
                    is_kcu_student=is_kcu,
                    student_id=self.cleaned_data.get("student_id", ""),
                    department=self.cleaned_data.get("department", ""),
                )
        return user


class FindAccountIdForm(forms.Form):
    """아이디(이메일) 찾기 — 이름 + 가입 이메일 일치 시 안내"""

    name = forms.CharField(
        label="이름",
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "name"}),
    )
    email = forms.EmailField(
        label="가입 시 등록한 이메일",
        widget=forms.EmailInput(attrs={"class": "form-control", "autocomplete": "email"}),
    )


class KoreanPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        label="가입 시 등록한 이메일",
        max_length=254,
        widget=forms.EmailInput(attrs={"class": "form-control", "autocomplete": "email"}),
    )


class KoreanSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class KoreanPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = "현재 비밀번호"
        self.fields["new_password1"].label = "새 비밀번호"
        self.fields["new_password2"].label = "새 비밀번호 확인"
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
            if field.name == "old_password":
                field.widget.attrs.setdefault("autocomplete", "current-password")
            elif field.name.startswith("new_password"):
                field.widget.attrs.setdefault("autocomplete", "new-password")


class OptionalPasswordChangeFieldsForm(forms.Form):
    """내정보 수정 — 비밀번호는 입력한 경우에만 변경."""

    old_password = forms.CharField(
        label="현재 비밀번호",
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "autocomplete": "current-password"},
        ),
    )
    new_password1 = forms.CharField(
        label="새 비밀번호",
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "autocomplete": "new-password"},
        ),
        help_text="변경하지 않으려면 비워 두세요.",
    )
    new_password2 = forms.CharField(
        label="새 비밀번호 확인",
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "autocomplete": "new-password"},
        ),
    )

    def _validate_optional_password_change(self) -> str | None:
        old = (self.cleaned_data.get("old_password") or "").strip()
        new1 = (self.cleaned_data.get("new_password1") or "").strip()
        new2 = (self.cleaned_data.get("new_password2") or "").strip()

        if not any([old, new1, new2]):
            return None

        if not old:
            self.add_error("old_password", "비밀번호를 변경하려면 현재 비밀번호를 입력해 주세요.")
            return None
        if not self.user.check_password(old):
            self.add_error("old_password", "현재 비밀번호가 올바르지 않습니다.")
            return None
        if not new1:
            self.add_error("new_password1", "새 비밀번호를 입력해 주세요.")
            return None
        if new1 != new2:
            self.add_error("new_password2", "새 비밀번호가 일치하지 않습니다.")
            return None

        validate_password(new1, self.user)
        return new1


class ProfileUpdateForm(OptionalPasswordChangeFieldsForm):
    """내담자 내정보 수정 — 이메일·연락처·비밀번호 변경 가능."""

    name = forms.CharField(
        label="이름",
        max_length=100,
        disabled=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "autocomplete": "name",
                "readonly": "readonly",
            },
        ),
    )
    is_kcu_student_display = forms.CharField(
        label="숭실사이버대학교 학생 여부",
        required=False,
        disabled=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "readonly": "readonly"},
        ),
    )
    student_id = forms.CharField(
        label="학번",
        max_length=20,
        required=False,
        disabled=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "readonly": "readonly",
            },
        ),
    )
    birth_date = forms.DateField(
        label="생년월일",
        required=False,
        disabled=True,
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date", "readonly": "readonly"},
        ),
    )
    department = forms.CharField(
        label="소속 학과",
        max_length=100,
        required=False,
        disabled=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "readonly": "readonly"},
        ),
    )
    email = forms.EmailField(
        label="이메일 (로그인 아이디)",
        widget=forms.EmailInput(
            attrs={"class": "form-control", "autocomplete": "email"},
        ),
    )
    phone = forms.CharField(
        label="휴대폰 번호",
        max_length=20,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "autocomplete": "tel"},
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user is not None:
            try:
                profile = user.client_profile
            except ClientProfile.DoesNotExist:
                profile = None
            self.fields["name"].initial = user.name
            self.fields["email"].initial = user.email
            self.fields["phone"].initial = user.phone or ""
            self.fields["student_id"].initial = (
                profile.student_id if profile is not None else ""
            )
            if profile is not None and profile.birth_date:
                self.fields["birth_date"].initial = profile.birth_date
            self.fields["department"].initial = (
                profile.department if profile is not None else ""
            )
            if profile is not None:
                self.fields["is_kcu_student_display"].initial = (
                    "예" if profile.is_kcu_student else "아니오"
                )

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        self._new_password = self._validate_optional_password_change()
        return cleaned

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"].strip())
        exists = User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists()
        if exists:
            raise forms.ValidationError("이미 사용 중인 이메일입니다.")
        return email

    @property
    def new_password(self) -> str | None:
        return getattr(self, "_new_password", None)


class CounselorProfileUpdateForm(OptionalPasswordChangeFieldsForm):
    """상담사 내정보 수정 — 이메일·연락처·비밀번호 변경 가능."""

    role_display = forms.CharField(
        label="가입 유형",
        disabled=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "readonly": "readonly"},
        ),
    )
    name = forms.CharField(
        label="이름",
        max_length=100,
        disabled=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "autocomplete": "name",
                "readonly": "readonly",
            },
        ),
    )
    birth_date = forms.DateField(
        label="생년월일",
        required=False,
        disabled=True,
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date", "readonly": "readonly"},
        ),
    )
    gender = forms.CharField(
        label="성별",
        required=False,
        disabled=True,
        widget=forms.TextInput(
            attrs={"class": "form-control", "readonly": "readonly"},
        ),
    )
    email = forms.EmailField(
        label="이메일 (로그인 아이디)",
        widget=forms.EmailInput(
            attrs={"class": "form-control", "autocomplete": "email"},
        ),
    )
    phone = forms.CharField(
        label="휴대폰 번호",
        max_length=20,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "autocomplete": "tel"},
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["role_display"].initial = user.get_role_display()
            self.fields["name"].initial = user.name
            self.fields["email"].initial = user.email
            self.fields["phone"].initial = user.phone or ""
            try:
                profile = user.counselor_profile
            except CounselorProfile.DoesNotExist:
                profile = None
            if profile is not None:
                if profile.birth_date:
                    self.fields["birth_date"].initial = profile.birth_date
                self.fields["gender"].initial = profile.gender or ""

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        self._new_password = self._validate_optional_password_change()
        return cleaned

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"].strip())
        exists = User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists()
        if exists:
            raise forms.ValidationError("이미 사용 중인 이메일입니다.")
        return email

    @property
    def new_password(self) -> str | None:
        return getattr(self, "_new_password", None)


# 하위 호환
ClientProfileUpdateForm = ProfileUpdateForm


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="이메일")

    def clean(self):
        email = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if email and password:
            self.user_cache = authenticate(self.request, username=email, password=password)
            if self.user_cache is None:
                raise forms.ValidationError("이메일 또는 비밀번호가 올바르지 않습니다.")
            if self.user_cache.status == UserStatus.SUSPENDED:
                raise forms.ValidationError("정지된 계정입니다. 관리자에게 문의하세요.")
        return self.cleaned_data
