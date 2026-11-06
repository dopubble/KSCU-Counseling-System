"""동일 기수 상담일지 조회 — 수퍼바이저·관리자 열람용."""

from __future__ import annotations

from collections import defaultdict

from django.db.models import Count, Exists, OuterRef, Q

from apps.accounts.models import CounselorProfile, UserRole, UserStatus
from apps.counseling.models import TrainingEnrollment
from apps.sessions_app.models import CounselingJournal, InitialCounselingRecord


def legacy_supervision_cohort(counselor) -> int | None:
    """Enrollment로 기수를 확정할 수 없을 때의 단일 기수 기준.

    Enrollment가 없거나, 있는 기수가 프로필 기수 하나뿐이면 그 기수를 쓴다.
    기수가 둘 이상이거나 프로필과 다르면 None이다. 그때는 수퍼바이저에게 열지 않는다.
    """
    profile_cohort = get_counselor_cohort(counselor)
    if counselor is None or not getattr(counselor, "pk", None) or not profile_cohort:
        return profile_cohort
    enrolled = {
        int(cohort)
        for cohort in TrainingEnrollment.objects.filter(counselor_id=counselor.pk).values_list(
            "cohort", flat=True
        )
    }
    if not enrolled or enrolled == {int(profile_cohort)}:
        return int(profile_cohort)
    return None


def supervision_cohort_for_case(case) -> int | None:
    """수퍼바이저 열람 기수. 사례 Enrollment가 있으면 그 기수다."""
    if case is None:
        return None
    if case.training_enrollment_id:
        enrollment = case.training_enrollment
        return int(enrollment.cohort) if enrollment is not None else None
    return legacy_supervision_cohort(case.counselor)


def counselor_visible_supervision_cohorts(counselor) -> set[int]:
    """이 상담사가 수퍼바이저 목록에 나타나는 기수.

    소속은 TrainingEnrollment다. 사례가 없어도 등록한 기수에 나온다.
    Enrollment가 없거나 프로필과 같은 단일 기수면 기존 프로필 기수를 유지한다.
    """
    if counselor is None or not getattr(counselor, "pk", None):
        return set()
    cohorts = {
        int(cohort)
        for cohort in TrainingEnrollment.objects.filter(counselor_id=counselor.pk).values_list(
            "cohort", flat=True
        )
    }
    legacy = legacy_supervision_cohort(counselor)
    if legacy:
        cohorts.add(int(legacy))
    return cohorts


def _legacy_enrollment_mismatch():
    return TrainingEnrollment.objects.filter(counselor_id=OuterRef("counselor_id")).exclude(
        cohort=OuterRef("counselor__counselor_profile__cohort")
    )


def supervision_record_cohort_q(allowed_cohorts: list[int] | None):
    """사례 Enrollment 기수, 또는 단일 기수 레거시 프로필 기수.

    관리자(allowed_cohorts=None)는 기수가 있는 기록과, 프로필 기수가 있는
    Enrollment 없는 기록을 그대로 본다. 기수를 확정할 수 없는 기록은
    수퍼바이저 목록에만 넣지 않는다.
    """
    enrolled = Q(case__training_enrollment__isnull=False)
    if allowed_cohorts is None:
        return enrolled | Q(
            case__training_enrollment__isnull=True,
            counselor__counselor_profile__cohort__isnull=False,
        )
    legacy = Q(case__training_enrollment__isnull=True) & ~Exists(_legacy_enrollment_mismatch())
    legacy &= Q(counselor__counselor_profile__cohort__in=allowed_cohorts)
    enrolled &= Q(case__training_enrollment__cohort__in=allowed_cohorts)
    return enrolled | legacy


def get_counselor_cohort(counselor) -> int | None:
    if counselor is None or not getattr(counselor, "pk", None):
        return None
    return CounselorProfile.objects.filter(user_id=counselor.pk).values_list(
        "cohort", flat=True
    ).first()


def get_cohort_peer_journals_by_session(
    cohort: int,
    *,
    max_session: int,
) -> dict[int, list[CounselingJournal]]:
    """[Deprecated 이름] 담당 기수 상담일지 — allowed_cohorts=[cohort] 로 위임."""
    return get_cohort_journals_for_supervision(
        allowed_cohorts=[cohort],
        max_session=max_session,
    )


def get_cohort_journals_for_supervision(
    *,
    allowed_cohorts: list[int] | None,
    max_session: int,
) -> dict[int, list[CounselingJournal]]:
    """
    완료(비초안) 상담일지를 회차별로 반환.
    allowed_cohorts=None 이면 전체 기수(ADMIN).
    """
    if max_session < 1:
        return {}

    qs = CounselingJournal.objects.filter(
        is_draft=False,
        session_number__gte=1,
        session_number__lte=max_session,
        counselor__counselor_profile__cohort__isnull=False,
    )
    if allowed_cohorts is not None:
        if not allowed_cohorts:
            return {}
        qs = qs.filter(counselor__counselor_profile__cohort__in=allowed_cohorts)

    by_session: dict[int, list[CounselingJournal]] = defaultdict(list)
    journals = qs.select_related(
        "case",
        "case__client",
        "case__application",
        "counselor",
        "counselor__counselor_profile",
    ).order_by("session_number", "counselor__name", "case__case_number")

    for journal in journals:
        by_session[journal.session_number].append(journal)
    return dict(by_session)


def _legacy_profile_cohort(profile_cohort, enrolled: set[int]) -> int | None:
    if not profile_cohort:
        return None
    cohort = int(profile_cohort)
    if not enrolled or enrolled == {cohort}:
        return cohort
    return None


def get_supervision_counselors_by_cohort(*, allowed_cohorts: list[int] | None) -> list[dict]:
    """
    수퍼비전 상담사 목록을 등록 기수별로 반환한다.

    상담사 조건은 사례발표 동기 집계와 같다.
    승인(is_approved), 역할 COUNSELOR, 상태 ACTIVE, 기수 있음.
    기수 소속은 TrainingEnrollment다. 사례나 완료 일지가 없어도 그 기수에 나온다.
    Enrollment가 없거나 프로필 기수와 같은 단일 기수면 기존처럼 프로필 기수에 나온다.
    같은 상담사가 여러 기수에 등록돼 있으면 기수 섹션마다 따로 나온다.
    완료 일지 수는 그 기수 Enrollment에 연결된 사례만 센다.
    """
    if allowed_cohorts is not None and not allowed_cohorts:
        return []
    profiles = list(
        CounselorProfile.objects.filter(
            cohort__isnull=False,
            is_approved=True,
            user__role=UserRole.COUNSELOR,
            user__status=UserStatus.ACTIVE,
        )
        .select_related("user")
        .order_by("user__name")
    )
    if not profiles:
        return []
    counselor_ids = [profile.user_id for profile in profiles]
    enrolled: dict[int, set[int]] = defaultdict(set)
    for counselor_id, cohort in TrainingEnrollment.objects.filter(
        counselor_id__in=counselor_ids
    ).values_list("counselor_id", "cohort"):
        enrolled[counselor_id].add(int(cohort))
    journal_counts: dict[tuple[int, int | None], int] = defaultdict(int)
    for row in (
        CounselingJournal.objects.filter(is_draft=False, counselor_id__in=counselor_ids)
        .values("counselor_id", "case__training_enrollment__cohort")
        .annotate(total=Count("pk"))
    ):
        journal_counts[
            (row["counselor_id"], row["case__training_enrollment__cohort"])
        ] = row["total"]

    allowed = set(allowed_cohorts) if allowed_cohorts is not None else None
    groups: dict[int, dict] = {}
    for profile in profiles:
        counselor_id = profile.user_id
        visible = set(enrolled[counselor_id])
        legacy = _legacy_profile_cohort(profile.cohort, enrolled[counselor_id])
        if legacy:
            visible.add(legacy)
        if allowed is not None:
            visible &= allowed
        for cohort in visible:
            count = journal_counts.get((counselor_id, cohort), 0)
            if legacy == cohort:
                count += journal_counts.get((counselor_id, None), 0)
            group = groups.get(cohort)
            if group is None:
                group = {
                    "cohort": cohort,
                    "label": f"{cohort}기",
                    "counselors": [],
                }
                groups[cohort] = group
            group["counselors"].append(
                {
                    "id": counselor_id,
                    "name": profile.user.name,
                    "completed_journal_count": count,
                }
            )
    ordered = [groups[cohort] for cohort in sorted(groups)]
    for group in ordered:
        group["counselors"].sort(key=lambda row: row["name"])
    return ordered


def get_counselor_journals_for_supervision(counselor, *, cohort=None):
    """
    특정 상담사의 완료(비초안) 상담일지.

    cohort를 주면 그 기수 사례의 일지만 반환한다.
    기수 권한은 호출 전에 검사한다.
    """
    if counselor is None or not getattr(counselor, "pk", None):
        return CounselingJournal.objects.none()

    queryset = CounselingJournal.objects.filter(
        counselor_id=counselor.pk,
        is_draft=False,
    )
    if cohort is not None:
        matched = Q(case__training_enrollment__cohort=cohort)
        if legacy_supervision_cohort(counselor) == cohort:
            matched |= Q(case__training_enrollment__isnull=True)
        queryset = queryset.filter(matched)
    return (
        queryset.select_related(
            "case",
            "case__client",
            "case__training_enrollment",
            "case__initial_counseling_record",
            "case__termination_counseling_record",
            "appointment",
            "counselor",
            "counselor__counselor_profile",
        )
        .order_by("session_number", "case__case_number")
    )


def get_cohort_initial_records_for_supervision(
    *,
    allowed_cohorts: list[int] | None,
) -> list[InitialCounselingRecord]:
    """완료(비초안) 초기상담 기록지 목록."""
    if allowed_cohorts is not None and not allowed_cohorts:
        return []
    qs = InitialCounselingRecord.objects.filter(is_draft=False).filter(
        supervision_record_cohort_q(allowed_cohorts)
    )

    return list(
        qs.select_related(
            "case",
            "case__client",
            "case__application",
            "counselor",
            "counselor__counselor_profile",
        ).order_by("counselor__name", "case__case_number")
    )
