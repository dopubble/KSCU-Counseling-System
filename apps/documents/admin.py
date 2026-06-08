from django.contrib import admin

from .models import ClosureReport, ConsentDocument, CounselorAssignmentSubmission, SessionMaterial


@admin.register(ConsentDocument)
class ConsentDocumentAdmin(admin.ModelAdmin):
    list_display = ("client", "application", "doc_type", "signed_at", "verified_by")
    list_filter = ("doc_type",)


@admin.register(ClosureReport)
class ClosureReportAdmin(admin.ModelAdmin):
    list_display = ("case", "counselor", "closure_reason", "approved_by", "created_at")
    search_fields = ("case__case_number", "counselor__name")


@admin.register(SessionMaterial)
class SessionMaterialAdmin(admin.ModelAdmin):
    list_display = (
        "case",
        "session_number",
        "title",
        "is_shared",
        "uploaded_by",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_shared", "created_at")
    search_fields = ("title", "content", "case__case_number", "appointment__case__case_number")
    autocomplete_fields = ("case", "appointment", "uploaded_by")
    list_editable = ("is_shared",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(CounselorAssignmentSubmission)
class CounselorAssignmentSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "case",
        "session_number",
        "title",
        "submitted_by",
        "updated_at",
        "created_at",
    )
    list_filter = ("session_number", "updated_at")
    search_fields = (
        "title",
        "note",
        "case__case_number",
        "submitted_by__name",
        "submitted_by__email",
        "case__client__name",
    )
    autocomplete_fields = ("case", "submitted_by")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("case__case_number", "session_number")
