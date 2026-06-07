from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse

from .models import CounselorProfile, UserRole, UserStatus


def role_required(*roles):
    """Allow access only for users with one of the given roles."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(
                    request.get_full_path(),
                    login_url=reverse("accounts:login"),
                )
            if not request.user.is_superuser and request.user.role not in roles:
                raise PermissionDenied("접근 권한이 없습니다.")
            if request.user.status != UserStatus.ACTIVE and not request.user.is_superuser:
                return redirect("accounts:pending")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def _has_counselor_profile(user):
    try:
        user.counselor_profile
        return True
    except CounselorProfile.DoesNotExist:
        return False


def user_can_access_counselor_area(user):
    """상담사 영역 접근 가능 여부"""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.role == UserRole.COUNSELOR:
        return True
    if _has_counselor_profile(user):
        return True
    return False


def counselor_required(view_func):
    """
    상담사 전용 뷰 접근 제어.
    - 미로그인: 로그인 페이지로 리다이렉트
    - COUNSELOR 역할, CounselorProfile 보유, is_superuser 허용
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(),
                login_url=reverse("accounts:login"),
            )

        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)

        if request.user.role == UserRole.COUNSELOR:
            if request.user.status != UserStatus.ACTIVE:
                return redirect("accounts:pending")
            return view_func(request, *args, **kwargs)

        if _has_counselor_profile(request.user):
            return view_func(request, *args, **kwargs)

        raise PermissionDenied("상담사 전용 페이지입니다.")

    return wrapper


def board_manager_required(view_func):
    """게시판 관리 — 담당 상담사·관리자·슈퍼유저."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(),
                login_url=reverse("accounts:login"),
            )

        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)

        if request.user.role == UserRole.ADMIN:
            if request.user.status != UserStatus.ACTIVE and not request.user.is_superuser:
                return redirect("accounts:pending")
            return view_func(request, *args, **kwargs)

        if request.user.role == UserRole.COUNSELOR:
            if request.user.status != UserStatus.ACTIVE:
                return redirect("accounts:pending")
            return view_func(request, *args, **kwargs)

        raise PermissionDenied("게시판 관리 권한이 없습니다.")

    return wrapper
