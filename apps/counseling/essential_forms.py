"""상담사 대시보드에서 내려받는 빈 상담 서식.

파일은 코드와 함께 배포되는 data/forms에 둔다.
작성된 상담서류나 개인정보는 저장하지 않는다.
"""

from pathlib import Path

from django.conf import settings
from django.http import Http404

ESSENTIAL_FORM_DIR = Path(settings.BASE_DIR) / "data" / "forms"

ESSENTIAL_FORMS = (
    {
        "key": "consent",
        "label": "상담동의서",
        "filename": "01. 상담동의서(상담자+내담자)_최종본.hwp",
        "download_name": "상담동의서.hwp",
    },
    {
        "key": "privacy",
        "label": "개인정보 수집·이용 동의서",
        "filename": "02. 개인정보수집이용동의_v2.0.hwp",
        "download_name": "개인정보 수집·이용 동의서.hwp",
    },
    {
        "key": "intake",
        "label": "접수면접지",
        "filename": "07. 접수면접지.hwp",
        "download_name": "접수면접지.hwp",
    },
)

_FORMS_BY_KEY = {item["key"]: item for item in ESSENTIAL_FORMS}


def get_essential_form(form_key: str) -> dict:
    meta = _FORMS_BY_KEY.get(form_key)
    if meta is None:
        raise Http404("서식을 찾을 수 없습니다.")
    path = ESSENTIAL_FORM_DIR / meta["filename"]
    if not path.is_file():
        raise Http404("서식 파일을 찾을 수 없습니다.")
    return {**meta, "path": path}
