"""상담 신청·일지 등에서 공통으로 쓰는 상담 유형 목록."""

# (대분류, [세부 주제]) — 동일 세부 주제는 대분류별로 한 번씩만 등록
COUNSELING_TYPE_GROUPS: list[tuple[str, list[str]]] = [
    (
        "개인성격",
        [
            "대인관계",
            "부부관계",
            "우울감, 무기력함, 수행장애",
            "상사와의 관계",
            "자녀관계",
        ],
    ),
    (
        "대인관계",
        [
            "무기력. 화",
            "부부관계",
            "자녀관계",
        ],
    ),
    (
        "진로상담",
        [
            "개인성격",
            "대인관계",
            "부부관계",
            "자녀관계",
        ],
    ),
    (
        "부부관계",
        [
            "불안 및 우울",
            "친정 어머니 간병",
            "리더와의 관계 실망",
            "자녀관계",
            "우울증",
        ],
    ),
]


def counseling_type_value(category: str, topic: str) -> str:
    return f"{category}|{topic}"


def counseling_type_label(category: str, topic: str) -> str:
    return f"{category} · {topic}"


def build_counseling_type_values() -> list[str]:
    return [
        counseling_type_value(category, topic)
        for category, topics in COUNSELING_TYPE_GROUPS
        for topic in topics
    ]


COUNSELING_TYPE_VALUES = build_counseling_type_values()

# optgroup: 대분류 아래 세부 주제만 표시 (겹쳐 보이는 긴 문자열 제거)
COUNSELING_TYPE_CHOICES: list = [("", "선택해 주세요")]
for _category, _topics in COUNSELING_TYPE_GROUPS:
    COUNSELING_TYPE_CHOICES.append(
        (
            _category,
            [
                (counseling_type_value(_category, topic), topic)
                for topic in _topics
            ],
        )
    )

# 일지 등 flat 선택용 (placeholder + 전체 항목, 라벨은 구분자 포함)
COUNSELING_TYPE_FLAT_CHOICES = [("", "선택해 주세요")] + [
    (value, counseling_type_label(*value.split("|", 1)))
    for value in COUNSELING_TYPE_VALUES
]

DEFAULT_COUNSELING_TYPE = COUNSELING_TYPE_VALUES[0]
