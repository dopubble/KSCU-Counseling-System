from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Exists, OuterRef
from django.utils.html import format_html

from .models import AuditLog, ClientProfile, CounselorProfile, User, UserRole


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
    fields = (
        "license_number",
        "birth_date",
        "gender",
        "specialties",
        "bio",
        "max_cases",
        "is_approved",
    )


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """커스텀 User — 어드민에서 사용자·상담사 계정 관리."""

    model = User
    inlines = (CounselorProfileInline,)

    list_display = (
        "name",
        "email",
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
        if obj is not None and obj.role == UserRole.COUNSELOR:
            return (CounselorProfileInline,)
        return ()

    @admin.display(description="역할", ordering="role")
    def role_badge(self, obj: User) -> str:
        colors = {
            UserRole.ADMIN: "#6f42c1",
            UserRole.COUNSELOR: "#0d6efd",
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


@admin.register(CounselorProfile)
class CounselorProfileAdmin(admin.ModelAdmin):
    """상담사 프로필 — 승인·전문분야·동시 사례 수 관리."""

    list_display = (
        "user_name",
        "user_email",
        "birth_date",
        "gender",
        "user_status",
        "license_number",
        "specialties_summary",
        "max_cases",
        "is_approved",
        "created_at",
    )
    list_filter = ("is_approved", "user__status")
    search_fields = ("user__name", "user__email", "license_number", "bio")
    list_editable = ("is_approved",)
    ordering = ("user__name",)
    list_select_related = ("user",)
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("user",)

    fieldsets = (
        (None, {"fields": ("user",)}),
        (
            "상담사 정보",
            {"fields": ("license_number", "birth_date", "gender", "specialties", "bio", "max_cases", "is_approved")},
        ),
        ("일시", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="이름", ordering="user__name")
    def user_name(self, obj: CounselorProfile) -> str:
        return obj.user.name

    @admin.display(description="이메일", ordering="user__email")
    def user_email(self, obj: CounselorProfile) -> str:
        return obj.user.email

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
        "gender",
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


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "user", "target_type", "ip_address", "created_at")
    list_filter = ("action", "target_type")
    search_fields = ("action", "user__email", "target_type")
    readonly_fields = ("user", "action", "target_type", "target_id", "ip_address", "created_at")
