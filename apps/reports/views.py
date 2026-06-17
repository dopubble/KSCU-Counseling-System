from django.db.models import Count
from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.decorators import role_required
from apps.accounts.models import CounselorProfile, User, UserRole
from apps.counseling.application_queries import (
    annotate_pending_application_flags,
    waiting_match_for_admin,
)
from apps.counseling.models import ApplicationStatus, Case, CaseStatus, CounselingApplication
from apps.counseling.services import get_available_counselors, get_counselor_active_case_counts
from apps.counseling.services import count_cancel_pending_appointments
from apps.reports.table_sort import (
    ACTIVE_CASE_SORT_SPECS,
    CANCEL_PENDING_DEFAULT,
    CANCEL_PENDING_SORT_SPECS,
    CLOSED_CASE_SORT_SPECS,
    MATCHING_DEFAULT,
    MATCHING_SORT_SPECS,
    TAB_SORT_DEFAULTS,
    WAITING_SORT_SPECS,
    allowed_sort_keys,
    parse_sort,
    sort_queryset,
)
from apps.reports.appointment_calendar import (
    HOST_COLORS,
    build_calendar_events,
    get_calendar_timezone_name,
    get_mock_calendar_events,
    get_zoom_host_pool,
    parse_calendar_bound,
    zoom_host_label,
)
from apps.scheduling.models import Appointment, AppointmentStatus


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


def platform_user_manual(request):
    """플랫폼 이용 매뉴얼 (단일 HTML) — 공개."""
    manual_path = settings.BASE_DIR / "docs" / "platform-user-manual.html"
    if not manual_path.is_file():
        raise Http404("매뉴얼 파일을 찾을 수 없습니다.")
    return FileResponse(
        manual_path.open("rb"),
        content_type="text/html; charset=utf-8",
    )


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
            status=AppointmentStatus.CONFIRMED,
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

    today_appointments = (
        Appointment.objects.filter(
            scheduled_at__date=now.date(),
            status=AppointmentStatus.CONFIRMED,
        )
        .select_related("client", "counselor")
        .order_by("scheduled_at")[:10]
    )

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

    default_field, default_dir = TAB_SORT_DEFAULTS[active_tab]
    if active_tab == "waiting":
        sort_specs = WAITING_SORT_SPECS
    elif active_tab == "active":
        sort_specs = ACTIVE_CASE_SORT_SPECS
    else:
        sort_specs = CLOSED_CASE_SORT_SPECS

    sort = parse_sort(
        request,
        allowed=allowed_sort_keys(sort_specs),
        default_field=default_field,
        default_direction=default_dir,
    )

    waiting_qs = _waiting_match_queryset()
    if active_tab == "waiting":
        waiting_applications = sort_queryset(waiting_qs, sort, WAITING_SORT_SPECS)
    else:
        waiting_applications = waiting_qs.order_by("-created_at")
    if not isinstance(waiting_applications, list):
        waiting_applications = list(waiting_applications)

    active_qs = Case.objects.filter(
        status=CaseStatus.ACTIVE,
        counselor__isnull=False,
    ).select_related("client", "counselor", "application")
    if active_tab == "active":
        active_cases = sort_queryset(active_qs, sort, ACTIVE_CASE_SORT_SPECS)
    else:
        active_cases = active_qs.order_by("-opened_at")
    if not isinstance(active_cases, list):
        active_cases = list(active_cases)

    closed_qs = Case.objects.filter(status=CaseStatus.CLOSED).select_related(
        "client", "counselor", "application"
    )
    if active_tab == "closed":
        closed_cases = sort_queryset(closed_qs, sort, CLOSED_CASE_SORT_SPECS)
    else:
        closed_cases = closed_qs.order_by("-closed_at", "-opened_at")
    if not isinstance(closed_cases, list):
        closed_cases = list(closed_cases)

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
            "sort_field": sort.field,
            "sort_dir": sort.direction,
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
    queryset = annotate_pending_application_flags(
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

    default_field, default_dir = MATCHING_DEFAULT
    sort = parse_sort(
        request,
        allowed=allowed_sort_keys(MATCHING_SORT_SPECS),
        default_field=default_field,
        default_direction=default_dir,
    )
    applications = sort_queryset(queryset, sort, MATCHING_SORT_SPECS)
    applications_count = (
        len(applications) if isinstance(applications, list) else applications.count()
    )

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
            "applications": applications,
            "applications_count": applications_count,
            "page_title": "상담사 매칭",
            "filter": filter_key,
            "filter_label": filter_labels.get(filter_key, filter_labels["all"]),
            "counselor_workload": counselor_workload,
            "sort_field": sort.field,
            "sort_dir": sort.direction,
        },
    )


@role_required(UserRole.ADMIN)
def cancel_pending_list(request):
    """취소 대기(CANCEL_PENDING) 예약 목록."""
    default_field, default_dir = CANCEL_PENDING_DEFAULT
    sort = parse_sort(
        request,
        allowed=allowed_sort_keys(CANCEL_PENDING_SORT_SPECS),
        default_field=default_field,
        default_direction=default_dir,
    )
    appointments_qs = Appointment.objects.filter(
        status=AppointmentStatus.CANCEL_PENDING
    ).select_related("client", "counselor", "case", "case__application")
    appointments = sort_queryset(appointments_qs, sort, CANCEL_PENDING_SORT_SPECS)
    if isinstance(appointments, list):
        appointments_count = len(appointments)
    else:
        appointments_count = appointments.count()
        appointments = list(appointments)
    return render(
        request,
        "admin_panel/cancel_pending_list.html",
        {
            "appointments": appointments,
            "appointments_count": appointments_count,
            "sort_field": sort.field,
            "sort_dir": sort.direction,
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


def _calendar_legend_hosts() -> list[dict[str, str]]:
    return [
        {
            "id": host_id,
            "label": zoom_host_label(host_id),
            "color": HOST_COLORS.get(host_id, {}).get("bg", "#4f46e5"),
        }
        for host_id in get_zoom_host_pool()
    ]


def _parse_calendar_range(request):
    start = parse_calendar_bound((request.GET.get("start") or "").strip())
    end = parse_calendar_bound((request.GET.get("end") or "").strip())
    return start, end


@role_required(UserRole.ADMIN)
def appointment_calendar(request):
    use_mock = request.GET.get("mock") == "1"
    return render(
        request,
        "admin_panel/appointment_calendar.html",
        {
            "use_mock": use_mock,
            "zoom_host_pool": _calendar_legend_hosts(),
            "calendar_timezone": get_calendar_timezone_name(),
        },
    )


@role_required(UserRole.ADMIN)
def appointment_calendar_events(request):
    if request.GET.get("mock") == "1":
        return JsonResponse({"events": get_mock_calendar_events()})

    start, end = _parse_calendar_range(request)
    events = build_calendar_events(start=start, end=end)
    return JsonResponse({"events": events})


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
