"""관리자 화면용 상담 신청 조회 (중복·유령 신청 제외)."""

from django.db.models import Exists, OuterRef, QuerySet

from apps.counseling.models import ApplicationStatus, Case, CaseStatus, CounselingApplication


def _assigned_active_case_elsewhere_subquery():
    """같은 내담자에게 다른 신청으로 이미 배정된 ACTIVE 사례."""
    return Case.objects.filter(
        client_id=OuterRef("client_id"),
        status=CaseStatus.ACTIVE,
        counselor_id__isnull=False,
    ).exclude(application_id=OuterRef("pk"))


def exclude_stale_pending_applications(
    queryset: QuerySet[CounselingApplication],
) -> QuerySet[CounselingApplication]:
    """
    상담사 배정·사례 생성이 끝난 뒤 중복으로 생긴 접수/매칭대기 신청을 제외합니다.
    (내담자·상담사 화면은 ACTIVE 사례를 보므로, 관리자 목록만 어긋나는 현상 방지)
    """
    assigned_elsewhere = _assigned_active_case_elsewhere_subquery()
    return queryset.annotate(
        _has_assigned_active_case_elsewhere=Exists(assigned_elsewhere),
    ).exclude(
        _has_assigned_active_case_elsewhere=True,
        case__isnull=True,
        status__in=[
            ApplicationStatus.RECEIVED,
            ApplicationStatus.WAITING_MATCH,
        ],
    )


def waiting_match_for_admin() -> QuerySet[CounselingApplication]:
    """관리자 대시보드·통합관리용 매칭 대기 목록."""
    qs = CounselingApplication.objects.waiting_for_match().select_related(
        "client", "case", "case__counselor"
    )
    return exclude_stale_pending_applications(qs)


def stale_pending_applications() -> QuerySet[CounselingApplication]:
    """이미 배정된 내담자에게 남아 있는 중복 매칭대기 신청."""
    assigned_elsewhere = _assigned_active_case_elsewhere_subquery()
    return (
        CounselingApplication.objects.filter(
            case__isnull=True,
            status__in=[
                ApplicationStatus.RECEIVED,
                ApplicationStatus.WAITING_MATCH,
            ],
        )
        .annotate(_has_assigned_active_case_elsewhere=Exists(assigned_elsewhere))
        .filter(_has_assigned_active_case_elsewhere=True)
        .select_related("client")
        .order_by("-created_at")
    )
