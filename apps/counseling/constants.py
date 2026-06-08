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
