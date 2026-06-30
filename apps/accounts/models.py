import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class UserRole(models.TextChoices):
    ADMIN = "ADMIN", "관리자"
    SUPERVISOR = "SUPERVISOR", "수퍼바이저"
    COUNSELOR = "COUNSELOR", "상담사"
    CLIENT = "CLIENT", "내담자"


class UserStatus(models.TextChoices):
    PENDING = "PENDING", "승인대기"
    ACTIVE = "ACTIVE", "활성"
    INACTIVE = "INACTIVE", "비활성"
    SUSPENDED = "SUSPENDED", "정지"


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("이메일은 필수입니다.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", UserRole.ADMIN)
        extra_fields.setdefault("status", UserStatus.ACTIVE)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("이메일", unique=True)
    name = models.CharField(
        "이름",
        max_length=100,
        help_text="회원가입 시 확정되며, 내담자 회원정보 수정 화면에서는 변경할 수 없습니다.",
    )
    phone = models.CharField("휴대폰", max_length=20, blank=True)
    role = models.CharField("역할", max_length=20, choices=UserRole.choices, default=UserRole.CLIENT)
    status = models.CharField(
        "상태", max_length=20, choices=UserStatus.choices, default=UserStatus.PENDING
    )
    is_active = models.BooleanField("계정 활성", default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField("가입일", default=timezone.now)
    updated_at = models.DateTimeField("수정일", auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        verbose_name = "사용자"
        verbose_name_plural = "사용자"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"

    @property
    def is_admin(self):
        return self.role == UserRole.ADMIN

    @property
    def is_supervisor(self):
        return self.role == UserRole.SUPERVISOR

    @property
    def is_counselor(self):
        return self.role == UserRole.COUNSELOR

    @property
    def is_client(self):
        return self.role == UserRole.CLIENT

    @property
    def is_active_user(self):
        return self.status == UserStatus.ACTIVE

    def save(self, *args, **kwargs):
        self.is_active = self.status == UserStatus.ACTIVE
        super().save(*args, **kwargs)
        from apps.accounts.profile_sync import sync_user_role_profiles

        sync_user_role_profiles(self)


class CounselorProfile(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="counselor_profile", verbose_name="사용자"
    )
    license_number = models.CharField("자격증 번호", max_length=100, blank=True)
    birth_date = models.DateField("생년월일", null=True, blank=True)
    gender = models.CharField("성별", max_length=10, blank=True)
    specialties = models.JSONField("전문분야", default=list, blank=True)
    bio = models.TextField("소개", blank=True)
    max_cases = models.PositiveIntegerField("최대 동시 사례 수", default=10)
    cohort = models.PositiveIntegerField(
        "기수",
        null=True,
        blank=True,
        help_text="수련 기수(예: 100). 관리자 승인 시 필수 입력.",
    )
    is_approved = models.BooleanField("승인 여부", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "상담사 프로필"
        verbose_name_plural = "상담사 프로필"

    def __str__(self):
        return f"상담사: {self.user.name}"

    def clean(self):
        super().clean()
        if self.user_id and self.user.role != UserRole.COUNSELOR:
            raise ValidationError(
                {"user": "상담사(COUNSELOR) 역할 계정만 상담사 프로필을 가질 수 있습니다."}
            )
        if self.is_approved and self.cohort is None:
            raise ValidationError({"cohort": "상담사 승인 시 기수를 입력해야 합니다."})


class SupervisorProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="supervisor_profile",
        verbose_name="사용자",
    )
    assigned_cohorts = models.JSONField(
        "담당 기수",
        default=list,
        blank=True,
        help_text="담당 수련 기수 목록. 예: [1, 2]",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "수퍼바이저 프로필"
        verbose_name_plural = "수퍼바이저 프로필"

    def __str__(self):
        return f"수퍼바이저: {self.user.name}"

    def clean(self):
        super().clean()
        if self.user_id and self.user.role != UserRole.SUPERVISOR:
            raise ValidationError(
                {"user": "수퍼바이저(SUPERVISOR) 역할 계정만 수퍼바이저 프로필을 가질 수 있습니다."}
            )


class ClientProfile(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="client_profile", verbose_name="사용자"
    )
    student_id = models.CharField(
        "학번",
        max_length=20,
        blank=True,
        help_text="선택 사항. 내담자 내정보에서 수정할 수 있습니다.",
    )
    birth_date = models.DateField(
        "생년월일",
        null=True,
        blank=True,
        help_text="내담자 내정보에서 수정할 수 있습니다.",
    )
    is_kcu_student = models.BooleanField(
        "숭실사이버대학교 학생 여부",
        default=False,
    )
    department = models.CharField(
        "소속 학과",
        max_length=100,
        blank=True,
        help_text="숭실사이버대학교 학생인 경우 회원가입 시 입력합니다.",
    )
    gender = models.CharField("성별", max_length=10, blank=True)
    emergency_contact = models.CharField("비상연락처", max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "내담자 프로필"
        verbose_name_plural = "내담자 프로필"

    def __str__(self):
        return f"내담자: {self.user.name}"


class AuditLog(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="사용자"
    )
    action = models.CharField("행동", max_length=100)
    target_type = models.CharField("대상 유형", max_length=100, blank=True)
    target_id = models.UUIDField("대상 ID", null=True, blank=True)
    ip_address = models.GenericIPAddressField("IP 주소", null=True, blank=True)
    created_at = models.DateTimeField("일시", auto_now_add=True)

    class Meta:
        verbose_name = "감사 로그"
        verbose_name_plural = "감사 로그"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} - {self.created_at:%Y-%m-%d %H:%M}"
