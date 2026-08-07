import io
import zipfile

from django.contrib import admin, messages
from django.contrib.admin import RelatedOnlyFieldListFilter
from django.http import HttpResponse
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    COUNSELOR_REQUIRED_DOC_TYPES,
    ClosureReport,
    ConsentDocument,
    ConsentDocType,
    SessionMaterial,
)


class RequiredDocTypeFilter(admin.SimpleListFilter):
    title = "동의서 종류"
    parameter_name = "doc_type"

    def lookups(self, request, model_admin):
        labels = dict(ConsentDocType.choices)
        return [
            (value, labels.get(value, value))
            for value in COUNSELOR_REQUIRED_DOC_TYPES
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            return queryset.filter(doc_type=value)
        return queryset


@admin.register(ConsentDocument)
class ConsentDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "client",
        "counselor_display",
        "doc_type",
        "signed_at",
        "file_view_link",
        "file_download_link",
    )
    list_filter = (
        RequiredDocTypeFilter,
        ("signed_at", admin.DateFieldListFilter),
        ("client", RelatedOnlyFieldListFilter),
        ("application", RelatedOnlyFieldListFilter),
    )
    search_fields = (
        "client__name",
        "client__email",
        "application__case__counselor__name",
        "application__case__counselor__email",
        "application__case__case_number",
    )
    date_hierarchy = "signed_at"
    ordering = ("-signed_at", "-updated_at")
    list_select_related = (
        "client",
        "application",
        "application__case",
        "application__case__counselor",
        "uploaded_by",
    )
    readonly_fields = ("signed_at", "updated_at", "file_download")
    actions = ("download_selected_consents_zip",)

    def get_exclude(self, request, obj=None):
        # ConsentMediaStorage has no url(); Admin FileField widget calls file.url on change.
        excluded = ["verified_by"]
        if obj is not None:
            excluded.append("file")
        return tuple(excluded)

    @admin.display(description="상담사", ordering="application__case__counselor__name")
    def counselor_display(self, obj):
        case = getattr(obj.application, "case", None)
        counselor = getattr(case, "counselor", None) if case else None
        if counselor:
            return counselor.name
        if obj.uploaded_by_id:
            return obj.uploaded_by.name
        return "—"

    def _consent_file_url(self, obj, *, inline: bool) -> str:
        url = reverse("documents:consent_file", kwargs={"pk": obj.pk})
        if inline:
            return f"{url}?disposition=inline"
        return url

    @admin.display(description="보기")
    def file_view_link(self, obj):
        if not obj or not obj.file:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">보기</a>',
            self._consent_file_url(obj, inline=True),
        )

    @admin.display(description="다운로드")
    def file_download_link(self, obj):
        if not obj or not obj.file:
            return "—"
        return format_html(
            '<a href="{}" download>다운로드</a>',
            self._consent_file_url(obj, inline=False),
        )

    @admin.display(description="파일")
    def file_download(self, obj):
        if not obj or not obj.file:
            return "—"
        url = self._consent_file_url(obj, inline=True)
        filename = obj.get_download_filename()
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            url,
            filename,
        )

    @admin.action(description="선택한 동의서 ZIP 다운로드")
    def download_selected_consents_zip(self, request, queryset):
        queryset = queryset.select_related("client", "application__case__counselor")
        buffer = io.BytesIO()
        added = 0
        used_names: set[str] = set()

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for consent in queryset:
                if not consent.file:
                    continue
                filename = consent.get_download_filename() or "consent.bin"
                if filename in used_names:
                    filename = f"{consent.pk}_{filename}"
                used_names.add(filename)
                try:
                    with consent.file.open("rb") as handle:
                        archive.writestr(filename, handle.read())
                    added += 1
                except FileNotFoundError:
                    continue

        if added == 0:
            self.message_user(
                request,
                "ZIP에 포함할 파일이 없습니다.",
                level=messages.WARNING,
            )
            return None

        buffer.seek(0)
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = 'attachment; filename="consents.zip"'
        return response


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
