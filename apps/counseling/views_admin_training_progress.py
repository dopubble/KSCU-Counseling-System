"""관리자 수련현황 통합 조회. 목록과 상세의 기준키는 TrainingEnrollment다."""

from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.decorators import role_required
from apps.accounts.models import CounselorProfile, User, UserRole
from apps.counseling.models import TrainingEnrollment
from apps.counseling.training_certificate import get_training_certificate_approval_status
from apps.counseling.training_enrollment import (
    resolve_legacy_counselor_training_enrollment,
    training_enrollment_cohorts,
)
from apps.counseling.training_progress import (
    get_cohort_counselors_training_progress,
    get_counselor_training_progress,
)


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
        "training_time_label": progress["training_completed_label"],
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


def legacy_training_enrollment_or_404(counselor):
    enrollment = resolve_legacy_counselor_training_enrollment(counselor)
    if enrollment is None:
        raise Http404("수련 기수를 찾을 수 없습니다.")
    return enrollment


def redirect_to_enrollment_training_progress(enrollment, *, url_name, request):
    target = reverse(url_name, kwargs={"enrollment_id": enrollment.pk})
    cohort = request.GET.get("cohort")
    if cohort:
        return redirect(f"{target}?cohort={cohort}")
    return redirect(target)


def render_enrollment_training_progress(request, enrollment, template):
    counselor = enrollment.counselor
    try:
        profile = counselor.counselor_profile
    except CounselorProfile.DoesNotExist:
        profile = None
    # 수련실적과 수련확인증 승인은 이 Enrollment만 본다.
    return render(
        request,
        template,
        {
            "counselor": counselor,
            "profile": profile,
            "cohort": enrollment.cohort,
            "training_enrollment": enrollment,
            "training_progress": get_counselor_training_progress(
                counselor,
                training_enrollment=enrollment,
            ),
            "list_cohort": enrollment.cohort,
            "certificate_approval": get_training_certificate_approval_status(
                counselor,
                training_enrollment=enrollment,
            ),
        },
    )


@role_required(UserRole.ADMIN)
def admin_training_progress(request):
    cohorts = training_enrollment_cohorts()
    selected_cohort = _parse_required_cohort(request.GET.get("cohort"), cohorts)
    rows = []
    if selected_cohort is not None:
        for entry in get_cohort_counselors_training_progress(selected_cohort):
            rows.append(
                {
                    "counselor": entry["counselor"],
                    "profile": entry["profile"],
                    "enrollment": entry["enrollment"],
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
    """기존 URL. 기수가 하나로 정해지면 Enrollment 상세로 보낸다."""
    counselor = get_object_or_404(
        User.objects.select_related("counselor_profile"),
        pk=counselor_id,
        role=UserRole.COUNSELOR,
    )
    enrollment = legacy_training_enrollment_or_404(counselor)
    return redirect_to_enrollment_training_progress(
        enrollment,
        url_name="admin_panel:enrollment_training_progress_detail",
        request=request,
    )


@role_required(UserRole.ADMIN)
def admin_enrollment_training_progress_detail(request, enrollment_id):
    enrollment = get_object_or_404(
        TrainingEnrollment.objects.select_related("counselor", "counselor__counselor_profile"),
        pk=enrollment_id,
        counselor__role=UserRole.COUNSELOR,
    )
    return render_enrollment_training_progress(
        request,
        enrollment,
        "admin_panel/counselor_training_progress_detail.html",
    )
