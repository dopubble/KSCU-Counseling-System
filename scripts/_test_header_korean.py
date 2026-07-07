import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kscu_counseling.settings.development")

import django

django.setup()

from django.http import HttpResponse

name = "08. (\ud55c\uae30\uc0c1)test.hwp.zip"
r = HttpResponse(b"x")
try:
    r["Content-Disposition"] = f'attachment; filename="{name}"'
    bytes(r.headers["Content-Disposition"], "latin-1")
    print("latin-1 ok (unexpected)")
except UnicodeEncodeError as e:
    print("UnicodeEncodeError (expected):", e)
