import io
import logging
import mimetypes
import zipfile

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import RelatedOnlyFieldListFilter
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import content_disposition_header

from apps.counseling.admin_lock import RecordsSubmittedLockMixin
from apps.counseling.cohort_journal_service import supervision_cohort_for_case
from apps.counseling.models import Case
from apps.documents.access import user_can_replace_consent
from apps.documents.forms import AdminConsentReplaceForm
from apps.documents.models import (
    COUNSELOR_REQUIRED_DOC_TYPES,
    DOC_TYPE_FILENAME_LABEL,
    ClosureReport,
    ConsentDocument,
    ConsentDocumentReplacement,
    ConsentDocType,
    SessionMaterial,
    _consent_file_storage,
)
from apps.documents.services.consent_service import replace_consent_file_for_admin
from apps.documents.views import _consent_file_response

logger = logging.getLogger(__name__)


class ConsentDocumentAdminForm(forms.ModelForm):
    class Meta:
        model = ConsentDocument
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        client = cleaned.get("client")
        application = cleaned.get("application")
        if client and application and application.client_id != client.pk:
            raise forms.ValidationError("내담자와 상담 신청의 내담자가 일치하지 않습니다.")
        return cleaned


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
class ConsentDocumentAdmin(RecordsSubmittedLockMixin, admin.ModelAdmin):
    form = ConsentDocumentAdminForm
    raw_id_fields = ("client", "application", "uploaded_by")
    list_display = (
        "client",
        "counselor_display",
        "doc_type",
        "signed_at",
        "updated_at",
        "file_view_link",
        "file_download_link",
        "file_replace_link",
    )
    list_filter = (
        RequiredDocTypeFilter,
        ("signed_at", admin.DateFieldListFilter),
        ("updated_at", admin.DateFieldListFilter),
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
    date_hierarchy = "updated_at"
    ordering = ("-updated_at", "-signed_at")
    list_select_related = (
        "client",
        "application",
        "application__case",
        "application__case__counselor",
        "uploaded_by",
    )
    readonly_fields = ("signed_at", "updated_at", "file_download", "file_replace_actions")
    actions = ("download_selected_consents_zip",)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "replace/<uuid:case_id>/<str:doc_type>/",
                self.admin_site.admin_view(self.replace_consent_file),
                name="documents_consentdocument_replace",
            ),
            path(
                "replacement/<uuid:replacement_id>/<str:which>/",
                self.admin_site.admin_view(self.serve_replacement_file),
                name="documents_consentdocument_replacement_file",
            ),
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
        # Keep excluding the field and use the replace view so existing files are not overwritten.
        excluded = ["verified_by"]
        if obj is not None:
            excluded.append("file")
        return tuple(excluded)

    def _require_replace_permission(self, request):
        if not user_can_replace_consent(request.user):
            raise PermissionDenied

    def _replace_url(self, case, doc_type):
        return reverse(
            "admin:documents_consentdocument_replace",
            args=[case.pk, doc_type],
        )

    def replace_consent_file(self, request, case_id, doc_type):
        self._require_replace_permission(request)
        case = get_object_or_404(
            Case.objects.select_related(
                "client",
                "counselor",
                "training_enrollment",
                "application",
            ),
            pk=case_id,
        )
        doc_type = (doc_type or "").upper()
        if doc_type not in COUNSELOR_REQUIRED_DOC_TYPES:
            raise Http404("Document type not found")
        consent = ConsentDocument.objects.filter(
            application=case.application,
            doc_type=doc_type,
        ).first()
        form = AdminConsentReplaceForm(request.POST, request.FILES) if request.method == "POST" else AdminConsentReplaceForm()
        if request.method == "POST" and form.is_valid():
            try:
                consent, _history = replace_consent_file_for_admin(
                    case=case,
                    doc_type=doc_type,
                    file_obj=form.cleaned_data["file"],
                    replaced_by=request.user,
                    reason=form.cleaned_data["reason"],
                )
            except ValueError:
                raise Http404 from None
            LogEntry.objects.create(
                user_id=request.user.pk,
                content_type_id=ContentType.objects.get_for_model(ConsentDocument).pk,
                object_id=str(consent.pk),
                object_repr=f"{case.case_number} {doc_type}",
                action_flag=CHANGE,
                change_message="첨부파일 교체",
            )
            self.message_user(request, "첨부파일을 교체했습니다.", level=messages.SUCCESS)
            return redirect(
                reverse("admin:documents_consentdocument_change", args=[consent.pk])
            )
        cohort = supervision_cohort_for_case(case)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "첨부파일 교체",
            "form": form,
            "case": case,
            "consent": consent,
            "doc_type": doc_type,
            "doc_label": DOC_TYPE_FILENAME_LABEL.get(doc_type, doc_type),
            "cohort_label": f"{cohort}기" if cohort else "기수미정",
            "has_current_file": bool(consent and consent.file and consent.file.name),
            "current_file_url": (
                self._consent_file_url(consent, inline=True)
                if consent and consent.file and consent.file.name
                else ""
            ),
            "current_filename": (
                consent.get_download_filename()
                if consent and consent.file
                else ""
            ),
        }
        return TemplateResponse(request, "admin/documents/consent_replace.html", context)

    def serve_replacement_file(self, request, replacement_id, which):
        self._require_replace_permission(request)
        if which not in ("previous", "new"):
            raise Http404("File not found")
        record = get_object_or_404(ConsentDocumentReplacement, pk=replacement_id)
        name = record.previous_file_name if which == "previous" else record.new_file_name
        normalized = (name or "").replace("\\", "/")
        if not normalized or ".." in normalized.split("/"):
            raise Http404("File not found")
        storage = _consent_file_storage()
        filename = normalized.rsplit("/", 1)[-1] or "consent.bin"
        content_type, _ = mimetypes.guess_type(filename)
        content_type = content_type or "application/octet-stream"
        try:
            handle = storage.open(normalized, "rb")
        except FileNotFoundError as exc:
            raise Http404("File not found") from exc
        response = FileResponse(handle, content_type=content_type)
        response["Cache-Control"] = "private, no-store, max-age=0"
        inline = request.GET.get("disposition", "attachment") == "inline"
        response["Content-Disposition"] = content_disposition_header(
            as_attachment=not inline,
            filename=filename,
        )
        return response

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

    @admin.display(description="교체")
    def file_replace_link(self, obj):
        case = getattr(getattr(obj, "application", None), "case", None)
        if obj is None or case is None or obj.doc_type not in COUNSELOR_REQUIRED_DOC_TYPES:
            return "—"
        return format_html(
            '<a href="{}">파일 교체</a>',
            self._replace_url(case, obj.doc_type),
        )

    @admin.display(description="첨부파일")
    def file_replace_actions(self, obj):
        case = getattr(getattr(obj, "application", None), "case", None)
        if obj is None or case is None or obj.doc_type not in COUNSELOR_REQUIRED_DOC_TYPES:
            return "—"
        replace = format_html(
            '<a class="button" href="{}">파일 교체</a>',
            self._replace_url(case, obj.doc_type),
        )
        if obj.file and obj.file.name:
            view = format_html(
                '<a class="button" href="{}" target="_blank" rel="noopener noreferrer">현재 파일 보기</a>',
                self._consent_file_url(obj, inline=True),
            )
            return format_html("{} {}", view, replace)
        return format_html("<span>첨부파일 없음</span> {}", replace)

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


@admin.register(ConsentDocumentReplacement)
class ConsentDocumentReplacementAdmin(admin.ModelAdmin):
    list_display = (
        "replaced_at",
        "replaced_by",
        "case_number",
        "doc_type",
        "previous_file_link",
        "new_file_link",
    )
    list_filter = ("doc_type", ("replaced_at", admin.DateFieldListFilter))
    search_fields = ("case_number", "reason")
    ordering = ("-replaced_at",)
    readonly_fields = (
        "replaced_at",
        "replaced_by",
        "case",
        "case_number",
        "consent",
        "doc_type",
        "previous_file_name",
        "new_file_name",
        "reason",
        "previous_file_link",
        "new_file_link",
    )

    def has_module_permission(self, request):
        return user_can_replace_consent(request.user)

    def has_view_permission(self, request, obj=None):
        return user_can_replace_consent(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def _history_file_url(self, obj, which):
        return reverse(
            "admin:documents_consentdocument_replacement_file",
            args=[obj.pk, which],
        )

    @admin.display(description="기존 파일")
    def previous_file_link(self, obj):
        if not obj or not obj.previous_file_name:
            return "없음"
        return format_html(
            '<a href="{}">보기</a>',
            self._history_file_url(obj, "previous"),
        )

    @admin.display(description="교체 파일")
    def new_file_link(self, obj):
        if not obj or not obj.new_file_name:
            return "—"
        return format_html(
            '<a href="{}">보기</a>',
            self._history_file_url(obj, "new"),
        )


@admin.register(ClosureReport)
class ClosureReportAdmin(RecordsSubmittedLockMixin, admin.ModelAdmin):
    list_display = ("case", "counselor", "closure_reason", "approved_by", "created_at")
    search_fields = ("case__case_number", "counselor__name")


@admin.register(SessionMaterial)
class SessionMaterialAdmin(RecordsSubmittedLockMixin, admin.ModelAdmin):
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
