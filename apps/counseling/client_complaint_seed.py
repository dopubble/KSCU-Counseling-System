"""내담자별 주요 호소 문제 시드 (스프레드시트 원문)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClientComplaintSeed:
    name: str
    email: str
    reason: str


def clean_reason(text: str) -> str:
    """앞뒤 공백·연속 공백만 정리 (원문 길이 유지)."""
    return " ".join((text or "").split())


# 스프레드시트 원문 그대로
CLIENT_COMPLAINT_SEEDS: list[ClientComplaintSeed] = [
    ClientComplaintSeed(
        "김아름",
        "arsui90@naver.com",
        "인간을 대할때 말투 좋은 말 할때 안듣는것 고집이 너무 심하고 우기기가 심해요",
    ),
    ClientComplaintSeed(
        "성명현",
        "estherborana@gmail.com",
        "나의 성격 특질에 대해 알고 싶음 / 그리고 부부 의사소통이 안되는 문제에 대해 비록 남편은 변하기를 싫어할지라도 나만이라도 변화하고 싶음",
    ),
    ClientComplaintSeed(
        "임유정",
        "k5jini@naver.com",
        "무기력... 모든것이 멈추었음 좋겠다는 생각이 많습니다.",
    ),
    ClientComplaintSeed(
        "성순희",
        "sooni1028@naver.com",
        "갱년기, 우울감, 무기력함",
    ),
    ClientComplaintSeed(
        "조선혜",
        "jshvictory65@naver.com",
        "교회의 부교역자 사모로서 적응하는 대인관계와 친정부모님과 시부모님과의 관계, 남편과의 관계에 대해 상담받고 싶습니다(현재 시험관으로 둘째 계획중에 있습니다.)",
    ),
    ClientComplaintSeed(
        "정한결",
        "hangyeol3884@naver.com",
        "하고싶은것을 못찾겠고 무기력함을 느낍니다.",
    ),
    ClientComplaintSeed(
        "이예은",
        "dpdms0624@naver.com",
        "학창시절 대인관계 문제로 고등학교 검정고시를 보고 일반대를 갔지만 어려워서 숭실사대로 재입학.사회생활 에서의 대인관계 문제 다루는 방법 알고싶음.미래 진로 방향 걱정되기도 함.",
    ),
    ClientComplaintSeed(
        "고혜숙",
        "sea124@naver.com",
        "완벽하기를 바라는 상사와 완벽하지 못한 나 사이에서의 갈등과 자존감 하락",
    ),
    ClientComplaintSeed(
        "김혜정",
        "iris0719@daum.net",
        "가족 내 문제와 진로 고민 등으로 불안과 우울감을 느낄 때가 있음. 어떻게 해야 내 안의 부정적인 감정들을 잘 다루고 해소할 수 있을지 도움을 받고 싶음.",
    ),
    ClientComplaintSeed(
        "안정민",
        "sindyan1@naver.com",
        "주변 정리가 잘 안됩니다.",
    ),
    ClientComplaintSeed(
        "이지현",
        "zee79@naver.com",
        "성향에 따른 리더십 부족에 따른 불편함, 조직에 대한 반감 등에 따른 관계 접촉시 불편함.",
    ),
    ClientComplaintSeed(
        "박슬아",
        "poopsc1018@gmail.com",
        "대인관계와 우울증 무기력함",
    ),
    ClientComplaintSeed(
        "정진아",
        "chobits1920@nate.com",
        "우울증으로 인한 사회적 어려움인지, 사회적 어려움으로 우울증인 것인지, 정확히 파악하고 싶음(사회적 인간관계가 매번 어려움). 무기력함이 있음.",
    ),
    ClientComplaintSeed(
        "최우정",
        "iceloo@naver.com",
        "사람을 만날 때 좀 더 편하게 되었으면 좋겠다. 아이에 대한 조급함이 해결되었으면 좋겠다. 엄마를 존중하며 대하고싶다.",
    ),
    ClientComplaintSeed(
        "강순화",
        "kkaldoong@hanmail.net",
        "부부관계",
    ),
    ClientComplaintSeed(
        "구현정",
        "kookoo162@daum.net",
        "거절이 어렵고 모두를 만족시키려는 성향이 강합니다. 다른 사람들이 모두 행복해하는 모습에 행복을 느낍니다. 하지만 이 또한 자기만족일지도 모르겠습니다.",
    ),
    ClientComplaintSeed(
        "이경숙",
        "esprit0731@naver.com",
        "사람을 좋아하는 것 같은 성격이라고 스스로는 생각하는데, 기대와는 다른 대인관계 틀을 가진 것 같습니다. 나이가 들어 갈수록 무엇이 문제인지 고민이 많이 되고 궁금합니다.",
    ),
    ClientComplaintSeed(
        "황명자",
        "dudtjd6262@naver.com",
        "무기력함 우울감이 많은것 같습니다",
    ),
    ClientComplaintSeed(
        "이세영",
        "nishikiori@naver.com",
        "남편에 대한 계속 되는 의심, 망상, 불안감",
    ),
    ClientComplaintSeed(
        "이명란",
        "starking0700@naver.com",
        "자녀와 밀착형 관계 / 졸업과 퇴사후 진로문제",
    ),
    ClientComplaintSeed(
        "김수미",
        "glory921@hanmail.net",
        "대인관계에서 오는 피로감, 무기력감, 우울증",
    ),
    ClientComplaintSeed(
        "배민정",
        "yhamom@naver.com",
        "엄마, 아빠가 싫지 않은데 엄마와의 관계가 편하지 않아요.",
    ),
    ClientComplaintSeed(
        "조영은",
        "2297evelyn@gmail.com",
        "혼자있는것이 좋고 사람만나는게 연락하는게 너무 피곤해요",
    ),
    ClientComplaintSeed(
        "박미영",
        "myparkrang@naver.com",
        "자녀와의 관계에서 어렵고 무기력합니다.",
    ),
    ClientComplaintSeed(
        "서보영",
        "suhboyoung68@gmail.com",
        "남편과의 갈등",
    ),
    ClientComplaintSeed(
        "장경화",
        "jiang_kor@hanmail.net",
        "내 자신에 성향 파악 / 부부관계에 도움이 되는 방안",
    ),
    ClientComplaintSeed(
        "김지민",
        "gracyroh@hanmail.net",
        "전 항상 불안함을 느끼고 살아갑니다.막상 생각해보면 불안한일이 없는데 너무나도 불안하고 무기력하고 깊은 우울감이 자주 찾아 옵니다.",
    ),
    ClientComplaintSeed(
        "정경화",
        "hisjoyce77@naver.com",
        "무기력함과 무기력함",
    ),
    ClientComplaintSeed(
        "홍연서",
        "alberopesca@naver.com",
        "3년동안 반복된 퇴사, 입사, 회사 문제 등으로 큰 스트레스를 받고 있으며, 새 직장에 취업시 두려움을 갖고 있습니다.",
    ),
    ClientComplaintSeed(
        "이정희",
        "gumboat@naver.com",
        "믿음의 삶과 합리적 삶에서의 갈등",
    ),
    ClientComplaintSeed(
        "이현옥",
        "gusdhrl@empas.com",
        "대화단절 / 소통불가 /",
    ),
    ClientComplaintSeed(
        "김영창",
        "va6309@hanmail.net",
        "진로 노후에 대한 불안",
    ),
    ClientComplaintSeed(
        "김효순",
        "kjsilu@naver.com",
        "무기력 불안 우울증 자살충동",
    ),
    ClientComplaintSeed(
        "오유진",
        "iting81@naver.com",
        "자녀와의 관계에서, 자꾸 과거의 제모습이 저를 붙잡습니다.",
    ),
    ClientComplaintSeed(
        "김선경",
        "sanqiong@naver.com",
        "자신에 대한 열등감 수치심을 다루고 바른 소통을 할 수 있는 방법을 알고 싶습니다",
    ),
    ClientComplaintSeed(
        "황윤진",
        "5671469@naver.com",
        "아버지의 여러번의 이혼과 재혼으로 인해 어린 시절 정서적인 면이 많이 깨져 사람을 신뢰하지 못하여 그게 연애와 대인관계까지 영향을 미쳐 주변사람이 많음에도 늘 외로운 마음이 듭니다",
    ),
]

# 이메일 변형(등록 오타) 대응
EMAIL_ALIASES: dict[str, str] = {
    "dudtjd626zx@naver.com": "dudtjd6262@naver.com",
    "jjang_kor@hanmail.net": "jiang_kor@hanmail.net",
}


def find_complaint_seed_for_email(email: str) -> ClientComplaintSeed | None:
    """이메일(별칭 포함)으로 시드 조회."""
    lowered = email.strip().lower()
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


DEFAULT_REASON_MARKERS = ("관리자 일괄 접수", "내담자 사전 등록")

# 축약본이 DB에 들어간 경우 시드 원문으로 덮어쓰기 판별용
LEGACY_TRUNCATED_REASONS: frozenset[str] = frozenset(
    {
        "나의 성격 특질에 대해 알고 싶음 / 부부 의사소통 문제 — 나만이라도 변화하고 싶음",
        "부교역자 사모로서 대인·가족·부부 관계 상담 희망(시험관 둘째 계획 중)",
        "대인관계·사회생활 어려움, 대처 방법과 미래 진로 방향에 대한 고민",
        "가족·진로 고민으로 불안·우울, 부정적 감정을 다루고 해소하는 방법을 배우고 싶음",
        "성향에 따른 리더십 부족, 조직 반감 등 관계 접촉 시 불편함",
        "우울·사회적 어려움의 원인 파악, 대인관계 어려움과 무기력함",
        "사람 만날 때 편해지고 싶음, 아이에 대한 조급함, 엄마를 존중하며 대하고 싶음",
        "거절이 어렵고 모두를 만족시키려는 성향, 타인의 행복에서 만족을 느낌",
        "대인관계 틀에 대한 고민, 나이 들수록 무엇이 문제인지 궁금함",
        "항상 불안·무기력·깊은 우울감이 자주 찾아옴(특별한 불안한 일이 없어도)",
        "3년간 반복된 퇴사·입사·회사 문제 스트레스, 새 직장 취업 시 두려움",
        "대화단절 / 소통불가",
        "열등감·수치심 다루기, 바른 소통 방법을 알고 싶음",
        "아버지의 이혼·재혼으로 정서적 상처, 신뢰 어려움·대인·연애 영향, 외로움",
    }
)


def presenting_reason_for_application(application, *, client_email: str = "") -> str:
    """상담 신청·시드 기준 주요 호소 문제 (원문)."""
    reason = clean_reason(application.reason or "")
    email = (client_email or "").strip()
    if not email and hasattr(application, "client_id"):
        client = getattr(application, "client", None)
        if client is not None:
            email = client.email or ""
    seed = find_complaint_seed_for_email(email)
    if seed:
        seed_text = clean_reason(seed.reason)
        if not reason:
            return seed_text
        if any(marker in reason for marker in DEFAULT_REASON_MARKERS):
            return seed_text
        if reason in LEGACY_TRUNCATED_REASONS:
            return seed_text
    return reason or "—"
