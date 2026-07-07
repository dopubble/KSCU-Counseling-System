import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kscu_counseling.settings.development")
django.setup()

from django.http import HttpResponse
from apps.counseling.presentation_file_download import (
    build_password_protected_zip,
    encrypted_zip_filename,
)

name = "08. (한기상)전문상담사 사례발표보고서.hwp"
zip_bytes = build_password_protected_zip(b"test", inner_filename=name, password="1234")
r = HttpResponse(zip_bytes, content_type="application/zip")
r["Content-Disposition"] = f'attachment; filename="{encrypted_zip_filename(name)}"'
print("zip ok", len(zip_bytes))
try:
    r.serialize_headers()
    print("headers ok")
except Exception as e:
    print("header error:", type(e), e)
