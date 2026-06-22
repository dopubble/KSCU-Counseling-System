import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class ApplicationStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "접수"
    WAITING_MATCH = "WAITING_MATCH", "매칭대기"
    MATCHED = "MATCHED", "매칭완료"
    IN_PROGRESS = "IN_PROGRESS", "진행중"
    CLOSED = "CLOSED", "종결"
    CANCELLED = "CANCELLED", "취소"


class CaseStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "개입중"
    ON_HOLD = "ON_HOLD", "휴지"
    CLOSED = "CLOSED", "종결"
    TRANSFERRED = "TRANSFERRED", "이관"


class RiskLevel(models.TextChoices):
    LOW = "LOW", "낮음"
    MEDIUM = "MEDIUM", "보통"
    HIGH = "HIGH", "높음"


class CounselingMethod(models.TextChoices):
    IN_PERSON = "IN_PERSON", "대면"
    REMOTE = "REMOTE", "비대면"


class CounselingApplicationQuerySet(models.QuerySet):
    """상담 신청 조회 헬퍼"""

    def waiting_for_match(self):
        """상담사 미배정·접수/매칭대기 상태 신청"""
        return self.filter(
            status__in=[
                ApplicationStatus.RECEIVED,
                ApplicationStatus.WAITING_MATCH,
            ]
        )


class CounselingApplication(models.Model):
    objects = CounselingApplicationQuerySet.as_manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="applications",
        verbose_name="내담자",
    )
    counseling_types = models.JSONField("상담 유형", default=list, blank=True)
    reason = models.TextField("주요 호소 문제")
    residence_region = models.CharField(
        "거주지역",
        max_length=200,
        blank=True,
        default="",
        help_text="국내: 시·도 단위 / 해외: 국가명 포함",
    )
    clinical_diagnosis = models.TextField(
        "병원 진단명",
        blank=True,
        default="",
    )
    current_medication = models.TextField(
        "복용 중인 약",
        blank=True,
        default="",
        help_text="관련 약물 없으면 '없음'",
    )
    occupation = models.CharField(
        "직업",
        max_length=100,
        blank=True,
        default="",
    )
    preferred_schedule = models.JSONField("희망 일정", default=dict, blank=True)
    counseling_method = models.CharField(
        "상담 방식",
        max_length=20,
        choices=CounselingMethod.choices,
        default=CounselingMethod.IN_PERSON,
    )
    status = models.CharField(
        "상태",
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.WAITING_MATCH,
    )
    created_at = models.DateTimeField("신청일", default=timezone.now)
    updated_at = models.DateTimeField("수정일", auto_now=True)

    class Meta:
        verbose_name = "상담 신청"
        verbose_name_plural = "상담 신청"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    def get_counseling_types_display(self, separator: str = ", ") -> str:
        raw = self.counseling_types
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw.strip()
        if isinstance(raw, (list, tuple)):
            return separator.join(
                str(item) for item in raw if item is not None and str(item).strip()
            )
        return str(raw)

    @property
    def counseling_type(self) -> str:
        """템플릿·레거시 호환용 (쉼표로 연결된 표시 문자열)."""
        return self.get_counseling_types_display()

    def __str__(self):
        return f"{self.client.name} - {self.counseling_type} ({self.get_status_display()})"


class Case(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.OneToOneField(
        CounselingApplication,
        on_delete=models.CASCADE,
        related_name="case",
        verbose_name="상담 신청",
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="client_cases",
        verbose_name="내담자",
    )
    counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="counselor_cases",
        verbose_name="상담사",
    )
    case_number = models.CharField("사례번호", max_length=20, unique=True)
    status = models.CharField(
        "상태", max_length=20, choices=CaseStatus.choices, default=CaseStatus.ACTIVE
    )
    risk_level = models.CharField(
        "위험도", max_length=20, choices=RiskLevel.choices, default=RiskLevel.LOW
    )
    tags = models.JSONField("태그", default=list, blank=True)
    notes = models.TextField("메모", blank=True)
    zoom_meeting_url = models.URLField(
        "Zoom 회의 링크",
        max_length=2000,
        blank=True,
        help_text="상담 예약 시 Zoom API로 생성된 회의 URL(참가 링크)",
    )
    counseling_method = models.CharField(
        "상담 진행 방식",
        max_length=20,
        choices=CounselingMethod.choices,
        default=CounselingMethod.IN_PERSON,
    )
    total_sessions = models.PositiveIntegerField(
        "총 회기 수",
        default=10,
        help_text="상담사 매칭 시 설정하는 전체 상담 회기 수",
    )
    remaining_sessions = models.PositiveIntegerField(
        "남은 회기 수",
        default=10,
        help_text="상담일지 완료 시 1회씩 차감됩니다.",
    )
    day_of_cancel_count = models.PositiveIntegerField(
        "당일 취소 횟수",
        default=0,
        help_text="예약 당일 취소 요청이 누적된 횟수(3회 이상 시 조기 종결)",
    )
    opened_at = models.DateTimeField("개시일", default=timezone.now)
    closed_at = models.DateTimeField("종결일", null=True, blank=True)

    class Meta:
        verbose_name = "사례"
        verbose_name_plural = "사례"
        ordering = ["-opened_at"]
        indexes = [
            models.Index(fields=["counselor", "status"]),
        ]

    def __str__(self):
        return f"{self.case_number} - {self.client.name}"

    @property
    def sessions_label(self) -> str:
        return f"{self.remaining_sessions} / {self.total_sessions}"

    def save(self, *args, **kwargs):
        from django.db import IntegrityError

        if self.case_number:
            super().save(*args, **kwargs)
            return

        year = timezone.now().year
        prefix = f"CASE-{year}-"
        last_error = None
        for _ in range(8):
            last = (
                Case.objects.filter(case_number__startswith=prefix)
                .order_by("-case_number")
                .values_list("case_number", flat=True)
                .first()
            )
            if last:
                try:
                    seq = int(str(last).rsplit("-", 1)[-1]) + 1
                except ValueError:
                    seq = Case.objects.filter(case_number__startswith=prefix).count() + 1
            else:
                seq = 1
            self.case_number = f"{prefix}{seq:04d}"
            try:
                super().save(*args, **kwargs)
                return
            except IntegrityError as exc:
                last_error = exc
                self.case_number = ""
        if last_error:
            raise last_error
        super().save(*args, **kwargs)


class ChatMessage(models.Model):
    """사례별 1:1 실시간 채팅 메시지 (내담자 ↔ 담당 상담사)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(
        Case,
        on_delete=models.CASCADE,
        related_name="chat_messages",
        verbose_name="사례",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_chat_messages",
        verbose_name="보낸 사람",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_chat_messages",
        verbose_name="받는 사람",
    )
    body = models.TextField("메시지", max_length=2000)
    is_read = models.BooleanField("읽음", default=False)
    created_at = models.DateTimeField("보낸 시간", auto_now_add=True)

    class Meta:
        verbose_name = "채팅 메시지"
        verbose_name_plural = "채팅 메시지"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["case", "created_at"]),
            models.Index(fields=["case", "recipient", "is_read"]),
        ]

    def __str__(self):
        preview = (self.body[:40] + "…") if len(self.body) > 40 else self.body
        return f"{self.case.case_number} — {self.sender.name}: {preview}"


class SessionScheduleChangeRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(
        Case,
        on_delete=models.CASCADE,
        related_name="schedule_change_requests",
        verbose_name="사례",
    )
    session_number = models.PositiveIntegerField("회차")
    appointment = models.ForeignKey(
        "scheduling.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schedule_change_requests",
        verbose_name="예약",
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="schedule_change_requests",
        verbose_name="내담자",
    )
    preferred_datetime = models.DateTimeField("희망 일시", null=True, blank=True)
    message = models.TextField("요청 내용", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "일정 변경 요청"
        verbose_name_plural = "일정 변경 요청"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.case.case_number} {self.session_number}회기 일정 변경"
