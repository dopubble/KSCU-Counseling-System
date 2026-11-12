"""상담 신청·일지 등에서 공통으로 쓰는 상담 유형 목록."""

COUNSELING_TYPE_VALUES = [
    "진로상담",
    "개인성격",
    "대인관계",
    "부부관계",
    "자녀관계",
]

COUNSELING_TYPE_CHOICES = [(value, value) for value in COUNSELING_TYPE_VALUES]

# 신청서에만 쓰는 선호. Case·예약·Zoom은 IN_PERSON / REMOTE 만 사용한다.
APPLICATION_COUNSELING_METHOD_BOTH = "BOTH"
APPLICATION_COUNSELING_METHOD_CHOICES = [
    ("IN_PERSON", "대면"),
    ("REMOTE", "비대면"),
    (APPLICATION_COUNSELING_METHOD_BOTH, "대면·비대면 모두 가능"),
]

WEEKDAY_CHOICES = [
    ("MON", "월요일"),
    ("TUE", "화요일"),
    ("WED", "수요일"),
    ("THU", "목요일"),
    ("FRI", "금요일"),
    ("SAT", "토요일"),
    ("SUN", "일요일"),
]

TIME_SLOT_CHOICES = [
    ("MORNING", "오전"),
    ("AFTERNOON", "오후"),
    ("EVENING", "저녁"),
]

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
    "학생테스트1",
})

LEGACY_COUNSELING_TYPE_MAP = {
    "개인상담": "개인성격",
    "학업상담": "진로상담",
    "가족상담": "자녀관계",
    "심리·정서": "개인성격",
    "기타": "개인성격",
}


def normalize_choice_list(raw, choices) -> list[str]:
    """허용된 코드만 정의된 순서로 남긴다."""
    allowed = [code for code, _label in choices]
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    selected = {str(item).strip() for item in raw if str(item).strip()}
    return [code for code in allowed if code in selected]


def choice_labels(raw, choices) -> str:
    label_map = dict(choices)
    codes = normalize_choice_list(raw, choices)
    return ", ".join(label_map[code] for code in codes)


def weekly_availability_tokens() -> list[tuple[str, str]]:
    """요일:시간대 토큰. 신청 폼 선택지와 서버 검증에 같이 쓴다."""
    return [
        (f"{day}:{slot}", f"{day_label} {slot_label}")
        for day, day_label in WEEKDAY_CHOICES
        for slot, slot_label in TIME_SLOT_CHOICES
    ]


def normalize_weekly_availability(raw) -> dict[str, list[str]]:
    """선택한 요일·시간대 조합만 요일 순서로 남긴다.

    허용되지 않은 토큰이 있으면 ValueError. 선택하지 않은 조합은 넣지 않는다.
    """
    selected: set[tuple[str, str]] = set()
    if raw is None:
        return {}
    if isinstance(raw, dict):
        items = []
        for day, slots in raw.items():
            if isinstance(slots, str):
                slots = [slots]
            if not isinstance(slots, (list, tuple)):
                raise ValueError("invalid availability")
            for slot in slots:
                items.append(f"{day}:{slot}")
        raw = items
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("invalid availability")
    allowed_days = {code for code, _label in WEEKDAY_CHOICES}
    allowed_slots = {code for code, _label in TIME_SLOT_CHOICES}
    for item in raw:
        text = str(item).strip()
        day, sep, slot = text.partition(":")
        if sep != ":" or day not in allowed_days or slot not in allowed_slots:
            raise ValueError("invalid availability")
        selected.add((day, slot))
    result: dict[str, list[str]] = {}
    for day, _label in WEEKDAY_CHOICES:
        slots = [slot for slot, _slot_label in TIME_SLOT_CHOICES if (day, slot) in selected]
        if slots:
            result[day] = slots
    return result


def weekly_availability_lines(raw) -> list[str]:
    """저장된 조합만 표시한다. 없는 조합은 만들지 않는다."""
    if not isinstance(raw, dict):
        return []
    day_labels = dict(WEEKDAY_CHOICES)
    slot_labels = dict(TIME_SLOT_CHOICES)
    lines = []
    for day, _label in WEEKDAY_CHOICES:
        slots = raw.get(day)
        if isinstance(slots, str):
            slots = [slots]
        if not isinstance(slots, (list, tuple)):
            continue
        labels = []
        seen = set()
        for slot in slots:
            code = str(slot).strip()
            if code in slot_labels and code not in seen:
                seen.add(code)
                labels.append(slot_labels[code])
        if labels and day in day_labels:
            lines.append(f"{day_labels[day]}: {', '.join(labels)}")
    return lines


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
