import mimetypes

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404

from apps.documents.access import user_can_access_consent
from apps.documents.models import ConsentDocument


def _consent_file_response(consent, *, inline: bool):
    if not consent.file:
        raise Http404("File not found")
    filename = consent.get_download_filename()
    content_type, _ = mimetypes.guess_type(filename)
    content_type = content_type or "application/octet-stream"
    response = FileResponse(
        consent.file.open("rb"),
        content_type=content_type,
        as_attachment=not inline,
        filename=filename,
    )
    if inline:
        response["Content-Disposition"] = f'inline; filename="{filename}"'
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
