from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import AuditLog, ClientProfile, CounselorProfile, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "name", "role", "status", "is_active", "is_staff", "created_at")
    list_filter = ("role", "status", "is_active", "is_staff")
    search_fields = ("email", "name", "phone")
    ordering = ("-created_at",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("개인정보", {"fields": ("name", "phone")}),
        ("권한", {"fields": ("role", "status", "is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("일시", {"fields": ("last_login", "created_at")}),
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
    readonly_fields = ("created_at", "last_login", "is_active")


@admin.register(CounselorProfile)
class CounselorProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "license_number", "is_approved", "max_cases")
    list_filter = ("is_approved",)
    search_fields = ("user__name", "user__email", "license_number")


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "student_id", "emergency_contact", "birth_date", "gender")
    search_fields = ("user__name", "user__email", "student_id", "emergency_contact")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "user", "target_type", "ip_address", "created_at")
    list_filter = ("action", "target_type")
    readonly_fields = ("user", "action", "target_type", "target_id", "ip_address", "created_at")
