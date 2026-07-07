import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.scheduling.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES


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


class RemoteZoomSchedulingSettings(models.Model):
    """
    비대면 Zoom 운영 설정 (단일 행).

  simultaneous_session_capacity: 같은 시작 시각 동시 확정 상한 (기본 2).
  ZOOM_LICENSED_USERS의 추가 계정(host_03 등)은 버퍼 엇갈림 배정용.
    """

    SETTINGS_PK = 1

    id = models.PositiveSmallIntegerField(
        primary_key=True,
        default=SETTINGS_PK,
        editable=False,
    )
    simultaneous_session_capacity = models.PositiveSmallIntegerField(
        "동시간대 비대면 최대 건수",
        default=2,
        help_text=(
            "같은 시작 시각(예: 11:00)에 확정 가능한 비대면 상담 최대 건수. "
            "ZOOM_LICENSED_USERS에 host_03 등을 추가하면 10시·11시 엇갈림 배정에만 "
            "쓰이고, 이 값을 늘리지 않는 한 11시 3건은 불가합니다."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Zoom 운영 설정"
        verbose_name_plural = "Zoom 운영 설정"

    def save(self, *args, **kwargs):
        self.pk = self.SETTINGS_PK
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        from apps.scheduling.zoom_hosts import get_zoom_licensed_user_emails

        if self.simultaneous_session_capacity < 1:
            raise ValidationError(
                {"simultaneous_session_capacity": "1건 이상이어야 합니다."}
            )
        pool_size = len(get_zoom_licensed_user_emails())
        if pool_size and self.simultaneous_session_capacity > pool_size:
            raise ValidationError(
                {
                    "simultaneous_session_capacity": (
                        f"Licensed Zoom 호스트 수({pool_size})를 초과할 수 없습니다."
                    )
                }
            )

    def __str__(self):
        return f"비대면 동시간대 최대 {self.simultaneous_session_capacity}건"


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
    duration_minutes = models.PositiveIntegerField(
        "상담 시간(분)",
        default=DEFAULT_APPOINTMENT_DURATION_MINUTES,
    )
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
        from apps.scheduling.availability import format_local_datetime

        return f"{self.client.name} - {format_local_datetime(self.scheduled_at)}"
