"""상담사 가용시간 시드 데이터 (매주 반복). 전효영·이수정 제외."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AvailabilitySlotSeed:
    days: list[int]  # 0=월 … 6=일
    start_time: str  # HH:MM
    end_time: str


@dataclass
class CounselorAvailabilitySeed:
    name: str
    email: str
    slots: list[AvailabilitySlotSeed] = field(default_factory=list)


# 이미지 표 기준 (가용시간 있는 상담사만)
COUNSELOR_AVAILABILITY_SEEDS: list[CounselorAvailabilitySeed] = [
    CounselorAvailabilitySeed(
        "심재화",
        "sjh226@daum.net",
        [AvailabilitySlotSeed([2, 3, 4], "11:00", "16:00")],
    ),
    CounselorAvailabilitySeed(
        "양은영",
        "ymg000@naver.com",
        [AvailabilitySlotSeed([1, 2], "10:00", "14:00")],
    ),
    CounselorAvailabilitySeed(
        "백경미",
        "100kmee@naver.com",
        [AvailabilitySlotSeed([3], "14:00", "16:00")],
    ),
    CounselorAvailabilitySeed(
        "한경희",
        "hkh0525@hanmail.net",
        [AvailabilitySlotSeed([2, 3], "18:00", "21:00")],
    ),
    CounselorAvailabilitySeed(
        "김소진",
        "ssoji0319@naver.com",
        [AvailabilitySlotSeed([1, 2, 4], "14:00", "18:00")],
    ),
    CounselorAvailabilitySeed(
        "천옥희",
        "coh90254750@gmail.com",
        [
            AvailabilitySlotSeed([0, 2, 3, 4], "09:00", "18:00"),
            AvailabilitySlotSeed([1], "14:00", "18:00"),
        ],
    ),
    CounselorAvailabilitySeed(
        "신영화",
        "movie720@naver.com",
        [AvailabilitySlotSeed([2], "09:00", "15:00")],
    ),
    CounselorAvailabilitySeed(
        "최윤희",
        "0504jesus@naver.com",
        [AvailabilitySlotSeed([1, 4], "14:00", "17:00")],
    ),
    CounselorAvailabilitySeed(
        "이영실",
        "joyfulis@hanmail.net",
        [AvailabilitySlotSeed([0, 3], "18:00", "21:00")],
    ),
    CounselorAvailabilitySeed(
        "권은혜",
        "myeunhye@hanmail.net",
        [AvailabilitySlotSeed([3], "15:00", "19:00")],
    ),
    CounselorAvailabilitySeed(
        "성소미",
        "whitessmi@naver.com",
        [AvailabilitySlotSeed([0, 2, 4], "10:00", "16:00")],
    ),
    CounselorAvailabilitySeed(
        "윤성희",
        "cccysh@naver.com",
        [AvailabilitySlotSeed([3, 4], "14:00", "19:00")],
    ),
    CounselorAvailabilitySeed(
        "김상연",
        "bdsd0727@gmail.com",
        [
            AvailabilitySlotSeed([1, 3, 4], "10:00", "17:00"),
            AvailabilitySlotSeed([2], "15:00", "18:00"),
        ],
    ),
    CounselorAvailabilitySeed(
        "한진이",
        "hajy7728@hanmail.net",
        [AvailabilitySlotSeed([1, 3], "11:00", "15:00")],
    ),
    CounselorAvailabilitySeed(
        "김은영",
        "n92202386@naver.com",
        [
            AvailabilitySlotSeed([1, 2, 3], "19:30", "21:30"),
            AvailabilitySlotSeed([5], "09:00", "18:00"),
        ],
    ),
    CounselorAvailabilitySeed(
        "정영란",
        "jungyl2@hanmail.net",
        [
            AvailabilitySlotSeed([1], "14:00", "18:00"),
            AvailabilitySlotSeed([3], "20:00", "23:00"),
        ],
    ),
]
