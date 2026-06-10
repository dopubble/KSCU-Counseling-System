import os
import uuid

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import get_valid_filename

from apps.accounts.models import UserRole


def _upload_basename(filename: str) -> str:
    """업로드 파일의 원본 파일명(경로 제거·안전 문자만)."""
    basename = os.path.basename((filename or "").replace("\\", "/"))
    return get_valid_filename(basename) or "file"


def session_material_upload_path(instance, filename):
    """원본 파일명 유지 + 날짜 폴더로 중복·덮어쓰기 방지."""
    basename = _upload_basename(filename)
    date_path = timezone.now().strftime("%Y/%m/%d")

    if instance.is_shared and instance.case_id:
        return f"board/{instance.case_id}/{date_path}/{basename}"

    if instance.appointment_id:
        return f"session_materials/{instance.appointment_id}/{date_path}/{basename}"

    if instance.case_id and instance.session_number:
        return (
            f"session_materials/{instance.case_id}/{instance.session_number}/"
            f"{date_path}/{basename}"
        )

    if instance.case_id:
        return f"session_materials/{instance.case_id}/{date_path}/{basename}"

    return f"session_materials/{date_path}/{basename}"


def consent_upload_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1]
    return f"consents/{instance.client_id}/{uuid.uuid4()}.{ext}"


def closure_report_upload_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1]
    return f"closure_reports/{instance.case_id}/{uuid.uuid4()}.{ext}"


def counselor_assignment_upload_path(instance, filename):
    basename = _upload_basename(filename)
    date_path = timezone.now().strftime("%Y/%m/%d")
    return f"counselor_assignments/{instance.case_id}/{date_path}/{basename}"


class ConsentDocType(models.TextChoices):
    PRIVACY = "PRIVACY", "개인정보 동의"
    COUNSELING = "COUNSELING", "상담 동의"
    RECORDING = "RECORDING", "녹화 동의"


class ConsentDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="consent_documents",
        verbose_name="내담자",
    )
    application = models.ForeignKey(
        "counseling.CounselingApplication",
        on_delete=models.CASCADE,
        related_name="consent_documents",
        verbose_name="상담 신청",
    )
    doc_type = models.CharField("동의서 유형", max_length=20, choices=ConsentDocType.choices)
    file = models.FileField("파일", upload_to=consent_upload_path)
    signed_at = models.DateTimeField("서명일", auto_now_add=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_consents",
        verbose_name="검증자",
    )

    class Meta:
        verbose_name = "동의서"
        verbose_name_plural = "동의서"
        unique_together = [("application", "doc_type")]

    def __str__(self):
        return f"{self.client.name} - {self.get_doc_type_display()}"


class ClosureReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(
        "counseling.Case",
        on_delete=models.CASCADE,
        related_name="closure_report",
        verbose_name="사례",
    )
    counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="closure_reports",
        verbose_name="상담사",
    )
    summary = models.TextField("개입 요약")
    outcomes = models.TextField("성과")
    recommendations = models.TextField("추후 권고", blank=True)
    closure_reason = models.CharField("종결 사유", max_length=200)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_reports",
        verbose_name="승인자",
    )
    approved_at = models.DateTimeField("승인일", null=True, blank=True)
    pdf_file = models.FileField("PDF", upload_to=closure_report_upload_path, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "종결보고서"
        verbose_name_plural = "종결보고서"

    def __str__(self):
        return f"종결보고서 - {self.case.case_number}"


class SessionMaterial(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(
        "counseling.Case",
        on_delete=models.CASCADE,
        related_name="session_materials",
        verbose_name="사례",
        null=True,
        blank=True,
    )
    session_number = models.PositiveIntegerField("회차", null=True, blank=True)
    appointment = models.ForeignKey(
        "scheduling.Appointment",
        on_delete=models.CASCADE,
        related_name="materials",
        verbose_name="예약(회기)",
        null=True,
        blank=True,
    )
    title = models.CharField("제목", max_length=200, blank=True)
    content = models.TextField("내용", blank=True)
    file = models.FileField(
        "파일",
        upload_to=session_material_upload_path,
        blank=True,
        null=True,
    )
    is_shared = models.BooleanField(
        "사례 공유 자료",
        default=False,
        help_text="True면 상담 상세 '게시판'에 노출되는 전체 공유용 게시글입니다.",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_session_materials",
        verbose_name="업로드한 사용자",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField("수정일", auto_now=True)

    class Meta:
        verbose_name = "회기 자료"
        verbose_name_plural = "회기 자료"
        ordering = ["-created_at"]

    def __str__(self):
        label = self.title or (self.file.name.rsplit("/", 1)[-1] if self.file else "")
        if self.appointment_id:
            return f"{self.appointment_id} — {label}"
        return f"{self.case_id} {self.session_number}회기 — {label}"

    @property
    def original_filename(self) -> str:
        """첨부 파일 원본명(표시·다운로드용). get_filename()과 동일."""
        return self.get_filename()

    def get_filename(self) -> str:
        """저장된 파일의 원본 파일명(확장자 포함)."""
        if not self.file:
            return ""
        return os.path.basename(self.file.name.replace("\\", "/"))

    @property
    def post_title(self) -> str:
        """게시판 목록 제목 (사용자 입력 제목)."""
        if self.title:
            return self.title
        if self.file:
            return self.get_filename()
        return "제목 없음"

    @property
    def has_attachment(self) -> bool:
        return bool(self.file)

    @property
    def has_content(self) -> bool:
        return bool(self.content and self.content.strip())

    @property
    def display_name(self):
        return self.post_title

    def can_delete_by(self, user) -> bool:
        if not user.is_authenticated:
            return False
        if user.is_superuser or user.role == UserRole.ADMIN:
            return True
        if user.role == UserRole.COUNSELOR and self.is_shared:
            return True
        if not self.uploaded_by_id:
            return False
        return self.uploaded_by_id == user.pk

    def can_edit_by(self, user) -> bool:
        return self.can_delete_by(user) if self.is_shared else self.uploaded_by_id == user.pk

    @property
    def uploader_role_label(self) -> str:
        if not self.uploaded_by_id:
            return "알 수 없음"
        role = self.uploaded_by.role
        if role == UserRole.CLIENT:
            return "내담자"
        if role == UserRole.COUNSELOR:
            return "상담사"
        return self.uploaded_by.get_role_display()

    @property
    def uploader_icon(self) -> str:
        if not self.uploaded_by_id:
            return "bi-file-earmark"
        if self.uploaded_by.role == UserRole.CLIENT:
            return "bi-person"
        if self.uploaded_by.role == UserRole.COUNSELOR:
            return "bi-person-badge"
        if self.uploaded_by.role == UserRole.ADMIN:
            return "bi-shield-check"
        return "bi-file-earmark"


class CounselorAssignmentSubmission(models.Model):
    """상담사가 사례·회차별로 관리자에게 제출하는 과제 (HWP/PDF)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(
        "counseling.Case",
        on_delete=models.CASCADE,
        related_name="counselor_assignments",
        verbose_name="사례",
    )
    session_number = models.PositiveIntegerField(
        "회차",
        help_text="과제가 해당하는 상담 회기.",
    )
    title = models.CharField("과제명", max_length=200)
    note = models.TextField("메모", blank=True)
    file = models.FileField(
        "파일",
        upload_to=counselor_assignment_upload_path,
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="counselor_assignment_submissions",
        verbose_name="제출 상담사",
    )
    cohort = models.PositiveIntegerField(
        "기수",
        null=True,
        blank=True,
        db_index=True,
        help_text="제출 시 상담사 기수가 자동 저장됩니다.",
    )
    created_at = models.DateTimeField("최초 제출일", auto_now_add=True)
    updated_at = models.DateTimeField("최종 제출일", auto_now=True)

    class Meta:
        verbose_name = "상담사 과제 제출"
        verbose_name_plural = "상담사 과제 제출"
        ordering = ["case__case_number", "session_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["case", "session_number"],
                name="unique_counselor_assignment_per_case_session",
            ),
        ]

    def __str__(self):
        return f"{self.case.case_number} {self.session_number}회기 — {self.title}"

    def get_filename(self) -> str:
        if not self.file:
            return ""
        return os.path.basename(self.file.name.replace("\\", "/"))

    def file_is_available(self) -> bool:
        """다운로드 가능 여부 (목록·버튼 표시용).

        storage.exists()는 S3/Volume마다 수백 ms 걸릴 수 있어 페이지 로드마다
        호출하지 않습니다. 실제 파일 유무는 다운로드 시 read_assignment_file_bytes에서 검증합니다.
        """
        return bool(self.file and self.file.name)

    @property
    def session_label(self) -> str:
        return f"{self.session_number}회기"

    @property
    def was_revised(self) -> bool:
        """재업로드(덮어쓰기) 여부."""
        if not self.created_at or not self.updated_at:
            return False
        return self.updated_at - self.created_at > timedelta(seconds=1)

    def can_delete_by(self, user) -> bool:
        if not user.is_authenticated:
            return False
        if user.is_superuser or user.role == UserRole.ADMIN:
            return True
        return self.submitted_by_id == user.pk

    def save(self, *args, **kwargs):
        if self.cohort is None and self.submitted_by_id:
            profile = getattr(self.submitted_by, "counselor_profile", None)
            if profile and profile.cohort is not None:
                self.cohort = profile.cohort
        super().save(*args, **kwargs)
