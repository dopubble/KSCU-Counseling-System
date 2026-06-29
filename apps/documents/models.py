import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import get_valid_filename

from apps.accounts.models import UserRole


def _consent_file_storage():
    """런타임에 STORAGES['consent'] 를 해석 (레거시/동의서 분리)."""
    from django.core.files.storage import storages

    return storages["consent"]


def _upload_basename(filename: str) -> str:
    """업로드 파일의 원본 파일명(경로 제거·안전 문자만)."""
    basename = os.path.basename((filename or "").replace("\\", "/"))
    return get_valid_filename(basename) or "file"


def _consent_safe_filename(name: str) -> str:
    """동의서 표준 파일명 — 대괄호·한글 유지, 경로 구분자만 제거."""
    forbidden = set('<>:"/\\|?*\0')
    cleaned = "".join(c if c not in forbidden else "_" for c in (name or ""))
    return cleaned.strip() or "consent.pdf"


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
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "bin").lower()
    display_name = instance.build_storage_basename(ext)
    app_id = instance.application_id or "unknown"
    return f"consents/{app_id}/{display_name}"


def closure_report_upload_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1]
    return f"closure_reports/{instance.case_id}/{uuid.uuid4()}.{ext}"



def counselor_assignment_upload_path(instance, filename):
    """레거시 migration(0006) 참조용 — CounselorAssignmentSubmission 모델은 제거됨."""
    basename = _upload_basename(filename)
    date_path = timezone.now().strftime("%Y/%m/%d")
    return f"counselor_assignments/{instance.case_id}/{date_path}/{basename}"


class ConsentDocType(models.TextChoices):
    PRIVACY = "PRIVACY", "개인정보 처리방침 동의서"
    INTAKE = "INTAKE", "접수면접지"
    COUNSELING = "COUNSELING", "상담 동의서"
    RECORDING = "RECORDING", "녹화 동의"


COUNSELOR_REQUIRED_DOC_TYPES = (
    ConsentDocType.PRIVACY,
    ConsentDocType.INTAKE,
    ConsentDocType.COUNSELING,
)

DOC_TYPE_FILENAME_LABEL = {
    ConsentDocType.PRIVACY: "개인정보동의서",
    ConsentDocType.INTAKE: "접수면접지",
    ConsentDocType.COUNSELING: "상담동의서",
    ConsentDocType.RECORDING: "녹화동의서",
}


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
    file = models.FileField(
        "파일",
        upload_to=consent_upload_path,
        storage=_consent_file_storage,
    )
    signed_at = models.DateTimeField("서명일", auto_now_add=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_consents",
        verbose_name="검증자",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_consent_documents",
        verbose_name="업로드한 사용자",
    )
    updated_at = models.DateTimeField("최종 수정일", auto_now=True)

    class Meta:
        verbose_name = "동의서"
        verbose_name_plural = "동의서"
        unique_together = [("application", "doc_type")]

    def __str__(self):
        return f"{self.client.name} - {self.get_doc_type_display()}"

    def build_storage_basename(self, ext: str) -> str:
        """[N기]상담사명_내담자명_서류종류.ext"""
        from apps.counseling.cohort_journal_service import get_counselor_cohort

        case = getattr(self.application, "case", None)
        counselor = case.counselor if case else None
        cohort = get_counselor_cohort(counselor) if counselor else None
        cohort_part = f"[{cohort}기]" if cohort else "[기수미정]"
        counselor_name = (counselor.name if counselor else "상담사미배정").replace(" ", "")
        client_name = self.client.name.replace(" ", "")
        label = DOC_TYPE_FILENAME_LABEL.get(self.doc_type, self.doc_type)
        safe = _consent_safe_filename(
            f"{cohort_part}{counselor_name}_{client_name}_{label}.{ext}"
        )
        return safe or f"consent.{ext}"

    def get_download_filename(self) -> str:
        if not self.file:
            return ""
        stored = os.path.basename(self.file.name.replace("\\", "/"))
        if "." in stored:
            ext = stored.rsplit(".", 1)[-1]
        else:
            ext = "bin"
        return self.build_storage_basename(ext)

    @property
    def is_submitted(self) -> bool:
        return bool(self.file)


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
