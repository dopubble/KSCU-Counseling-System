import uuid

from django.conf import settings
from django.db import models


class AppointmentStatus(models.TextChoices):
    PENDING = "PENDING", "대기"
    SCHEDULED = "SCHEDULED", "예약"
    CONFIRMED = "CONFIRMED", "확정"
    CANCEL_PENDING = "CANCEL_PENDING", "취소 대기 중"
    COMPLETED = "COMPLETED", "완료"
    CANCELLED = "CANCELLED", "취소"
    NO_SHOW = "NO_SHOW", "노쇼"


class CounselorAvailability(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="availabilities",
        verbose_name="상담사",
    )
    is_recurring = models.BooleanField("매주 반복", default=True)
    specific_date = models.DateField("특정 날짜", null=True, blank=True)
    day_of_week = models.IntegerField(
        "요일",
        null=True,
        blank=True,
        help_text="0=월요일, 6=일요일",
    )
    start_time = models.TimeField("시작 시간")
    end_time = models.TimeField("종료 시간")
    slot_duration = models.PositiveIntegerField("슬롯 시간(분)", default=50)
    is_available = models.BooleanField("상담 가능", default=True)
    is_active = models.BooleanField("활성", default=True)

    class Meta:
        verbose_name = "상담 가능 시간"
        verbose_name_plural = "상담 가능 시간"
        ordering = ["-is_recurring", "specific_date", "day_of_week", "start_time"]

    _DAY_LABELS = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")

    @property
    def day_label(self) -> str:
        if self.is_recurring and self.day_of_week is not None:
            if 0 <= self.day_of_week < len(self._DAY_LABELS):
                return self._DAY_LABELS[self.day_of_week]
            return str(self.day_of_week)
        if self.specific_date:
            weekday = self.specific_date.weekday()
            if 0 <= weekday < len(self._DAY_LABELS):
                return self._DAY_LABELS[weekday]
        return ""

    @property
    def schedule_label(self) -> str:
        if self.is_recurring:
            return f"매주 {self.day_label}"
        if self.specific_date:
            return self.specific_date.strftime("%Y-%m-%d")
        return "특정 일정"

    @property
    def availability_label(self) -> str:
        return "가능" if self.is_available else "차단"

    def __str__(self):
        prefix = "매주" if self.is_recurring else str(self.specific_date or "")
        status = "가능" if self.is_available else "차단"
        return f"{self.counselor.name} - {prefix} {self.start_time}-{self.end_time} ({status})"


class AvailabilityException(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="availability_exceptions",
        verbose_name="상담사",
    )
    date = models.DateField("날짜")
    is_available = models.BooleanField("가능 여부", default=False, help_text="False=휴무")
    note = models.CharField("메모", max_length=200, blank=True)

    class Meta:
        verbose_name = "일정 예외"
        verbose_name_plural = "일정 예외"
        unique_together = [("counselor", "date")]

    def __str__(self):
        status = "가능" if self.is_available else "휴무"
        return f"{self.counselor.name} - {self.date} ({status})"


class Appointment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(
        "counseling.Case",
        on_delete=models.CASCADE,
        related_name="appointments",
        verbose_name="사례",
    )
    counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="counselor_appointments",
        verbose_name="상담사",
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="client_appointments",
        verbose_name="내담자",
    )
    scheduled_at = models.DateTimeField("예약 일시")
    duration_minutes = models.PositiveIntegerField("상담 시간(분)", default=50)
    status = models.CharField(
        "상태",
        max_length=20,
        choices=AppointmentStatus.choices,
        default=AppointmentStatus.PENDING,
    )
    cancelled_at = models.DateTimeField("취소일", null=True, blank=True)
    cancel_reason = models.TextField(
        "취소 사유",
        blank=True,
        help_text="내담자 취소 요청 또는 관리자 취소 처리 시 입력",
    )
    cancel_requested_at = models.DateTimeField(
        "취소 요청일",
        null=True,
        blank=True,
        help_text="내담자가 취소 요청(CANCEL_PENDING)을 제출한 시각",
    )
    confirmed_at = models.DateTimeField("확정일", null=True, blank=True)
    session_number = models.PositiveIntegerField(
        "회차",
        null=True,
        blank=True,
        help_text="내담자 회기별 예약 신청 시 연결되는 상담 회기 번호",
    )
    request_message = models.TextField(
        "예약 요청 내용",
        blank=True,
        help_text="회기별 예약·일정 변경 요청 시 내담자가 입력한 내용",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "예약"
        verbose_name_plural = "예약"
        ordering = ["scheduled_at"]
        indexes = [
            models.Index(fields=["counselor", "scheduled_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["counselor", "scheduled_at"],
                condition=models.Q(
                    status__in=[
                        AppointmentStatus.SCHEDULED,
                        AppointmentStatus.CONFIRMED,
                    ]
                ),
                name="unique_counselor_confirmed_slot",
            ),
        ]

    @property
    def appointment_datetime(self):
        """상담 예약 일시 (운영 규칙·취소 정책에서 사용)."""
        return self.scheduled_at

    def __str__(self):
        return f"{self.client.name} - {self.scheduled_at:%Y-%m-%d %H:%M}"
