from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)

from .models import CounselorProfile, ClientProfile, User, UserRole, UserStatus


class SignUpForm(UserCreationForm):
    role = forms.ChoiceField(
        label="가입 유형",
        choices=[(UserRole.CLIENT, "내담자"), (UserRole.COUNSELOR, "상담사")],
        widget=forms.RadioSelect,
    )
    name = forms.CharField(label="이름", max_length=100)
    phone = forms.CharField(label="휴대폰", max_length=20, required=False)
    agree_terms = forms.BooleanField(label="이용약관 및 개인정보 처리방침에 동의합니다.", required=True)

    class Meta:
        model = User
        fields = ("email", "name", "phone", "role", "password1", "password2")

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
            if user.role == UserRole.COUNSELOR:
                CounselorProfile.objects.create(user=user)
            elif user.role == UserRole.CLIENT:
                ClientProfile.objects.create(user=user)
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


class ProfileUpdateForm(forms.Form):
    """내담자 회원정보 수정 — 이메일·연락처만 저장 가능."""

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
    email = forms.EmailField(
        label="이메일 (로그인 아이디)",
        widget=forms.EmailInput(
            attrs={"class": "form-control", "autocomplete": "email"},
        ),
    )
    phone = forms.CharField(
        label="연락처",
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

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"].strip())
        exists = User.objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists()
        if exists:
            raise forms.ValidationError("이미 사용 중인 이메일입니다.")
        return email


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
