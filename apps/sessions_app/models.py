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


class ZoomMeeting(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    appointment = models.OneToOneField(
        "scheduling.Appointment",
        on_delete=models.CASCADE,
        related_name="zoom_meeting",
        verbose_name="예약",
    )
    zoom_meeting_id = models.CharField("Zoom Meeting ID", max_length=100)
    join_url = models.URLField("참가 URL")
    start_url = models.URLField("호스트 URL", blank=True)
    password = models.CharField("비밀번호", max_length=50, blank=True)
    recording_url = models.URLField("녹화 URL", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Zoom 미팅"
        verbose_name_plural = "Zoom 미팅"

    def __str__(self):
        return f"Zoom - {self.appointment}"
