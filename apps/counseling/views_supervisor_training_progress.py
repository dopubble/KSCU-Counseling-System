"""수퍼바이저 담당 기수 수련현황 조회(읽기 전용).

숫자·라벨은 관리자와 동일하게 get_cohort_counselors_training_progress /
get_counselor_training_progress 를 재사용한다.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from apps.accounts.decorators import supervisor_required
from apps.accounts.models import CounselorProfile, User, UserRole
from apps.counseling.journal_permissions import (
    get_supervisor_assigned_cohorts,
    supervisor_can_access_cohort,
    user_can_browse_counselor_journals,
    user_is_platform_admin,
)
from apps.counseling.training_progress import (
    get_cohort_counselors_training_progress,
    get_counselor_training_progress,
)
from apps.counseling.views_admin_training_progress import _list_display
from apps.reports.views import get_available_cohorts


def _supervisor_training_cohorts(user) -> list[int]:
    """선택 가능한 기수(내림차순). 관리자는 전체, 수퍼바이저는 담당 기수만."""
    if user_is_platform_admin(user):
        return get_available_cohorts()
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


@supervisor_required
def supervisor_counselor_training_progress_detail(request, counselor_id):
    counselor = get_object_or_404(
        User.objects.select_related("counselor_profile"),
        pk=counselor_id,
        role=UserRole.COUNSELOR,
    )
    if not user_can_browse_counselor_journals(request.user, counselor):
        raise PermissionDenied("담당 기수 상담사만 조회할 수 있습니다.")

    try:
        profile = counselor.counselor_profile
    except CounselorProfile.DoesNotExist as exc:
        raise Http404("상담사 프로필이 없습니다.") from exc

    cohort = profile.cohort
    if not supervisor_can_access_cohort(request.user, cohort):
        raise PermissionDenied("담당 기수 상담사만 조회할 수 있습니다.")

    progress = get_counselor_training_progress(counselor)
    return render(
        request,
        "supervisor/counselor_training_progress_detail.html",
        {
            "counselor": counselor,
            "profile": profile,
            "cohort": cohort,
            "training_progress": progress,
            "list_cohort": cohort or request.GET.get("cohort") or "",
        },
    )
