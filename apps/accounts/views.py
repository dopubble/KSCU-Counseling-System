from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout as auth_logout
from django.contrib.auth.views import (
    LoginView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .auth_utils import get_safe_next_url
from .emailing import send_find_id_email
from apps.accounts.decorators import role_required

from .forms import (
    EmailAuthenticationForm,
    ProfileUpdateForm,
    FindAccountIdForm,
    KoreanPasswordResetForm,
    KoreanSetPasswordForm,
    SignUpForm,
)
from apps.counseling.services import get_client_home_dashboard
from apps.reports.views import build_admin_dashboard_stats

from .models import ClientProfile, User, UserRole, UserStatus


def _show_admin_home_widget(user: User) -> bool:
    """메인 화면 관리자 위젯 — is_staff·is_superuser·ADMIN 역할에게만 표시."""
    if not user.is_authenticated:
        return False
    return user.is_staff or user.is_superuser or user.role == UserRole.ADMIN


def home(request):
    """메인 화면 — 모든 방문자에게 상담센터 소개 페이지 표시"""
    context = {}
    if request.user.is_authenticated and request.user.role == UserRole.CLIENT:
        context["client_dashboard"] = get_client_home_dashboard(request.user)
    if _show_admin_home_widget(request.user):
        stats, cancel_pending_count = build_admin_dashboard_stats()
        context["admin_home"] = {
            "stats": stats,
            "cancel_pending_count": cancel_pending_count,
        }
    return render(request, "home.html", context)


class UserLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        redirect_to = self.get_redirect_url()
        if redirect_to:
            return redirect_to

        user = self.request.user
        if user.role == UserRole.ADMIN:
            return reverse_lazy("admin_panel:dashboard")
        if user.role == UserRole.COUNSELOR:
            return reverse_lazy("counselor:dashboard")
        if user.role == UserRole.CLIENT:
            return reverse_lazy("client:dashboard")
        return reverse_lazy("home")


@never_cache
@require_http_methods(["GET", "POST"])
def logout_view(request):
    """
    로그아웃 후 메인 홈(/)으로 이동.
    Django 5 LogoutView는 POST만 허용하므로 GET 링크 호환을 위해 별도 뷰 사용.
    """
    auth_logout(request)
    request.session.flush()

    messages.success(request, "로그아웃되었습니다.")

    response = redirect("home")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def signup(request):
    next_url = get_safe_next_url(request)

    if request.user.is_authenticated:
        if next_url:
            return redirect(next_url)
        return redirect("home")

    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            if user.role == UserRole.CLIENT:
                login(request, user)
                messages.success(request, "회원가입이 완료되었습니다.")
                if next_url:
                    return redirect(next_url)
                return redirect("client:dashboard")
            messages.info(request, "회원가입이 완료되었습니다. 관리자 승인 후 이용 가능합니다.")
            login_next = f"?next={next_url}" if next_url else ""
            return redirect(f"{reverse('accounts:login')}{login_next}")
    else:
        form = SignUpForm()

    return render(
        request,
        "accounts/signup.html",
        {"form": form, "next_url": next_url},
    )


def pending(request):
    if request.user.is_authenticated and request.user.status == UserStatus.ACTIVE:
        return redirect("home")
    return render(request, "accounts/pending.html")


def _profile_immutable_fields_tampered(request, user, profile) -> bool:
    """HTML/POST 조작으로 이름·학번 변경을 시도했는지 검사."""
    if "name" in request.POST and request.POST.get("name", "") != user.name:
        return True
    current_student_id = profile.student_id or ""
    if "student_id" in request.POST and request.POST.get("student_id", "") != current_student_id:
        return True
    return False


def _save_client_profile_contact(user, *, email: str, phone: str) -> None:
    """이메일·연락처만 저장 (name·student_id는 갱신하지 않음)."""
    user.email = email
    user.phone = phone or ""
    user.save(update_fields=["email", "phone", "updated_at"])


@role_required(UserRole.CLIENT)
def profile_update(request):
    """내담자 회원정보 수정 (이메일·연락처만)."""
    user = request.user
    profile, _ = ClientProfile.objects.get_or_create(user=user)

    if request.method == "POST":
        if _profile_immutable_fields_tampered(request, user, profile):
            messages.error(request, "이름·학번은 변경할 수 없습니다.")
            return redirect("accounts:profile_update")

        form = ProfileUpdateForm(request.POST, user=user)
        if form.is_valid():
            _save_client_profile_contact(
                user,
                email=form.cleaned_data["email"],
                phone=form.cleaned_data.get("phone", ""),
            )

            messages.success(request, "회원정보가 저장되었습니다.")
            return redirect("client:dashboard")
        messages.error(request, "입력 내용을 확인해 주세요.")
    else:
        form = ProfileUpdateForm(user=user)

    return render(
        request,
        "accounts/profile_update.html",
        {"form": form},
    )


class AccountPasswordResetView(PasswordResetView):
    """비밀번호 재설정 링크 이메일 발송"""

    form_class = KoreanPasswordResetForm
    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/email/password_reset_email.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")
    html_email_template_name = "accounts/email/password_reset_email.html"


class AccountPasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class AccountPasswordResetConfirmView(PasswordResetConfirmView):
    form_class = KoreanSetPasswordForm
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class AccountPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


@never_cache
@require_http_methods(["GET", "POST"])
def find_id(request):
    """
    아이디(이메일) 찾기.
    이름과 가입 이메일이 일치하면 화면에 표시하고 등록 메일로도 발송합니다.
    """
    found_email = None
    email_sent = False

    if request.method == "POST":
        form = FindAccountIdForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data["name"].strip()
            email = form.cleaned_data["email"].strip().lower()
            user = User.objects.filter(name=name, email__iexact=email).first()
            if user:
                found_email = user.email
                email_sent = send_find_id_email(user=user)
                if email_sent:
                    messages.success(
                        request,
                        "로그인 아이디를 화면에 표시했으며, 등록된 이메일로도 발송했습니다.",
                    )
                else:
                    messages.warning(
                        request,
                        "로그인 아이디를 확인할 수 있습니다. "
                        "이메일 발송은 설정을 확인한 뒤 다시 시도해 주세요.",
                    )
            else:
                messages.error(
                    request,
                    "입력하신 이름과 이메일과 일치하는 계정을 찾을 수 없습니다.",
                )
    else:
        form = FindAccountIdForm()

    return render(
        request,
        "accounts/find_id.html",
        {
            "form": form,
            "found_email": found_email,
            "email_sent": email_sent,
            "smtp_configured": bool(settings.EMAIL_HOST and settings.EMAIL_HOST_USER),
        },
    )
