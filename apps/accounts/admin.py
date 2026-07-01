from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Exists, OuterRef
from django.utils.html import format_html

from .models import AuditLog, ClientProfile, CounselorProfile, SupervisorProfile, User, UserRole


class CounselorUserFilter(admin.SimpleListFilter):
    """어드민 사용자 목록 — 상담사 계정 빠른 필터."""

    title = "상담사 계정"
    parameter_name = "counselor_accounts"

    def lookups(self, request, model_admin):
        return (
            ("yes", "상담사 역할"),
            ("approved", "승인된 상담사"),
            ("missing_profile", "프로필 없음"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "yes":
            return queryset.filter(role=UserRole.COUNSELOR)
        if value == "approved":
            return queryset.filter(
                role=UserRole.COUNSELOR,
                counselor_profile__is_approved=True,
            )
        if value == "missing_profile":
            return queryset.filter(role=UserRole.COUNSELOR, counselor_profile__isnull=True)
        return queryset


class CounselorProfileInline(admin.StackedInline):
    model = CounselorProfile
    can_delete = False
    extra = 0
    fk_name = "user"
    readonly_fields = ("created_at", "updated_at")
    fields = (
        "birth_date",
        "gender",
        "license_number",
        "specialties",
        "bio",
        "max_cases",
        "cohort",
        "is_approved",
        "created_at",
        "updated_at",
    )


class SupervisorProfileInline(admin.StackedInline):
    model = SupervisorProfile
    can_delete = False
    extra = 0
    fk_name = "user"
    readonly_fields = ("created_at", "updated_at")
    fields = (
        "assigned_cohorts",
        "created_at",
        "updated_at",
    )


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """커스텀 User — 어드민에서 사용자·상담사 계정 관리."""

    model = User
    inlines = (CounselorProfileInline,)

    list_display = (
        "name",
        "email",
        "phone",
        "role_badge",
        "status",
        "is_counselor_role",
        "has_counselor_profile",
        "counselor_approved",
        "is_active",
        "is_staff",
        "created_at",
    )
    list_filter = ("role", "status", "is_active", "is_staff", "is_superuser", CounselorUserFilter)
    search_fields = ("email", "name", "phone")
    ordering = ("-created_at",)
    list_per_page = 50
    readonly_fields = ("created_at", "updated_at", "last_login", "is_active")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("개인정보", {"fields": ("name", "phone")}),
        (
            "권한·역할",
            {
                "fields": (
                    "role",
                    "status",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        ("일시", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "name", "role", "password1", "password2"),
            },
        ),
    )
    filter_horizontal = ("groups", "user_permissions")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        counselor_profile = CounselorProfile.objects.filter(user_id=OuterRef("pk"))
        return qs.annotate(
            _has_counselor_profile=Exists(counselor_profile),
            _counselor_is_approved=Exists(
                counselor_profile.filter(is_approved=True)
            ),
        )

    def get_inlines(self, request, obj=None):
        if obj is None:
            return ()
        role = obj.role
        if request.method == "POST":
            # 저장 시 폼에서 바꾼 역할 기준 — 상담사→수퍼바이저 전환 시 구 프로필 인라인 검증 오류 방지
            posted_role = (request.POST.get("role") or "").strip()
            if posted_role:
                role = posted_role
        if role == UserRole.COUNSELOR:
            return (CounselorProfileInline,)
        if role == UserRole.SUPERVISOR:
            return (SupervisorProfileInline,)
        return ()

    @admin.display(description="역할", ordering="role")
    def role_badge(self, obj: User) -> str:
        colors = {
            UserRole.ADMIN: "#6f42c1",
            UserRole.COUNSELOR: "#0d6efd",
            UserRole.SUPERVISOR: "#fd7e14",
            UserRole.CLIENT: "#198754",
        }
        color = colors.get(obj.role, "#6c757d")
        return format_html(
            '<span style="color:{}; font-weight:600;">{}</span>',
            color,
            obj.get_role_display(),
        )

    @admin.display(description="상담사 역할", boolean=True, ordering="role")
    def is_counselor_role(self, obj: User) -> bool:
        return obj.role == UserRole.COUNSELOR

    @admin.display(description="상담사 프로필", boolean=True)
    def has_counselor_profile(self, obj: User) -> bool:
        if hasattr(obj, "_has_counselor_profile"):
            return obj._has_counselor_profile
        return CounselorProfile.objects.filter(user_id=obj.pk).exists()

    @admin.display(description="상담사 승인", boolean=True)
    def counselor_approved(self, obj: User) -> bool:
        if obj.role != UserRole.COUNSELOR:
            return False
        if hasattr(obj, "_counselor_is_approved"):
            return obj._counselor_is_approved
        try:
            return obj.counselor_profile.is_approved
        except CounselorProfile.DoesNotExist:
            return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from apps.accounts.profile_sync import sync_user_role_profiles

        sync_user_role_profiles(obj)


@admin.register(CounselorProfile)
class CounselorProfileAdmin(admin.ModelAdmin):
    """상담사 프로필 — 승인·전문분야·동시 사례 수 관리."""

    list_display = (
        "user_name",
        "user_email",
        "user_phone",
        "user_status",
        "birth_date",
        "gender",
        "license_number",
        "specialties_summary",
        "max_cases",
        "cohort",
        "is_approved",
        "created_at",
    )
    list_filter = ("is_approved", "cohort", "user__status")
    search_fields = ("user__name", "user__email", "license_number", "bio")
    list_editable = ("is_approved",)
    ordering = ("user__name",)
    list_select_related = ("user",)
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("user",)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(user__role=UserRole.COUNSELOR)

    def save_model(self, request, obj, form, change):
        from django.contrib import messages

        if obj.user.role != UserRole.COUNSELOR:
            messages.error(
                request,
                "상담사(COUNSELOR) 역할 계정에만 상담사 프로필을 연결할 수 있습니다.",
            )
            return
        if obj.is_approved and obj.cohort is None:
            messages.error(request, "상담사 승인 시 기수를 입력해야 합니다.")
            return
        super().save_model(request, obj, form, change)

    fieldsets = (
        (None, {"fields": ("user",)}),
        (
            "상담사 정보",
            {
                "fields": (
                    "birth_date",
                    "gender",
                    "license_number",
                    "specialties",
                    "bio",
                    "max_cases",
                    "cohort",
                    "is_approved",
                ),
            },
        ),
        ("일시", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="이름", ordering="user__name")
    def user_name(self, obj: CounselorProfile) -> str:
        return obj.user.name

    @admin.display(description="이메일", ordering="user__email")
    def user_email(self, obj: CounselorProfile) -> str:
        return obj.user.email

    @admin.display(description="휴대폰", ordering="user__phone")
    def user_phone(self, obj: CounselorProfile) -> str:
        return obj.user.phone or "—"

    @admin.display(description="계정 상태", ordering="user__status")
    def user_status(self, obj: CounselorProfile) -> str:
        return obj.user.get_status_display()

    @admin.display(description="전문분야")
    def specialties_summary(self, obj: CounselorProfile) -> str:
        if not obj.specialties:
            return "—"
        text = ", ".join(str(s) for s in obj.specialties)
        return text if len(text) <= 40 else f"{text[:37]}..."


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user_name",
        "user_email",
        "student_id",
        "is_kcu_student",
        "department",
        "birth_date",
    )
    list_filter = ("is_kcu_student",)
    search_fields = ("user__name", "user__email", "student_id", "department")
    list_select_related = ("user",)
    autocomplete_fields = ("user",)

    @admin.display(description="이름", ordering="user__name")
    def user_name(self, obj: ClientProfile) -> str:
        return obj.user.name

    @admin.display(description="이메일", ordering="user__email")
    def user_email(self, obj: ClientProfile) -> str:
        return obj.user.email


@admin.register(SupervisorProfile)
class SupervisorProfileAdmin(admin.ModelAdmin):
    list_display = ("user_name", "user_email", "assigned_cohorts", "updated_at")
    search_fields = ("user__name", "user__email")
    list_select_related = ("user",)
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="이름", ordering="user__name")
    def user_name(self, obj: SupervisorProfile) -> str:
        return obj.user.name

    @admin.display(description="이메일", ordering="user__email")
    def user_email(self, obj: SupervisorProfile) -> str:
        return obj.user.email


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "user", "target_type", "ip_address", "created_at")
    list_filter = ("action", "target_type")
    search_fields = ("action", "user__email", "target_type")
    readonly_fields = ("user", "action", "target_type", "target_id", "ip_address", "created_at")
