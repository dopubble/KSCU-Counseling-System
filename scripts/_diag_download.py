import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kscu_counseling.settings.development")
django.setup()

from urllib.parse import quote

from django.http import HttpResponse

from apps.counseling.presentation_file_download import build_password_protected_download

print("=== direct korean header test")
r = HttpResponse(b"x")
korean = "08._한기상.zip"
try:
    r["Content-Disposition"] = (
        f'attachment; filename="{korean}"; filename*=UTF-8\'\'{quote(korean)}'
    )
    r.serialize_headers()
    print(" direct OK")
except Exception as exc:
    print(" direct FAIL:", type(exc).__name__, exc)

names = [
    "report.hwp",
    "08. (한기상)전문상담사 사례발표보고서.hwp",
]

for name in names:
    print("===", name)
    payload = build_password_protected_download(
        b"test-content",
        inner_filename=name,
        password="12345678",
    )
    print(" delivery:", payload.delivery, "filename:", repr(payload.filename))
    r = HttpResponse(payload.data, content_type=payload.content_type)
    try:
        r["Content-Disposition"] = (
            f'attachment; filename="{payload.filename}"; '
            f"filename*=UTF-8''{quote(payload.filename)}"
        )
        r.serialize_headers()
        print(" headers OK")
    except Exception as exc:
        print(" HEADER FAIL:", type(exc).__name__, exc)
