import io
import logging
import zipfile

from django.contrib import admin, messages
from django.contrib.admin import RelatedOnlyFieldListFilter
from django.http import Http404, HttpResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import content_disposition_header

from apps.documents.views import _consent_file_response

from .models import (
    COUNSELOR_REQUIRED_DOC_TYPES,
    ClosureReport,
    ConsentDocument,
    ConsentDocType,
    SessionMaterial,
)

logger = logging.getLogger(__name__)


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

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<uuid:object_id>/file/",
                self.admin_site.admin_view(self.serve_consent_file),
                name="documents_consentdocument_file",
            ),
        ]
        return custom + urls

    def serve_consent_file(self, request, object_id):
        consent = self.get_object(
            request,
            str(object_id),
            from_field=None,
        )
        if consent is None:
            logger.warning(
                "consent admin file: document not found object_id=%s user=%s",
                object_id,
                getattr(request.user, "email", None),
            )
            raise Http404("Consent document not found")
        inline = request.GET.get("disposition", "attachment") == "inline"
        return _consent_file_response(consent, inline=inline)

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
        url = reverse("admin:documents_consentdocument_file", args=[obj.pk])
        if inline:
            return f"{url}?disposition=inline"
        return url

    def _consent_file_missing_label(self, obj):
        if not obj:
            return "—"
        deleted_at = timezone.localtime(obj.updated_at)
        return format_html(
            '<span class="text-muted" title="파일 삭제됨">삭제 {}</span>',
            deleted_at.strftime("%Y-%m-%d %H:%M"),
        )

    @admin.display(description="보기")
    def file_view_link(self, obj):
        if not obj or not obj.file or not obj.file.name:
            return self._consent_file_missing_label(obj)
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">보기</a>',
            self._consent_file_url(obj, inline=True),
        )

    @admin.display(description="다운로드")
    def file_download_link(self, obj):
        if not obj or not obj.file or not obj.file.name:
            return self._consent_file_missing_label(obj)
        return format_html(
            '<a href="{}">다운로드</a>',
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

    @admin.action(description="선택한 동의서 일괄 다운로드 (ZIP)")
    def download_selected_consents_zip(self, request, queryset):
        queryset = queryset.select_related("client", "application__case__counselor")
        buffer = io.BytesIO()
        added = 0
        used_names: set[str] = set()

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for consent in queryset:
                if not consent.file or not consent.file.name:
                    continue
                filename = consent.get_download_filename() or "consent.bin"
                if filename in used_names:
                    filename = f"{consent.pk}_{filename}"
                used_names.add(filename)
                try:
                    with consent.file.open("rb") as handle:
                        entry = zipfile.ZipInfo(filename)
                        entry.flag_bits |= 0x800  # UTF-8 filename
                        archive.writestr(entry, handle.read())
                    added += 1
                except FileNotFoundError as exc:
                    logger.warning(
                        "consent zip skip missing file pk=%s name=%r err=%s",
                        consent.pk,
                        consent.file.name,
                        exc,
                    )
                    continue

        if added == 0:
            self.message_user(
                request,
                "ZIP에 포함할 파일이 없습니다.",
                level=messages.WARNING,
            )
            return None

        buffer.seek(0)
        zip_name = timezone.localtime().strftime("동의서_일괄다운로드_%Y%m%d_%H%M%S.zip")
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = content_disposition_header(
            as_attachment=True,
            filename=zip_name,
        )
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
