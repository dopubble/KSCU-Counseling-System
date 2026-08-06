from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import ClosureReport, ConsentDocument, SessionMaterial


@admin.register(ConsentDocument)
class ConsentDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "client",
        "application",
        "doc_type",
        "signed_at",
        "updated_at",
        "uploaded_by",
        "verified_by",
    )
    list_filter = ("doc_type",)
    readonly_fields = ("signed_at", "updated_at", "file_download")

    def get_exclude(self, request, obj=None):
        # ConsentMediaStorage has no url(); Admin FileField widget calls file.url on change.
        if obj is not None:
            return ("file",)
        return super().get_exclude(request, obj)

    @admin.display(description="파일")
    def file_download(self, obj):
        if not obj or not obj.file:
            return "—"
        url = reverse("documents:consent_file", kwargs={"pk": obj.pk})
        filename = obj.get_download_filename()
        return format_html(
            '<a href="{}?disposition=inline" target="_blank" rel="noopener noreferrer">{}</a>',
            url,
            filename,
        )


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
