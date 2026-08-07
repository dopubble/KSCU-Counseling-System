import logging
import mimetypes

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header

from apps.documents.access import user_can_access_consent
from apps.documents.models import ConsentDocument

logger = logging.getLogger(__name__)


def _consent_file_response(consent, *, inline: bool):
    if not consent.file or not consent.file.name:
        logger.warning(
            "consent file missing metadata pk=%s name=%r",
            consent.pk,
            getattr(consent.file, "name", None),
        )
        raise Http404("Consent file not found")
    filename = consent.get_download_filename() or "consent.bin"
    content_type, _ = mimetypes.guess_type(filename)
    content_type = content_type or "application/octet-stream"
    try:
        file_handle = consent.file.open("rb")
    except FileNotFoundError as exc:
        logger.warning(
            "consent file missing in storage pk=%s name=%r err=%s",
            consent.pk,
            consent.file.name,
            exc,
        )
        raise Http404("Consent file not found") from exc

    response = FileResponse(file_handle, content_type=content_type)
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=not inline,
        filename=filename,
    )
    return response


@login_required
def consent_file(request, pk):
    consent = get_object_or_404(
        ConsentDocument.objects.select_related(
            "application__case__counselor",
            "application__case__client",
            "client",
        ),
        pk=pk,
    )
    if not user_can_access_consent(request.user, consent):
        raise PermissionDenied
    inline = request.GET.get("disposition", "attachment") == "inline"
    return _consent_file_response(consent, inline=inline)
