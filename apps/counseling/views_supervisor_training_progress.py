"""수퍼바이저 담당 기수 수련현황 조회(읽기 전용).

숫자·라벨은 관리자와 동일하게 get_cohort_counselors_training_progress /
get_counselor_training_progress 를 재사용한다.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from apps.accounts.decorators import supervisor_required
from apps.accounts.models import User, UserRole
from apps.counseling.journal_permissions import (
    get_supervisor_assigned_cohorts,
    supervisor_can_access_cohort,
    user_is_platform_admin,
)
from apps.counseling.models import TrainingEnrollment
from apps.counseling.training_enrollment import training_enrollment_cohorts
from apps.counseling.training_progress import get_cohort_counselors_training_progress
from apps.counseling.views_admin_training_progress import (
    _list_display,
    legacy_training_enrollment_or_404,
    redirect_to_enrollment_training_progress,
    render_enrollment_training_progress,
)


def _supervisor_training_cohorts(user) -> list[int]:
    """선택 가능한 기수(내림차순). 관리자는 Enrollment 기수, 수퍼바이저는 담당 기수만."""
    if user_is_platform_admin(user):
        return training_enrollment_cohorts()
    return sorted(get_supervisor_assigned_cohorts(user), reverse=True)


def _resolve_assigned_cohort(raw, cohorts: list[int]) -> int | None:
    """GET cohort. 미지정 시 담당 기수 중 최신. 담당 외 기수는 거부."""
    if not cohorts:
        return None
    if raw in (None, ""):
        return cohorts[0]
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise PermissionDenied("담당 기수가 아닙니다.") from exc
    if value not in cohorts:
        raise PermissionDenied("담당 기수가 아닙니다.")
    return value


@supervisor_required
def supervisor_training_progress(request):
    no_assigned = (
        not user_is_platform_admin(request.user)
        and not get_supervisor_assigned_cohorts(request.user)
    )
    if no_assigned:
        return render(
            request,
            "supervisor/training_progress.html",
            {
                "cohorts": [],
                "selected_cohort": None,
                "rows": [],
                "counselors_count": 0,
                "no_assigned_cohorts": True,
            },
        )

    cohorts = _supervisor_training_cohorts(request.user)
    selected_cohort = _resolve_assigned_cohort(request.GET.get("cohort"), cohorts)
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
        "supervisor/training_progress.html",
        {
            "cohorts": cohorts,
            "selected_cohort": selected_cohort,
            "rows": rows,
            "counselors_count": len(rows),
            "no_assigned_cohorts": False,
        },
    )


def _assert_supervisor_enrollment_access(user, enrollment):
    if not supervisor_can_access_cohort(user, enrollment.cohort):
        raise PermissionDenied("담당 기수 상담사만 조회할 수 있습니다.")


@supervisor_required
def supervisor_counselor_training_progress_detail(request, counselor_id):
    """기존 URL. 기수가 하나로 정해지고 담당 기수일 때만 Enrollment 상세로 보낸다."""
    counselor = get_object_or_404(
        User.objects.select_related("counselor_profile"),
        pk=counselor_id,
        role=UserRole.COUNSELOR,
    )
    enrollment = legacy_training_enrollment_or_404(counselor)
    _assert_supervisor_enrollment_access(request.user, enrollment)
    return redirect_to_enrollment_training_progress(
        enrollment,
        url_name="supervisor:enrollment_training_progress_detail",
        request=request,
    )


@supervisor_required
def supervisor_enrollment_training_progress_detail(request, enrollment_id):
    enrollment = get_object_or_404(
        TrainingEnrollment.objects.select_related(
            "counselor", "counselor__counselor_profile"
        ),
        pk=enrollment_id,
        counselor__role=UserRole.COUNSELOR,
    )
    _assert_supervisor_enrollment_access(request.user, enrollment)
    return render_enrollment_training_progress(
        request,
        enrollment,
        "supervisor/counselor_training_progress_detail.html",
    )
