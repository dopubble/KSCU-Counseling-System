"""수퍼비전 출석관리 — 수퍼바이저 담당 기수, 관리자 전체 기수."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import role_required, supervisor_required
from apps.accounts.models import UserRole
from apps.counseling.models import SupervisionSession
from apps.counseling.supervision_attendance import (
    SupervisionSessionForm,
    assert_cohort_access,
    attendance_cohorts_for_user,
    save_session_attendance,
    session_roster,
    user_can_edit_supervision_sessions,
)


def _render_cohorts(request, *, manage: bool):
    cohorts = attendance_cohorts_for_user(request.user)
    template = (
        "admin_panel/supervision_cohorts.html"
        if manage
        else "supervisor/supervision_cohorts.html"
    )
    return render(
        request,
        template,
        {
            "cohorts": cohorts,
            "can_manage_sessions": manage and user_can_edit_supervision_sessions(request.user),
        },
    )


def _render_sessions(request, cohort: int, *, manage: bool):
    assert_cohort_access(request.user, cohort)
    sessions = SupervisionSession.objects.filter(cohort=cohort).order_by(
        "session_number", "scheduled_at"
    )
    template = (
        "admin_panel/supervision_sessions.html"
        if manage
        else "supervisor/supervision_sessions.html"
    )
    return render(
        request,
        template,
        {
            "cohort": cohort,
            "sessions": sessions,
            "can_manage_sessions": manage and user_can_edit_supervision_sessions(request.user),
        },
    )


def _render_attendance(request, session: SupervisionSession, *, manage: bool):
    assert_cohort_access(request.user, session.cohort)
    if request.method == "POST":
        save_session_attendance(session, request.user, request.POST)
        messages.success(request, "출석을 저장했습니다.")
        return redirect(request.path)
    template = (
        "admin_panel/supervision_attendance.html"
        if manage
        else "supervisor/supervision_attendance.html"
    )
    return render(
        request,
        template,
        {
            "session": session,
            "roster": session_roster(session),
            "can_manage_sessions": manage and user_can_edit_supervision_sessions(request.user),
        },
    )


@supervisor_required
def supervisor_supervision_cohorts(request):
    return _render_cohorts(request, manage=False)


@supervisor_required
def supervisor_supervision_sessions(request, cohort: int):
    return _render_sessions(request, cohort, manage=False)


@supervisor_required
@require_http_methods(["GET", "POST"])
def supervisor_supervision_attendance(request, pk):
    session = get_object_or_404(SupervisionSession, pk=pk)
    return _render_attendance(request, session, manage=False)


@role_required(UserRole.ADMIN)
def admin_supervision_cohorts(request):
    return _render_cohorts(request, manage=True)


@role_required(UserRole.ADMIN)
def admin_supervision_sessions(request, cohort: int):
    return _render_sessions(request, cohort, manage=True)


@role_required(UserRole.ADMIN)
@require_http_methods(["GET", "POST"])
def admin_supervision_session_create(request, cohort: int):
    if not user_can_edit_supervision_sessions(request.user):
        raise PermissionDenied("수퍼비전 회차를 만들 권한이 없습니다.")
    assert_cohort_access(request.user, cohort)
    if request.method == "POST":
        form = SupervisionSessionForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "수퍼비전 회차를 만들었습니다.")
            return redirect("admin_panel:supervision_sessions", cohort=form.cleaned_data["cohort"])
    else:
        form = SupervisionSessionForm(initial={"cohort": cohort})
    return render(
        request,
        "admin_panel/supervision_session_form.html",
        {"form": form, "cohort": cohort, "is_edit": False},
    )


@role_required(UserRole.ADMIN)
@require_http_methods(["GET", "POST"])
def admin_supervision_session_edit(request, pk):
    if not user_can_edit_supervision_sessions(request.user):
        raise PermissionDenied("수퍼비전 회차를 수정할 권한이 없습니다.")
    session = get_object_or_404(SupervisionSession, pk=pk)
    assert_cohort_access(request.user, session.cohort)
    if request.method == "POST":
        form = SupervisionSessionForm(request.POST, instance=session)
        if form.is_valid():
            form.save()
            messages.success(request, "수퍼비전 회차를 저장했습니다.")
            return redirect(
                "admin_panel:supervision_sessions",
                cohort=form.cleaned_data["cohort"],
            )
    else:
        form = SupervisionSessionForm(instance=session)
    return render(
        request,
        "admin_panel/supervision_session_form.html",
        {"form": form, "cohort": session.cohort, "is_edit": True, "session": session},
    )


@role_required(UserRole.ADMIN)
@require_http_methods(["GET", "POST"])
def admin_supervision_attendance(request, pk):
    session = get_object_or_404(SupervisionSession, pk=pk)
    return _render_attendance(request, session, manage=True)
