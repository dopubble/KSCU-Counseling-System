from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.decorators import role_required
from apps.accounts.models import UserRole
from apps.counseling.models import ApplicationStatus, Case, CaseStatus, CounselingApplication
from apps.counseling.services import get_available_counselors, get_counselor_active_case_counts
from apps.counseling.services import count_cancel_pending_appointments
from apps.scheduling.models import Appointment, AppointmentStatus


def health_check(request):
    return JsonResponse({"status": "ok"})


def _month_start(now=None):
    now = now or timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _waiting_match_queryset():
    """접수(RECEIVED)·매칭대기(WAITING_MATCH) — 상담사 배정 전 신규 신청 포함"""
    return CounselingApplication.objects.waiting_for_match().select_related("client")


def build_admin_dashboard_stats(now=None):
    """관리자 대시보드·메인 홈 위젯용 통계."""
    now = now or timezone.now()
    month_start = _month_start(now)
    waiting_qs = _waiting_match_queryset()
    cancel_pending_count = count_cancel_pending_appointments()
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
    return stats, cancel_pending_count


@role_required(UserRole.ADMIN)
def admin_dashboard(request):
    now = timezone.now()
    waiting_qs = _waiting_match_queryset()

    stats, cancel_pending_count = build_admin_dashboard_stats(now)

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
        _waiting_match_queryset()
        .select_related("client", "case", "case__counselor")
        .order_by("-created_at")
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
    queryset = (
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
