import uuid

from django.conf import settings
from django.db import models


class CounselingJournal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(
        "counseling.Case",
        on_delete=models.CASCADE,
        related_name="journals",
        verbose_name="사례",
    )
    appointment = models.ForeignKey(
        "scheduling.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="journals",
        verbose_name="예약",
    )
    counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="journals",
        verbose_name="상담사",
    )
    session_number = models.PositiveIntegerField("회차")
    session_category = models.CharField("상담 구분", max_length=50, blank=True)
    session_datetime = models.DateTimeField("상담 일시", null=True, blank=True)
    subjective = models.TextField("S (주관적)", blank=True)
    objective = models.TextField("O (객관적)", blank=True)
    assessment = models.TextField("A (평가)", blank=True)
    plan = models.TextField("P (계획)", blank=True)
    is_draft = models.BooleanField("임시저장", default=True)
    session_consumed = models.BooleanField(
        "회기 차감 완료",
        default=False,
        help_text="완료된 일지가 사례의 남은 회기에 반영되었는지",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "상담일지"
        verbose_name_plural = "상담일지"
        ordering = ["case", "session_number"]
        indexes = [
            models.Index(fields=["case", "session_number"]),
        ]
        unique_together = [("case", "session_number")]

    def __str__(self):
        return f"{self.case.case_number} - {self.session_number}회차"


class InitialCounselingRecord(models.Model):
    """초기상담 기록지 — 1회기 전용, 상담사만 열람·작성."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(
        "counseling.Case",
        on_delete=models.CASCADE,
        related_name="initial_counseling_record",
        verbose_name="사례",
    )
    counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="initial_counseling_records",
        verbose_name="상담사",
    )
    session_start_datetime = models.DateTimeField(
        "상담 시작 일시",
        null=True,
        blank=True,
    )
    presented_problems_summary = models.TextField(
        "제시된 문제·주제·패턴·현재 상태 요약",
        blank=True,
    )
    functioning_impact = models.TextField(
        "현재와 과거의 기능 및 문제의 영향",
        blank=True,
    )
    relational_history = models.TextField(
        "관계적 역사",
        blank=True,
    )
    clinical_history = models.TextField(
        "임상적 역사",
        blank=True,
    )
    theological_evaluation = models.TextField(
        "신학적 평가",
        blank=True,
    )
    clinical_strategy = models.TextField(
        "임상적 전략",
        blank=True,
    )
    other_notes = models.TextField("기타", blank=True)
    is_draft = models.BooleanField("임시저장", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "초기상담 기록지"
        verbose_name_plural = "초기상담 기록지"

    def __str__(self):
        return f"{self.case.case_number} - 초기상담 기록지"


class TerminationCounselingRecord(models.Model):
    """종결기록지 — 마지막 회기 전용, 상담사만 열람·작성."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(
        "counseling.Case",
        on_delete=models.CASCADE,
        related_name="termination_counseling_record",
        verbose_name="사례",
    )
    counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="termination_counseling_records",
        verbose_name="상담사",
    )
    counseling_period = models.TextField("상담 진행 일시", blank=True)
    main_topics = models.TextField("상담받은 주요주제", blank=True)
    termination_reason = models.TextField("종결(중단) 사유", blank=True)
    counselor_opinion = models.TextField("내담자에 대한 상담자 소견", blank=True)
    post_termination_plan = models.TextField(
        "종결 후 계획 또는 후속조치",
        blank=True,
    )
    other_notes = models.TextField("기타", blank=True)
    is_draft = models.BooleanField("임시저장", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "종결기록지"
        verbose_name_plural = "종결기록지"

    def __str__(self):
        return f"{self.case.case_number} - 종결기록지"


class ZoomMeeting(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    appointment = models.OneToOneField(
        "scheduling.Appointment",
        on_delete=models.CASCADE,
        related_name="zoom_meeting",
        verbose_name="예약",
    )
    zoom_meeting_id = models.CharField("Zoom Meeting ID", max_length=100)
    join_url = models.URLField("참가 URL", max_length=2000)
    start_url = models.URLField(
        "호스트 URL",
        max_length=2000,
        blank=True,
        help_text="Zoom API 보관용(/s/, zak=). 상담사·내담자 UI·메일에는 사용하지 않음.",
    )
    password = models.CharField("비밀번호", max_length=50, blank=True)
    zoom_host_email = models.EmailField(
        "Zoom 호스트(Licensed 사용자)",
        max_length=254,
        blank=True,
        default="",
        help_text="회의를 생성한 Zoom Licensed 사용자 이메일",
    )
    counselor_host_key = models.CharField(
        "상담사 호스트 키(Claim Host)",
        max_length=20,
        blank=True,
        default="",
        help_text="외부 Zoom 계정 회의 등 — 회기별 Claim Host 6자리. 비우면 ZOOM_HOST_KEY 환경변수.",
    )
    recording_url = models.URLField("녹화 URL", max_length=2000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Zoom 미팅"
        verbose_name_plural = "Zoom 미팅"

    def __str__(self):
        return f"Zoom - {self.appointment}"
