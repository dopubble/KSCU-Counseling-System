"""상담 신청·일지 등에서 공통으로 쓰는 상담 유형 목록."""

COUNSELING_TYPE_VALUES = [
    "진로상담",
    "개인성격",
    "대인관계",
    "부부관계",
    "자녀관계",
]

COUNSELING_TYPE_CHOICES = [(value, value) for value in COUNSELING_TYPE_VALUES]

DEFAULT_COUNSELING_TYPES = ["진로상담"]

# 비대면(Zoom) 상담 내담자 — Case.counseling_method 일괄 반영용
REMOTE_CLIENT_NAMES = frozenset({
    "서영진",
    "고혜숙",
    "안정민",
    "이경숙",
    "김수미",
    "이현옥",
    "정경화",
    "구현정",
    "정진아",
    "박미영",
    "홍연서",
    "정한결",
    "김효순",
    "조선혜",
    "조영은",
    "임유정",
    "오유진",
    "조현경",
    "김혜정",
})

LEGACY_COUNSELING_TYPE_MAP = {
    "개인상담": "개인성격",
    "학업상담": "진로상담",
    "가족상담": "자녀관계",
    "심리·정서": "개인성격",
    "기타": "개인성격",
}


def normalize_counseling_types(raw) -> list[str]:
    """유효한 상담 유형만 순서 유지·중복 제거."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    if not isinstance(raw, (list, tuple)):
        return []

    seen: set[str] = set()
    normalized: list[str] = []
    for item in raw:
        value = str(item).strip()
        if not value or value in seen:
            continue
        if value in COUNSELING_TYPE_VALUES:
            seen.add(value)
            normalized.append(value)
    return normalized


def split_spreadsheet_complaint_categories(text: str) -> tuple[list[str], str]:
    """스프레드시트 '상담 호소 문제(*)' → (상담 유형 목록, 나머지 설명)."""
    remaining = (text or "").strip()
    types: list[str] = []
    seen: set[str] = set()
    ordered = sorted(COUNSELING_TYPE_VALUES, key=len, reverse=True)
    while remaining:
        matched = None
        for value in ordered:
            if remaining.startswith(value):
                matched = value
                break
        if not matched:
            break
        if matched not in seen:
            types.append(matched)
            seen.add(matched)
        remaining = remaining[len(matched) :].lstrip()
    return types, remaining.strip()


def parse_spreadsheet_complaint_categories(text: str) -> list[str]:
    """스프레드시트 '상담 호소 문제(*)' → 상담 유형(checkbox) 목록."""
    types, _ = split_spreadsheet_complaint_categories(text)
    return types


def format_spreadsheet_complaint_categories(text: str) -> str:
    """상담사 화면 표시용 — 유형 + 나머지 설명."""
    types, remainder = split_spreadsheet_complaint_categories(text)
    parts = list(types)
    if remainder:
        parts.append(remainder)
    cleaned = (text or "").strip()
    return ", ".join(parts) if parts else cleaned


def migrate_legacy_counseling_type(value: str) -> list[str]:
    """기존 단일 문자열 counseling_type → counseling_types 목록."""
    text = (value or "").strip()
    if not text:
        return []

    if text in COUNSELING_TYPE_VALUES:
        return [text]

    if text in LEGACY_COUNSELING_TYPE_MAP:
        return [LEGACY_COUNSELING_TYPE_MAP[text]]

    if "|" in text:
        category = text.split("|", 1)[0].strip()
        if category in COUNSELING_TYPE_VALUES:
            return [category]

    return normalize_counseling_types(text)
