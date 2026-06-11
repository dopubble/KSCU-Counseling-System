"""관리자 화면용 상담 신청 조회 (중복·유령 신청 표시)."""

from django.db.models import Exists, OuterRef, QuerySet

from apps.counseling.models import ApplicationStatus, Case, CaseStatus, CounselingApplication


def _assigned_active_case_elsewhere_subquery():
    """같은 내담자에게 다른 신청으로 이미 배정된 ACTIVE 사례."""
    return Case.objects.filter(
        client_id=OuterRef("client_id"),
        status=CaseStatus.ACTIVE,
        counselor_id__isnull=False,
    ).exclude(application_id=OuterRef("pk"))


def annotate_pending_application_flags(
    queryset: QuerySet[CounselingApplication],
) -> QuerySet[CounselingApplication]:
    """
    다른 신청으로 이미 진행 중인 사례가 있는지 표시용 플래그를 붙입니다.
    (관리자 목록에서 숨기지 않고, 중복 신청임을 안내합니다.)
    """
    assigned_elsewhere = _assigned_active_case_elsewhere_subquery()
    return queryset.annotate(
        has_other_active_case=Exists(assigned_elsewhere),
    )


def client_has_other_active_case(application: CounselingApplication) -> bool:
    """이 신청 외에 같은 내담자의 진행 중(상담사 배정) 사례가 있는지."""
    return (
        Case.objects.filter(
            client_id=application.client_id,
            status=CaseStatus.ACTIVE,
            counselor_id__isnull=False,
        )
        .exclude(application_id=application.pk)
        .exists()
    )


def is_stale_pending_application(application: CounselingApplication) -> bool:
    """진행 중 사례가 있는데 매칭대기만 중복으로 남은 신청."""
    try:
        application.case
    except Case.DoesNotExist:
        pass
    else:
        return False
    if application.status not in (
        ApplicationStatus.RECEIVED,
        ApplicationStatus.WAITING_MATCH,
    ):
        return False
    return client_has_other_active_case(application)


def waiting_match_for_admin() -> QuerySet[CounselingApplication]:
    """관리자 대시보드·통합관리용 매칭 대기 목록."""
    qs = CounselingApplication.objects.waiting_for_match().select_related(
        "client", "case", "case__counselor"
    )
    return annotate_pending_application_flags(qs)


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
        .annotate(has_other_active_case=Exists(assigned_elsewhere))
        .filter(has_other_active_case=True)
        .select_related("client")
        .order_by("-created_at")
    )


def client_has_open_pending_application(client) -> bool:
    """사례 미생성·접수/매칭대기 중인 신청이 있으면 True."""
    return CounselingApplication.objects.filter(
        client=client,
        case__isnull=True,
        status__in=(
            ApplicationStatus.RECEIVED,
            ApplicationStatus.WAITING_MATCH,
        ),
    ).exists()
