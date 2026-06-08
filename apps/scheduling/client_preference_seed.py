"""내담자 상담 가능 시간 시드 (스프레드시트 기준). 전효영·이수정 담당 내담자 제외."""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.scheduling.counselor_availability_seed import AvailabilitySlotSeed


@dataclass
class ClientPreferenceSeed:
    name: str
    email: str
    counselor_name: str
    slots: list[AvailabilitySlotSeed] = field(default_factory=list)
    skip: bool = False
    skip_reason: str = ""


EXCLUDED_COUNSELOR_NAMES = frozenset({"전효영", "이수정"})

EXCLUDED_CLIENT_EMAILS = frozenset(
    {
        "arsui90@naver.com",
        "dudtjd626zx@naver.com",
        "kkaldoong@hanmail.net",
        "jjang_kor@hanmail.net",
    }
)

# 1회기 자동 배정 대상 (스프레드시트 매칭 10명)
SESSION1_AUTO_MATCH_EMAILS = frozenset(
    {
        "hisjoyce77@naver.com",
        "poopsc1018@gmail.com",
        "gusdhrl@empas.com",
        "5671469@naver.com",
        "sea124@naver.com",
        "suhboyoung68@gmail.com",
        "esprit0731@naver.com",
        "estherborana@gmail.com",
        "sanqiong@naver.com",
        "kjsilu@naver.com",
    }
)


def _slot(days: list[int], start: str, end: str) -> AvailabilitySlotSeed:
    return AvailabilitySlotSeed(days=days, start_time=start, end_time=end)


CLIENT_PREFERENCE_SEEDS: list[ClientPreferenceSeed] = [
    ClientPreferenceSeed(
        "성명현",
        "estherborana@gmail.com",
        "심재화",
        [
            _slot([4], "14:00", "16:00"),
            _slot([2, 3], "17:00", "21:00"),
        ],
    ),
    ClientPreferenceSeed(
        "임유정",
        "k5jini@naver.com",
        "이영실",
        [_slot([0], "14:00", "16:00")],
    ),
    ClientPreferenceSeed(
        "성순희",
        "sooni1028@naver.com",
        "이영실",
        [_slot([1, 2, 3], "09:00", "12:00")],
    ),
    ClientPreferenceSeed(
        "조선혜",
        "jshvictory65@naver.com",
        "권은혜",
        [_slot([3, 4], "10:00", "13:00")],
    ),
    ClientPreferenceSeed(
        "정한결",
        "hangyeol3884@naver.com",
        "한진이",
        [_slot([2], "19:00", "21:00")],
    ),
    ClientPreferenceSeed(
        "이예은",
        "dpdms0624@naver.com",
        "성소미",
        [
            _slot([2], "17:00", "21:00"),
            _slot([4], "17:00", "21:00"),
            _slot([5], "13:00", "16:00"),
        ],
    ),
    ClientPreferenceSeed(
        "고혜숙",
        "sea124@naver.com",
        "김상연",
        [
            _slot([0, 1, 2, 3], "17:00", "21:00"),
            _slot([5], "17:00", "21:00"),
        ],
    ),
    ClientPreferenceSeed(
        "김혜정",
        "iris0719@daum.net",
        "한경희",
        [
            _slot([5], "11:00", "13:00"),
            _slot([6], "13:00", "15:00"),
            _slot([0], "19:00", "20:00"),
            _slot([1], "18:00", "20:00"),
            _slot([1], "11:00", "13:00"),
        ],
    ),
    ClientPreferenceSeed(
        "안정민",
        "sindyan1@naver.com",
        "최윤희",
        [_slot([1, 2, 3, 4], "12:00", "13:00")],
    ),
    ClientPreferenceSeed(
        "이지현",
        "zee79@naver.com",
        "한경희",
        [_slot([0, 1, 2, 3, 4], "12:00", "13:00")],
    ),
    ClientPreferenceSeed(
        "박슬아",
        "poopsc1018@gmail.com",
        "김소진",
        [_slot([0, 1, 4], "14:00", "16:00")],
    ),
    ClientPreferenceSeed(
        "정진아",
        "chobits1920@nate.com",
        "천옥희",
        [_slot([0, 1, 2, 3, 4], "18:00", "21:00")],
    ),
    ClientPreferenceSeed(
        "최우정",
        "iceloo@naver.com",
        "윤성희",
        [
            _slot([1], "14:00", "16:00"),
            _slot([0, 2, 3, 4], "09:00", "13:00"),
        ],
    ),
    ClientPreferenceSeed(
        "구현정",
        "kookoo162@daum.net",
        "신영화",
        skip=True,
        skip_reason="캐나다·한국 시차 조율 필요 - 자동 매칭 불가",
    ),
    ClientPreferenceSeed(
        "이경숙",
        "esprit0731@naver.com",
        "김상연",
        [
            _slot([2], "09:00", "12:00"),
            _slot([4], "09:00", "12:00"),
            _slot([4], "14:00", "16:00"),
        ],
    ),
    ClientPreferenceSeed(
        "이세영",
        "nishikiori@naver.com",
        "성소미",
        [_slot([1, 3, 4], "19:00", "21:00")],
    ),
    ClientPreferenceSeed(
        "이명란",
        "starking0700@naver.com",
        "백경미",
        [_slot([0, 1, 2, 4], "16:00", "21:00")],
    ),
    ClientPreferenceSeed(
        "김수미",
        "glory921@hanmail.net",
        "권은혜",
        [
            _slot([2], "10:00", "12:00"),
            _slot([4], "10:00", "12:00"),
            _slot([5], "10:00", "11:00"),
        ],
    ),
    ClientPreferenceSeed(
        "배민정",
        "yhamom@naver.com",
        "정영란",
        [
            _slot([0], "18:00", "21:00"),
            _slot([1], "19:00", "21:00"),
            _slot([4], "19:00", "21:00"),
        ],
    ),
    ClientPreferenceSeed(
        "조영은",
        "2297evelyn@gmail.com",
        "윤성희",
        [
            _slot([0, 1, 2, 3], "08:00", "09:00"),
            _slot([0, 2], "13:00", "14:00"),
        ],
    ),
    ClientPreferenceSeed(
        "박미영",
        "myparkrang@naver.com",
        "양은영",
        [_slot([1], "20:00", "21:00")],
    ),
    ClientPreferenceSeed(
        "서보영",
        "suhboyoung68@gmail.com",
        "김은영",
        [
            _slot([6], "20:00", "22:00"),
            _slot([0], "20:00", "22:00"),
            _slot([2], "20:00", "22:00"),
        ],
    ),
    ClientPreferenceSeed(
        "김지민",
        "gracyroh@hanmail.net",
        "백경미",
        [_slot([2], "20:00", "21:00")],
    ),
    ClientPreferenceSeed(
        "정경화",
        "hisjoyce77@naver.com",
        "한진이",
        [_slot([1, 2, 3, 4], "09:00", "12:00")],
    ),
    ClientPreferenceSeed(
        "홍연서",
        "alberopesca@naver.com",
        "양은영",
        [
            _slot([0, 1, 2, 3, 4], "18:00", "21:00"),
            _slot([5], "13:00", "18:00"),
            _slot([6], "09:00", "21:00"),
        ],
    ),
    ClientPreferenceSeed(
        "이정희",
        "gumboat@naver.com",
        "신영화",
        [_slot([0, 1, 2, 3, 4], "18:00", "21:00")],
    ),
    ClientPreferenceSeed(
        "이현옥",
        "gusdhrl@empas.com",
        "천옥희",
        [
            _slot([0, 1, 2, 3], "09:00", "16:00"),
            _slot([4], "15:00", "17:00"),
            _slot([0], "20:00", "21:00"),
            _slot([3], "18:00", "20:00"),
        ],
    ),
    ClientPreferenceSeed(
        "김영창",
        "va6309@hanmail.net",
        "심재화",
        [_slot(list(range(7)), "17:00", "21:00")],
    ),
    ClientPreferenceSeed(
        "김효순",
        "kjsilu@naver.com",
        "김은영",
        [
            _slot([1, 3], "10:00", "12:00"),
            _slot([5], "15:00", "18:00"),
        ],
    ),
    ClientPreferenceSeed(
        "오유진",
        "iting81@naver.com",
        "정영란",
        [_slot([0, 2, 4], "18:40", "19:40")],
    ),
    ClientPreferenceSeed(
        "김선경",
        "sanqiong@naver.com",
        "최윤희",
        [
            _slot([0, 2], "09:00", "12:00"),
            _slot([1], "08:00", "10:00"),
            _slot([4], "15:00", "17:00"),
        ],
    ),
    ClientPreferenceSeed(
        "황윤진",
        "5671469@naver.com",
        "김소진",
        [
            _slot([0], "08:00", "12:00"),
            _slot([2, 3], "13:00", "17:00"),
            _slot([6], "19:00", "21:00"),
        ],
    ),
]
