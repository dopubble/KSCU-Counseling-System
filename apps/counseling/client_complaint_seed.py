"""내담자별 상담 호소 문제 시드 (스프레드시트 원문).

- complaint_categories: 「상담 호소 문제(*)」 → 상담 유형(개인성격·부부관계 등)
- written_reason: 「주요 호소 문제 작성」 원문 → reason 필드
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.counseling.constants import (
    format_spreadsheet_complaint_categories,
    parse_spreadsheet_complaint_categories,
)


@dataclass(frozen=True)
class ClientComplaintSeed:
    name: str
    email: str
    complaint_categories: str
    written_reason: str


def clean_reason(text: str) -> str:
    """앞뒤 공백·연속 공백만 정리 (원문 길이 유지)."""
    return " ".join((text or "").split())


def counseling_types_for_seed(seed: ClientComplaintSeed) -> list[str]:
    return parse_spreadsheet_complaint_categories(seed.complaint_categories)


def _s(
    name: str,
    email: str,
    complaint_categories: str,
    written_reason: str,
) -> ClientComplaintSeed:
    return ClientComplaintSeed(name, email, complaint_categories, written_reason)


# 스프레드시트 원문 — (상담 호소 문제(*), 주요 호소 문제 작성)
CLIENT_COMPLAINT_SEEDS: list[ClientComplaintSeed] = [
    _s("김아름", "arsui90@naver.com", "개인성격대인관계", "인간을 대할때 말투 좋은 말 할때 안듣는것 고집이 너무 심하고 우기기가 심해요"),
    _s("성명현", "estherborana@gmail.com", "개인성격부부관계", "나의 성격 특질에 대해 알고 싶음 / 그리고 부부 의사소통이 안되는 문제에 대해 비록 남편은 변하기를 싫어할지라도 나만이라도 변화하고 싶음"),
    _s("임유정", "k5jini@naver.com", "대인관계무기력. 화", "무기력... 모든것이 멈추었음 좋겠다는 생각이 많습니다."),
    _s("성순희", "sooni1028@naver.com", "개인성격우울감, 무기력함, 수면장애", "갱년기, 우울감, 무기력함"),
    _s("조선혜", "jshvictory65@naver.com", "개인성격대인관계", "교회의 부교역자 사모로서 적응하는 대인관계와 친정부모님과 시부모님과의 관계, 남편과의 관계에 대해 상담받고 싶습니다(현재 시험관으로 둘째 계획중에 있습니다.)"),
    _s("정한결", "hangyeol3884@naver.com", "진로상담개인성격", "하고싶은것을 못찾겠고 무기력함을 느낍니다."),
    _s("이예은", "dpdms0624@naver.com", "진로상담대인관계", "학창시절 대인관계 문제로 고등학교 검정고시를 보고 일반대를 갔지만 어려워서 숭실사대로 재입학.사회생활 에서의 대인관계 문제 다루는 방법 알고싶음.미래 진로 방향 걱정되기도 함."),
    _s("고혜숙", "sea124@naver.com", "개인성격상사와의 관계", "완벽하기를 바라는 상사와 완벽하지 못한 나 사이에서의 갈등과 자존감 하락"),
    _s("김혜정", "iris0719@daum.net", "부부관계불안 및 우울", "가족 내 문제와 진로 고민 등으로 불안과 우울감을 느낄 때가 있음. 어떻게 해야 내 안의 부정적인 감정들을 잘 다루고 해소할 수 있을지 도움을 받고 싶음."),
    _s("안정민", "sindyan1@naver.com", "진로상담개인성격", "주변 정리가 잘 안됩니다."),
    _s("이지현", "zee79@naver.com", "개인성격대인관계", "성향에 따른 리더십 부족에 따른 불편함, 조직에 대한 반감 등에 따른 관계 접촉시 불편함."),
    _s("박슬아", "poopsc1018@gmail.com", "개인성격대인관계", "대인관계와 우울증 무기력함"),
    _s("정진아", "chobits1920@nate.com", "개인성격대인관계", "우울증으로 인한 사회적 어려움인지, 사회적 어려움으로 우울증인 것인지, 정확히 파악하고 싶음(사회적 인간관계가 매번 어려움). 무기력함이 있음."),
    _s("최우정", "iceloo@naver.com", "대인관계자녀관계", "사람을 만날 때 좀 더 편하게 되었으면 좋겠다. 아이에 대한 조급함이 해결되었으면 좋겠다. 엄마를 존중하며 대하고싶다."),
    _s("강순화", "kkaldoong@hanmail.net", "대인관계부부관계", "부부관계"),
    _s("구현정", "kookoo162@daum.net", "개인성격대인관계", "거절이 어렵고 모두를 만족시키려는 성향이 강합니다. 다른 사람들이 모두 행복해하는 모습에 행복을 느낍니다. 하지만 이 또한 자기만족일지도 모르겠습니다."),
    _s("이경숙", "esprit0731@naver.com", "진로상담개인성격", "사람을 좋아하는 것 같은 성격이라고 스스로는 생각하는데, 기대와는 다른 대인관계 틀을 가진 것 같습니다. 나이가 들어 갈수록 무엇이 문제인지 고민이 많이 되고 궁금합니다."),
    _s("황명자", "dudtjd6262@naver.com", "진로상담부부관계", "무기력함 우울감이 많은것 같습니다"),
    _s("이세영", "nishikiori@naver.com", "개인성격부부관계", "남편에 대한 계속 되는 의심, 망상, 불안감"),
    _s("이명란", "starking0700@naver.com", "진로상담자녀관계", "자녀와 밀착형 관계 / 졸업과 퇴사후 진로문제"),
    _s("김수미", "glory921@hanmail.net", "개인성격대인관계", "대인관계에서 오는 피로감, 무기력감, 우울증"),
    _s("배민정", "yhamom@naver.com", "개인성격대인관계", "엄마, 아빠가 싫지 않은데 엄마와의 관계가 편하지 않아요."),
    _s("조영은", "2297evelyn@gmail.com", "개인성격부부관계", "혼자있는것이 좋고 사람만나는게 연락하는게 너무 피곤해요"),
    _s("박미영", "myparkrang@naver.com", "개인성격자녀관계", "자녀와의 관계에서 어렵고 무기력합니다."),
    _s("서보영", "suhboyoung68@gmail.com", "부부관계친정 어머니 간병", "남편과의 갈등"),
    _s("장경화", "jiang_kor@hanmail.net", "개인성격부부관계", "내 자신에 성향 파악 / 부부관계에 도움이 되는 방안"),
    _s("김지민", "gracyroh@hanmail.net", "개인성격부부관계", "전 항상 불안함을 느끼고 살아갑니다.막상 생각해보면 불안한일이 없는데 너무나도 불안하고 무기력하고 깊은 우울감이 자주 찾아 옵니다."),
    _s("정경화", "hisjoyce77@naver.com", "부부관계리더와의 관계 실망", "무기력함과 무기력함"),
    _s("홍연서", "alberopesca@naver.com", "진로상담대인관계", "3년동안 반복된 퇴사, 입사, 회사 문제 등으로 큰 스트레스를 받고 있으며, 새 직장에 취업시 두려움을 갖고 있습니다."),
    _s("이정희", "gumboat@naver.com", "대인관계자녀관계", "믿음의 삶과 합리적 삶에서의 갈등"),
    _s("이현옥", "gusdhrl@empas.com", "부부관계자녀관계", "대화단절 / 소통불가 /"),
    _s("김영창", "va6309@hanmail.net", "진로상담자녀관계", "진로 노후에 대한 불안"),
    _s("김효순", "kjsilu@naver.com", "부부관계우울증", "무기력 불안 우울증 자살충동"),
    _s("오유진", "iting81@naver.com", "대인관계자녀관계", "자녀와의 관계에서, 자꾸 과거의 제모습이 저를 붙잡습니다."),
    _s("김선경", "sanqiong@naver.com", "개인성격대인관계", "자신에 대한 열등감 수치심을 다루고 바른 소통을 할 수 있는 방법을 알고 싶습니다"),
    _s("황윤진", "5671469@naver.com", "개인성격대인관계", "아버지의 여러번의 이혼과 재혼으로 인해 어린 시절 정서적인 면이 많이 깨져 사람을 신뢰하지 못하여 그게 연애와 대인관계까지 영향을 미쳐 주변사람이 많음에도 늘 외로운 마음이 듭니다"),
]

# 이메일 변형(등록 오타) 대응
EMAIL_ALIASES: dict[str, str] = {
    "dudtjd626zx@naver.com": "dudtjd6262@naver.com",
    "jjang_kor@hanmail.net": "jiang_kor@hanmail.net",
}

# reason 필드에 잘못 넣었던 카테고리 문자열
MISPLACED_CATEGORY_REASONS: frozenset[str] = frozenset(
    clean_reason(seed.complaint_categories) for seed in CLIENT_COMPLAINT_SEEDS
)


def find_complaint_seed_for_email(email: str) -> ClientComplaintSeed | None:
    """이메일(별칭 포함)으로 시드 조회."""
    lowered = email.strip().lower()
    if not lowered:
        return None
    candidates = {lowered}
    if lowered in EMAIL_ALIASES:
        candidates.add(EMAIL_ALIASES[lowered].lower())
    for alias, canonical in EMAIL_ALIASES.items():
        if canonical.lower() == lowered:
            candidates.add(alias.lower())
    for seed in CLIENT_COMPLAINT_SEEDS:
        if seed.email.lower() in candidates:
            return seed
    return None


def find_complaint_seed_for_client(*, email: str = "", name: str = "") -> ClientComplaintSeed | None:
    """이메일·이름으로 시드 조회 (운영 DB 이메일 불일치 대비)."""
    seed = find_complaint_seed_for_email(email)
    if seed:
        return seed
    normalized_name = (name or "").strip()
    if not normalized_name:
        return None
    for item in CLIENT_COMPLAINT_SEEDS:
        if item.name == normalized_name:
            return item
    return None


def find_complaint_seed_for_case(case) -> ClientComplaintSeed | None:
    """사례의 내담자 정보로 시드 조회."""
    client = getattr(case, "client", None)
    if client is None:
        return None
    seed = find_complaint_seed_for_client(
        email=getattr(client, "email", "") or "",
        name=getattr(client, "name", "") or "",
    )
    if seed:
        return seed
    application = getattr(case, "application", None)
    schedule = (application.preferred_schedule or {}) if application else {}
    alt_email = schedule.get("email") or schedule.get("client_email") or ""
    return find_complaint_seed_for_email(str(alt_email))


DEFAULT_REASON_MARKERS = ("관리자 일괄 접수", "내담자 사전 등록")


def presenting_complaint_categories_for_case(case) -> str:
    """사례 기준 상담 호소 문제(유형) — 스프레드시트 「상담 호소 문제(*)」."""
    seed = find_complaint_seed_for_case(case)
    if seed:
        return format_spreadsheet_complaint_categories(seed.complaint_categories)
    application = getattr(case, "application", None)
    if application is None:
        return "—"
    types = application.counseling_types or []
    if types:
        return ", ".join(types)
    return "—"


def presenting_written_reason_for_case(case) -> str:
    """사례 기준 주요 호소 문제 작성(원문) — reason 필드."""
    seed = find_complaint_seed_for_case(case)
    application = getattr(case, "application", None)
    if seed:
        return clean_reason(seed.written_reason)
    if application is None:
        return "—"
    reason = clean_reason(application.reason or "")
    if reason in MISPLACED_CATEGORY_REASONS:
        return "—"
    return reason or "—"


def presenting_reason_for_case(case) -> str:
    """하위 호환 — 주요 호소 문제 작성(원문)."""
    return presenting_written_reason_for_case(case)


def presenting_reason_for_application(application, *, client_email: str = "", client_name: str = "") -> str:
    """상담 신청 기준 주요 호소 문제 작성(원문)."""
    seed = find_complaint_seed_for_client(email=client_email, name=client_name)
    if seed:
        return clean_reason(seed.written_reason)
    if application is None:
        return "—"
    if not client_email and hasattr(application, "client_id"):
        client = getattr(application, "client", None)
        if client is not None:
            seed = find_complaint_seed_for_client(
                email=client.email or "",
                name=client.name or "",
            )
            if seed:
                return clean_reason(seed.written_reason)
    reason = clean_reason(application.reason or "")
    if reason in MISPLACED_CATEGORY_REASONS:
        return "—"
    return reason or "—"
