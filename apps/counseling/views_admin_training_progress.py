"""관리자 수련현황 통합 조회."""

from django.http import Http404
from django.shortcuts import get_object_or_404, render

from apps.accounts.decorators import role_required
from apps.accounts.models import CounselorProfile, User, UserRole
from apps.counseling.training_progress import (
    get_cohort_counselors_training_progress,
    get_counselor_training_progress,
)
from apps.reports.views import get_available_cohorts


def _parse_required_cohort(raw, cohorts: list[int]) -> int | None:
    """GET cohort. 없거나 잘못되면 최신 기수(내림차순 첫 값). 기수 목록이 비면 None."""
    if not cohorts:
        return None
    if raw in (None, ""):
        return cohorts[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return cohorts[0]
    if value <= 0:
        return cohorts[0]
    return value


def _list_display(progress: dict) -> dict:
    return {
        "intake_label": (
            f"{progress['intake_required_count']}/{progress['intake_completed_count']}건"
        ),
        "counseling_label": (
            f"{progress['counseling_assigned_count']}/"
            f"{progress['journal_completed_count']}시간"
        ),
        "termination_label": (
            f"{progress['termination_required_count']}/"
            f"{progress['termination_completed_count']}건"
        ),
        "supervision_label": (
            f"{progress['supervision_total_count']}/"
            f"{progress['supervision_attended_count']}회"
        ),
        "case_report_label": (
            f"{progress['case_report_submitted_count']}사례 완료"
        ),
        "public_presentation_label": (
            f"{progress['public_presentation_participation_count']}회"
        ),
        "training_time_label": progress["training_method_summary_label"],
        "supervision_attendance_percent": progress["supervision_attendance_percent"],
        "intake_done": (
            progress["intake_required_count"] > 0
            and progress["intake_completed_count"] >= progress["intake_required_count"]
        ),
        "counseling_done": (
            progress["counseling_assigned_count"] > 0
            and progress["journal_completed_count"]
            >= progress["counseling_assigned_count"]
        ),
        "termination_done": (
            progress["termination_required_count"] > 0
            and progress["termination_completed_count"]
            >= progress["termination_required_count"]
        ),
        "case_report_done": (
            progress["case_report_required_count"] > 0
            and progress["case_report_submitted_count"]
            >= progress["case_report_required_count"]
        ),
    }


@role_required(UserRole.ADMIN)
def admin_training_progress(request):
    cohorts = get_available_cohorts()
    selected_cohort = _parse_required_cohort(request.GET.get("cohort"), cohorts)
    rows = []
    if selected_cohort is not None:
        for entry in get_cohort_counselors_training_progress(selected_cohort):
            rows.append(
                {
                    "counselor": entry["counselor"],
                    "profile": entry["profile"],
                    "progress": entry["progress"],
                    "display": _list_display(entry["progress"]),
                }
            )

    return render(
        request,
        "admin_panel/training_progress.html",
        {
            "cohorts": cohorts,
            "selected_cohort": selected_cohort,
            "rows": rows,
            "counselors_count": len(rows),
        },
    )


@role_required(UserRole.ADMIN)
def admin_counselor_training_progress_detail(request, counselor_id):
    counselor = get_object_or_404(
        User.objects.select_related("counselor_profile"),
        pk=counselor_id,
        role=UserRole.COUNSELOR,
    )
    try:
        profile = counselor.counselor_profile
    except CounselorProfile.DoesNotExist as exc:
        raise Http404("상담사 프로필이 없습니다.") from exc

    progress = get_counselor_training_progress(counselor)
    cohort = profile.cohort
    return render(
        request,
        "admin_panel/counselor_training_progress_detail.html",
        {
            "counselor": counselor,
            "profile": profile,
            "cohort": cohort,
            "training_progress": progress,
            "list_cohort": cohort or request.GET.get("cohort") or "",
        },
    )
