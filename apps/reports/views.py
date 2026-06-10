from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.decorators import role_required
from apps.accounts.models import CounselorProfile, User, UserRole
from apps.counseling.application_queries import (
    exclude_stale_pending_applications,
    waiting_match_for_admin,
)
from apps.counseling.models import ApplicationStatus, Case, CaseStatus, CounselingApplication
from apps.counseling.services import get_available_counselors, get_counselor_active_case_counts
from apps.counseling.services import count_cancel_pending_appointments
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.documents.models import CounselorAssignmentSubmission


def _parse_cohort_param(raw) -> int | None:
    if raw in (None, "", "all"):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def get_available_cohorts():
    """등록된 기수 목록 (내림차순)."""
    return list(
        CounselorProfile.objects.exclude(cohort__isnull=True)
        .values_list("cohort", flat=True)
        .distinct()
        .order_by("-cohort")
    )


def _csv_response(filename: str, header: list[str], rows: list[list]) -> HttpResponse:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    response = HttpResponse(
        "\ufeff" + buffer.getvalue(),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def health_check(request):
    return JsonResponse({"status": "ok"})


def _month_start(now=None):
    now = now or timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _waiting_match_queryset():
    """접수·매칭대기 — 이미 배정된 내담자의 중복 신청은 제외."""
    return waiting_match_for_admin()


def build_admin_dashboard_stats(now=None, *, use_cache: bool = True):
    """관리자 대시보드·메인 홈 위젯용 통계."""
    from apps.reports.cache_utils import safe_cache_get, safe_cache_set

    cache_key = "kscu:admin_dashboard_stats"
    if use_cache:
        cached = safe_cache_get(cache_key)
        if cached is not None:
            return cached

    now = now or timezone.now()
    month_start = _month_start(now)
    waiting_qs = _waiting_match_queryset()
    cancel_pending_count = count_cancel_pending_appointments(use_cache=use_cache)
    stats = {
        "applications_this_month": CounselingApplication.objects.filter(
            created_at__gte=month_start
        ).count(),
        "waiting_match": waiting_qs.count(),
        "active_cases": Case.objects.filter(status=CaseStatus.ACTIVE).count(),
        "appointments_today": Appointment.objects.filter(
            scheduled_at__date=now.date(),
            status__in=[AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED],
        ).count(),
        "cancel_pending": cancel_pending_count,
    }
    result = (stats, cancel_pending_count)
    if use_cache:
        safe_cache_set(cache_key, result, 60)
    return result


@role_required(UserRole.ADMIN)
def admin_dashboard(request):
    now = timezone.now()
    waiting_qs = _waiting_match_queryset()

    stats, cancel_pending_count = build_admin_dashboard_stats(now, use_cache=False)

    waiting_applications = waiting_qs.order_by("-created_at")[:10]

    today_appointments = Appointment.objects.filter(
        scheduled_at__date=now.date(),
    ).select_related("client", "counselor").order_by("scheduled_at")[:10]

    return render(
        request,
        "admin_panel/dashboard.html",
        {
            "stats": stats,
            "waiting_applications": waiting_applications,
            "today_appointments": today_appointments,
            "cancel_pending_count": cancel_pending_count,
        },
    )


_COUNSELING_MGMT_TABS = frozenset({"waiting", "active", "closed"})


@role_required(UserRole.ADMIN)
def counseling_management(request):
    """상담 통합 관리 — 신규 신청 / 진행 중 / 종결 사례 탭."""
    active_tab = request.GET.get("tab", "waiting")
    if active_tab not in _COUNSELING_MGMT_TABS:
        active_tab = "waiting"

    waiting_applications = list(
        _waiting_match_queryset().order_by("-created_at")
    )
    active_cases = list(
        Case.objects.filter(
            status=CaseStatus.ACTIVE,
            counselor__isnull=False,
        )
        .select_related("client", "counselor", "application")
        .order_by("-opened_at")
    )
    closed_cases = list(
        Case.objects.filter(status=CaseStatus.CLOSED)
        .select_related("client", "counselor", "application")
        .order_by("-closed_at", "-opened_at")
    )

    return render(
        request,
        "admin_panel/counseling_management.html",
        {
            "active_tab": active_tab,
            "waiting_applications": waiting_applications,
            "active_cases": active_cases,
            "closed_cases": closed_cases,
            "waiting_count": len(waiting_applications),
            "active_count": len(active_cases),
            "closed_count": len(closed_cases),
        },
    )


@role_required(UserRole.ADMIN)
def application_list(request):
    """이전 URL 호환 — 상담 통합 관리(신규 신청 탭)로 이동."""
    tab = "waiting"
    filter_key = request.GET.get("filter", "")
    if filter_key == "month":
        tab = "waiting"
    return redirect(f"{reverse('admin_panel:counseling_management')}?tab={tab}")


@role_required(UserRole.ADMIN)
def case_list(request):
    """이전 URL 호환 — 상담 통합 관리로 이동."""
    filter_key = request.GET.get("filter", "active")
    tab = "closed" if filter_key == "all" else "active"
    return redirect(f"{reverse('admin_panel:counseling_management')}?tab={tab}")


@role_required(UserRole.ADMIN)
def matching_list(request):
    """내담자·상담 신청 매칭 관리 (상담사 배정·변경)"""
    filter_key = request.GET.get("filter", "all")
    queryset = exclude_stale_pending_applications(
        CounselingApplication.objects.select_related("client")
        .select_related("case", "case__counselor")
        .order_by("-created_at")
    )

    if filter_key == "waiting":
        queryset = queryset.waiting_for_match()
    elif filter_key == "unassigned":
        queryset = queryset.filter(
            case__isnull=True,
            status__in=[
                ApplicationStatus.RECEIVED,
                ApplicationStatus.WAITING_MATCH,
            ],
        )
    elif filter_key == "assigned":
        queryset = queryset.filter(case__counselor__isnull=False)

    counselors = get_available_counselors()
    active_case_counts = get_counselor_active_case_counts()
    counselor_workload = [
        {
            "profile": profile,
            "active_cases": active_case_counts.get(profile.user_id, 0),
        }
        for profile in counselors
    ]

    filter_labels = {
        "all": "전체 내담자",
        "waiting": "매칭 대기",
        "unassigned": "미배정",
        "assigned": "배정 완료",
    }

    return render(
        request,
        "admin_panel/matching_list.html",
        {
            "applications": queryset,
            "applications_count": queryset.count(),
            "page_title": "상담사 매칭",
            "filter": filter_key,
            "filter_label": filter_labels.get(filter_key, filter_labels["all"]),
            "counselor_workload": counselor_workload,
        },
    )


@role_required(UserRole.ADMIN)
def cancel_pending_list(request):
    """취소 대기(CANCEL_PENDING) 예약 목록."""
    appointments = (
        Appointment.objects.filter(status=AppointmentStatus.CANCEL_PENDING)
        .select_related("client", "counselor", "case", "case__application")
        .order_by("-cancel_requested_at", "-updated_at")
    )
    return render(
        request,
        "admin_panel/cancel_pending_list.html",
        {
            "appointments": appointments,
            "appointments_count": appointments.count(),
        },
    )


@role_required(UserRole.ADMIN)
def counselor_list(request):
    """기수별 상담사 명단."""
    cohorts = get_available_cohorts()
    selected_cohort = _parse_cohort_param(request.GET.get("cohort"))

    profiles = (
        CounselorProfile.objects.select_related("user")
        .filter(user__role=UserRole.COUNSELOR)
        .order_by("cohort", "user__name")
    )
    if selected_cohort is not None:
        profiles = profiles.filter(cohort=selected_cohort)

    profiles = list(profiles)

    if request.GET.get("export") == "csv":
        rows = [
            [
                p.user.name,
                p.user.email,
                p.user.phone or "",
                p.cohort or "",
                "Y" if p.is_approved else "N",
                p.user.get_status_display(),
                p.user.created_at.strftime("%Y-%m-%d"),
            ]
            for p in profiles
        ]
        suffix = f"_{selected_cohort}기" if selected_cohort else "_전체"
        return _csv_response(
            f"상담사_명단{suffix}.csv",
            ["이름", "이메일", "휴대폰", "기수", "승인", "계정상태", "가입일"],
            rows,
        )

    return render(
        request,
        "admin_panel/counselor_list.html",
        {
            "counselors": profiles,
            "counselors_count": len(profiles),
            "cohorts": cohorts,
            "selected_cohort": selected_cohort,
        },
    )


@role_required(UserRole.ADMIN)
def counselor_assignment_list(request):
    """기수별 과제 검수 대시보드 — 사례·회차별 최종본."""
    cohorts = get_available_cohorts()
    selected_cohort = _parse_cohort_param(request.GET.get("cohort"))

    assignments = (
        CounselorAssignmentSubmission.objects.select_related(
            "case",
            "case__client",
            "case__counselor",
            "submitted_by",
        )
        .order_by("cohort", "case__case_number", "session_number")
    )
    if selected_cohort is not None:
        assignments = assignments.filter(cohort=selected_cohort)

    assignments = list(assignments)

    if request.GET.get("export") == "csv":
        rows = [
            [
                a.case.case_number,
                a.case.client.name,
                a.submitted_by.name,
                a.cohort or "",
                a.session_number,
                a.updated_at.strftime("%Y-%m-%d %H:%M"),
                a.get_filename(),
            ]
            for a in assignments
        ]
        suffix = f"_{selected_cohort}기" if selected_cohort else "_전체"
        return _csv_response(
            f"과제_제출{suffix}.csv",
            ["사례번호", "내담자", "상담사", "기수", "회차", "최종제출일", "파일명"],
            rows,
        )

    return render(
        request,
        "admin_panel/counselor_assignment_list.html",
        {
            "assignments": assignments,
            "assignments_count": len(assignments),
            "cohorts": cohorts,
            "selected_cohort": selected_cohort,
        },
    )


@role_required(UserRole.ADMIN)
def admin_assignment_file(request, assignment_pk):
    """과제 파일 다운로드 (관리자)."""
    assignment = get_object_or_404(
        CounselorAssignmentSubmission.objects.select_related("case"),
        pk=assignment_pk,
    )
    if not assignment.file:
        raise Http404("File not found")
    return FileResponse(
        assignment.file.open("rb"),
        as_attachment=True,
        filename=assignment.get_filename(),
    )


@role_required(UserRole.ADMIN)
def statistics(request):
    type_counts: dict[str, int] = {}
    for types in CounselingApplication.objects.values_list("counseling_types", flat=True):
        for counseling_type in types or []:
            type_counts[counseling_type] = type_counts.get(counseling_type, 0) + 1
    type_distribution = [
        {"counseling_type": key, "count": value}
        for key, value in sorted(type_counts.items(), key=lambda item: -item[1])
    ]
    status_distribution = (
        Case.objects.values("status")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return render(
        request,
        "admin_panel/statistics.html",
        {
            "type_distribution": type_distribution,
            "status_distribution": status_distribution,
        },
    )
