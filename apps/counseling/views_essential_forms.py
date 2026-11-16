"""상담사 대시보드 필수서식 다운로드."""

from django.http import FileResponse
from django.views.decorators.http import require_GET

from apps.accounts.decorators import counselor_required

from .essential_forms import get_essential_form


@counselor_required
@require_GET
def counselor_essential_form_download(request, form_key: str):
    form = get_essential_form(form_key)
    return FileResponse(
        form["path"].open("rb"),
        as_attachment=True,
        filename=form["download_name"],
        content_type="application/octet-stream",
    )
