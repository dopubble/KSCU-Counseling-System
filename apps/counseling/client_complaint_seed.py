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


# 스프레드시트 「상담 호소 문제(*)」 원문 그대로
CLIENT_COMPLAINT_SEEDS: list[ClientComplaintSeed] = [
    ClientComplaintSeed("김아름", "arsui90@naver.com", "개인성격대인관계"),
    ClientComplaintSeed("성명현", "estherborana@gmail.com", "개인성격부부관계"),
    ClientComplaintSeed("임유정", "k5jini@naver.com", "대인관계무기력. 화"),
    ClientComplaintSeed("성순희", "sooni1028@naver.com", "개인성격우울감, 무기력함, 수면장애"),
    ClientComplaintSeed("조선혜", "jshvictory65@naver.com", "개인성격대인관계"),
    ClientComplaintSeed("정한결", "hangyeol3884@naver.com", "진로상담개인성격"),
    ClientComplaintSeed("이예은", "dpdms0624@naver.com", "진로상담대인관계"),
    ClientComplaintSeed("고혜숙", "sea124@naver.com", "개인성격상사와의 관계"),
    ClientComplaintSeed("김혜정", "iris0719@daum.net", "부부관계불안 및 우울"),
    ClientComplaintSeed("안정민", "sindyan1@naver.com", "진로상담개인성격"),
    ClientComplaintSeed("이지현", "zee79@naver.com", "개인성격대인관계"),
    ClientComplaintSeed("박슬아", "poopsc1018@gmail.com", "개인성격대인관계"),
    ClientComplaintSeed("정진아", "chobits1920@nate.com", "개인성격대인관계"),
    ClientComplaintSeed("최우정", "iceloo@naver.com", "대인관계자녀관계"),
    ClientComplaintSeed("강순화", "kkaldoong@hanmail.net", "대인관계부부관계"),
    ClientComplaintSeed("구현정", "kookoo162@daum.net", "개인성격대인관계"),
    ClientComplaintSeed("이경숙", "esprit0731@naver.com", "진로상담개인성격"),
    ClientComplaintSeed("황명자", "dudtjd6262@naver.com", "진로상담부부관계"),
    ClientComplaintSeed("이세영", "nishikiori@naver.com", "개인성격부부관계"),
    ClientComplaintSeed("이명란", "starking0700@naver.com", "진로상담자녀관계"),
    ClientComplaintSeed("김수미", "glory921@hanmail.net", "개인성격대인관계"),
    ClientComplaintSeed("배민정", "yhamom@naver.com", "개인성격대인관계"),
    ClientComplaintSeed("조영은", "2297evelyn@gmail.com", "개인성격부부관계"),
    ClientComplaintSeed("박미영", "myparkrang@naver.com", "개인성격자녀관계"),
    ClientComplaintSeed("서보영", "suhboyoung68@gmail.com", "부부관계친정 어머니 간병"),
    ClientComplaintSeed("장경화", "jiang_kor@hanmail.net", "개인성격부부관계"),
    ClientComplaintSeed("김지민", "gracyroh@hanmail.net", "개인성격부부관계"),
    ClientComplaintSeed("정경화", "hisjoyce77@naver.com", "부부관계리더와의 관계 실망"),
    ClientComplaintSeed("홍연서", "alberopesca@naver.com", "진로상담대인관계"),
    ClientComplaintSeed("이정희", "gumboat@naver.com", "대인관계자녀관계"),
    ClientComplaintSeed("이현옥", "gusdhrl@empas.com", "부부관계자녀관계"),
    ClientComplaintSeed("김영창", "va6309@hanmail.net", "진로상담자녀관계"),
    ClientComplaintSeed("김효순", "kjsilu@naver.com", "부부관계우울증"),
    ClientComplaintSeed("오유진", "iting81@naver.com", "대인관계자녀관계"),
    ClientComplaintSeed("김선경", "sanqiong@naver.com", "개인성격대인관계"),
    ClientComplaintSeed("황윤진", "5671469@naver.com", "개인성격대인관계"),
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
    """상담 신청·시드 기준 주요 호소 문제 (스프레드시트 원문)."""
    email = (client_email or "").strip()
    if not email and application is not None and hasattr(application, "client_id"):
        client = getattr(application, "client", None)
        if client is not None:
            email = client.email or ""
    seed = find_complaint_seed_for_email(email)
    if seed:
        return clean_reason(seed.reason)
    if application is None:
        return "—"
    return clean_reason(application.reason or "") or "—"
