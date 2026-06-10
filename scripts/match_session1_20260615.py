"""1회기 상담 매칭 (2026-06-15 시작) — 시드 기반 오프라인 계산."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import product

DURATION = 50
START_DATE = date(2026, 6, 15)
WEEKS = 8
MIN_SAME_DAY_GAP = 120  # minutes


@dataclass
class Slot:
    days: list[int]
    start: str
    end: str


@dataclass
class Person:
    name: str
    email: str = ""
    slots: list[Slot] = field(default_factory=list)
    skip: bool = False
    skip_reason: str = ""
    blocked_ranges: list[tuple[date, date]] = field(default_factory=list)
    date_slots: dict[date, list[Slot]] | None = None
    overseas: bool = False  # 해외 거주


OVERSEAS_COUNSELORS = frozenset({"이영실", "신영화"})
OVERSEAS_CLIENTS = frozenset({"구현정", "이정희"})


def parse_t(s: str) -> time:
    return datetime.strptime(s, "%H:%M").time()


def intersect(a_start: time, a_end: time, b_start: time, b_end: time) -> tuple[time, time] | None:
    s, e = max(a_start, b_start), min(a_end, b_end)
    return (s, e) if s < e else None


def weekly_overlap_minutes(client: Person, counselor: Person) -> int:
    total = 0
    for cs in client.slots:
        for ks in counselor.slots:
            for d in cs.days:
                if d not in ks.days:
                    continue
                ov = intersect(parse_t(cs.start), parse_t(cs.end), parse_t(ks.start), parse_t(ks.end))
                if not ov:
                    continue
                s, e = ov
                total += (datetime.combine(date.min, e) - datetime.combine(date.min, s)).seconds // 60
    return total


def counselor_slots_on(counselor: Person, d: date) -> list[Slot]:
    if counselor.date_slots and d in counselor.date_slots:
        return counselor.date_slots[d]
    for blk_start, blk_end in counselor.blocked_ranges:
        if blk_start <= d <= blk_end:
            return []
    weekday = d.weekday()
    return [s for s in counselor.slots if weekday in s.days]


def iter_starts(client: Person, counselor: Person, d: date) -> list[datetime]:
    out: list[datetime] = []
    c_rules = counselor_slots_on(counselor, d)
    if not c_rules:
        return out
    weekday = d.weekday()
    for cs in client.slots:
        if weekday not in cs.days:
            continue
        cs_s, cs_e = parse_t(cs.start), parse_t(cs.end)
        for ks in c_rules:
            ov = intersect(cs_s, cs_e, parse_t(ks.start), parse_t(ks.end))
            if not ov:
                continue
            win_s, win_e = ov
            cur = datetime.combine(d, win_s)
            end = datetime.combine(d, win_e)
            while cur + timedelta(minutes=DURATION) <= end:
                out.append(cur)
                cur += timedelta(minutes=10)
    return sorted(out)


@dataclass
class Blocked:
    start: datetime
    end: datetime


def is_blocked(at: datetime, blocked: list[Blocked]) -> bool:
    cand_s, cand_e = at, at + timedelta(minutes=DURATION)
    for b in blocked:
        if cand_s < b.end and cand_e > b.start:
            return True
        if cand_s.date() == b.start.date():
            gap = abs((cand_s - b.start).total_seconds()) / 60
            if gap < MIN_SAME_DAY_GAP:
                return True
    return False


def find_slot(client: Person, counselor: Person, blocked: list[Blocked]) -> datetime | None:
    end = START_DATE + timedelta(weeks=WEEKS)
    d = START_DATE
    while d <= end:
        for at in iter_starts(client, counselor, d):
            if not is_blocked(at, blocked):
                return at
        d += timedelta(days=1)
    return None


# --- 상담사 가용 (첨부 표 F열 기준, 연령·경력 미반영) ---
COUNSELORS: list[Person] = [
    Person("심재화", slots=[Slot([2, 3, 4], "11:00", "16:00")]),
    Person("양은영", slots=[Slot([1, 2], "10:00", "14:00")]),
    Person("백경미", slots=[Slot([3], "14:00", "16:00")]),
    Person("한경희", slots=[Slot([2, 3], "18:00", "21:00")]),
    Person(
        "김소진",
        slots=[Slot([1, 2, 4], "14:00", "18:00")],
        blocked_ranges=[(date(2026, 6, 21), date(2026, 6, 28))],
    ),
    Person(
        "천옥희",
        slots=[
            Slot([0, 2, 3, 4], "09:00", "18:00"),
            Slot([1], "14:00", "18:00"),
        ],
    ),
    Person(
        "신영화",
        slots=[
            Slot([2], "10:00", "17:00"),
            Slot([3], "21:00", "23:00"),
            Slot([4], "10:00", "17:00"),
        ],
        overseas=True,
    ),
    Person("최윤희", slots=[Slot([1, 4], "14:00", "17:00")]),
    Person(
        "이영실",
        slots=[Slot([0, 3], "17:00", "21:00")],
        overseas=True,
    ),
    Person("권은혜", slots=[Slot([0, 3], "15:00", "19:00")]),
    Person("성소미", slots=[Slot([0, 2, 4], "10:00", "16:00")]),
    Person("윤성희", slots=[Slot([3, 4], "14:00", "19:00")]),
    Person("전효영", slots=[Slot([0, 1, 2, 3, 4], "09:00", "18:00")]),
    Person("이수정", slots=[Slot([1, 2, 3, 4], "20:00", "23:00")]),
    Person("김상연", slots=[Slot([1, 3, 4, 5], "10:00", "17:00")]),
    Person("한진이", slots=[Slot([1, 3], "11:00", "15:00")]),
    Person(
        "김은영",
        slots=[
            Slot([1, 2, 3], "19:00", "20:00"),
            Slot([5], "09:00", "18:00"),
        ],
    ),
    Person(
        "정영란",
        slots=[
            Slot([1], "14:00", "18:00"),
            Slot([3], "20:00", "23:00"),
        ],
    ),
]

# 이영실(해외): 6/25 이전 월·목 17시 이후 / 이후 월·목 18-21 비대면
def patch_leeyoungsil():
    c = next(x for x in COUNSELORS if x.name == "이영실")
    c.slots = [Slot([0, 3], "17:00", "21:00")]
    c.date_slots = {}
    d = START_DATE
    end = START_DATE + timedelta(weeks=WEEKS)
    while d <= end:
        if d >= date(2026, 6, 25):
            wd = d.weekday()
            if wd in (0, 3):
                c.date_slots[d] = [Slot([wd], "18:00", "21:00")]
            else:
                c.date_slots[d] = []
        d += timedelta(days=1)


patch_leeyoungsil()

# --- 내담자 희망 (첨부 표: 상단 22명 + 하단 15명, 성순희 중복 1회) ---
CLIENTS: list[Person] = [
    Person("임유정", "k5jini@naver.com", [Slot([0], "14:00", "16:00")]),
    Person("성순희", "sooni1028@naver.com", [Slot([1, 2, 3], "09:00", "12:00")]),
    Person("조선혜", "jshvictory65@naver.com", [Slot([3, 4], "10:00", "13:00")]),
    Person("정한결", "hangyeol3884@naver.com", [Slot([2], "19:00", "21:00")]),
    Person(
        "이예은",
        "dpdms0624@naver.com",
        [
            Slot([2], "17:00", "21:00"),
            Slot([4], "17:00", "21:00"),
            Slot([5], "13:00", "17:00"),
        ],
    ),
    Person(
        "김혜정",
        "iris0719@daum.net",
        [
            Slot([5], "11:00", "13:00"),
            Slot([6], "13:00", "15:00"),
            Slot([0], "19:00", "20:00"),
            Slot([1], "11:00", "13:00"),
            Slot([1], "18:00", "20:00"),
        ],
    ),
    Person("안정민", "sindyan1@naver.com", [Slot([1, 2, 3, 4], "12:00", "13:00")]),
    Person("이지현", "zee79@naver.com", [Slot([0, 1, 2, 3, 4], "12:00", "13:00")]),
    Person("정진아", "chobits1920@nate.com", [Slot([0, 1, 2, 3, 4], "18:00", "21:00")]),
    Person(
        "최우정",
        "iceloo@naver.com",
        [
            # 원문: "화요일은14시 / 월수목금 13시까지 가능" — 종료 시간 기준
            Slot([1], "09:00", "14:00"),
            Slot([0, 2, 3, 4], "09:00", "13:00"),
        ],
    ),
    Person("이세영", "nishikiori@naver.com", [Slot([1, 3, 4], "19:00", "21:00")]),
    Person("이명란", "starking0700@naver.com", [Slot([0, 1, 2, 4], "16:00", "21:00")]),
    Person(
        "김수미",
        "glory921@hanmail.net",
        [
            Slot([2], "10:00", "12:00"),
            Slot([4], "10:00", "12:00"),
            Slot([5], "10:00", "11:00"),
        ],
    ),
    Person(
        "배민정",
        "yhamom@naver.com",
        [
            Slot([0], "18:00", "21:00"),
            Slot([1], "19:00", "21:00"),
            Slot([4], "19:00", "21:00"),
        ],
    ),
    Person(
        "조영은",
        "2297evelyn@gmail.com",
        [
            Slot([0, 1, 2, 3], "08:00", "09:00"),
            Slot([0, 2], "13:00", "14:00"),
        ],
    ),
    Person("박미영", "myparkrang@naver.com", [Slot([1], "20:00", "21:00")]),
    Person("김지민", "gracyroh@hanmail.net", [Slot([2], "20:00", "21:00")]),
    Person(
        "홍연서",
        "alberopesca@naver.com",
        [
            Slot([0, 1, 2, 3, 4], "18:00", "21:00"),
            Slot([5], "13:00", "18:00"),
            Slot([6], "09:00", "21:00"),
        ],
    ),
    Person("이정희", "gumboat@naver.com", [Slot([0, 1, 2, 3, 4], "18:00", "21:00")], overseas=True),
    Person("김영창", "va6309@hanmail.net", [Slot(list(range(7)), "17:00", "21:00")]),
    Person("오유진", "iting81@naver.com", [Slot([0, 2, 4], "18:40", "19:40")]),
    Person(
        "구현정",
        "kookoo162@daum.net",
        [
            # 벤쿠버 기준: 한국 오전 ≈ 현지 전일 저녁 (비대면)
            Slot([0, 1, 2, 3, 4], "09:00", "12:00"),
            Slot([2, 4], "10:00", "13:00"),
        ],
        overseas=True,
    ),
    # 하단 15명 (성순희 중복 제외)
    Person("김아름", "arsui90@naver.com", [Slot(list(range(7)), "09:00", "21:00")]),
    Person(
        "성명현",
        "estherborana@gmail.com",
        [
            Slot([4], "14:00", "16:00"),
            Slot([2, 3], "17:00", "21:00"),
        ],
    ),
    Person(
        "고혜숙",
        "sea124@naver.com",
        [
            Slot([0, 1, 2, 3], "17:00", "21:00"),
            Slot([5, 6], "17:00", "21:00"),
        ],
    ),
    Person("박슬아", "poopsc1018@gmail.com", [Slot([0, 1, 4], "14:00", "16:00")]),
    Person("강순화", "kkaldoong@hanmail.net", [Slot([0, 1, 2, 4, 5, 6], "09:00", "12:00")]),
    Person(
        "이경숙",
        "esprit0731@naver.com",
        [
            Slot([2], "09:00", "12:00"),
            Slot([4], "09:00", "12:00"),
            Slot([4], "14:00", "16:00"),
        ],
    ),
    Person("황명자", "dudtjd6262@naver.com", [Slot([0], "14:00", "16:00")]),
    Person("서보영", "suhboyoung68@gmail.com", [Slot([6, 0, 2], "20:00", "23:00")]),
    Person(
        "장경화",
        "jiang_kor@hanmail.net",
        [
            Slot([0, 1], "12:00", "18:00"),  # 월·화 오전(10-12) 제외
            Slot([2, 3, 4], "09:00", "18:00"),
        ],
    ),
    Person("정경화", "hisjoyce77@naver.com", [Slot([1, 2, 3, 4], "09:00", "12:00")]),
    Person(
        "이현옥",
        "gusdhrl@empas.com",
        [
            Slot([0, 1, 2, 3], "09:00", "16:00"),
            Slot([4], "15:00", "17:00"),
            Slot([0], "20:00", "21:00"),  # 월 20-21시
            Slot([3], "18:00", "20:00"),  # 목 18-20시
        ],
    ),
    Person(
        "김효순",
        "kjsilu@naver.com",
        [
            Slot([1, 3], "10:00", "12:00"),
            Slot([5], "15:00", "18:00"),
        ],
    ),
    Person(
        "김선경",
        "sanqiong@naver.com",
        [
            Slot([0, 2], "09:00", "12:00"),
            Slot([1], "08:00", "10:00"),
            Slot([4], "15:00", "17:00"),
        ],
    ),
    Person(
        "황윤진",
        "5671469@naver.com",
        [
            Slot([0], "08:00", "12:00"),
            Slot([2, 3], "13:00", "17:00"),
            Slot([6], "19:00", "21:00"),
        ],
    ),
]


def _can_assign(cn: str, kn: str, edges: list[tuple[int, str, str, int]]) -> bool:
    return any(c == cn and k == kn for _, c, k, _ in edges)


def rebalance_unassigned(
    assignment: dict[str, list[Person]],
    active: list[Person],
    edges: list[tuple[int, str, str, int]],
) -> dict[str, list[Person]]:
    """미배정 내담자 — 다른 상담사 내담자와 교환 시도."""
    unmatched = assignment.pop("__UNMATCHED__", [])
    if not unmatched:
        return assignment

    still: list[Person] = []
    for client in unmatched:
        placed = False
        for kn in [c.name for c in COUNSELORS]:
            if len(assignment[kn]) >= 2:
                continue
            if _can_assign(client.name, kn, edges):
                assignment[kn].append(client)
                placed = True
                break
        if placed:
            continue

        for kn in [c.name for c in COUNSELORS]:
            if not _can_assign(client.name, kn, edges):
                continue
            for i, movable in enumerate(list(assignment[kn])):
                for kn2 in [c.name for c in COUNSELORS]:
                    if kn2 == kn or len(assignment[kn2]) >= 2:
                        continue
                    if not _can_assign(movable.name, kn2, edges):
                        continue
                    assignment[kn2].append(movable)
                    assignment[kn][i] = client
                    placed = True
                    break
                if placed:
                    break
            if placed:
                break

        if not placed:
            for kn in [c.name for c in COUNSELORS]:
                if len(assignment[kn]) != 2:
                    continue
                if not _can_assign(client.name, kn, edges):
                    continue
                for i, movable in enumerate(list(assignment[kn])):
                    for kn2 in [c.name for c in COUNSELORS]:
                        if kn2 == kn:
                            continue
                        if len(assignment[kn2]) >= 2:
                            continue
                        if not _can_assign(movable.name, kn2, edges):
                            continue
                        assignment[kn2].append(movable)
                        del assignment[kn][i]
                        assignment[kn].append(client)
                        placed = True
                        break
                    if placed:
                        break
                if placed:
                    break

        if not placed:
            still.append(client)

    if still:
        assignment["__UNMATCHED__"] = still
    return assignment


def assign_clients() -> dict[str, list[Person]]:
    """상담사당 최대 2명 — 제약 만족 우선, 겹침 분수 최대화."""
    active = [c for c in CLIENTS if not c.skip]
    counselor_map = {c.name: c for c in COUNSELORS}
    assignment: dict[str, list[Person]] = {c.name: [] for c in COUNSELORS}

    # (client, counselor, overlap_minutes)
    edges: list[tuple[int, str, str, int]] = []
    for i, client in enumerate(active):
        for counselor in COUNSELORS:
            mins = weekly_overlap_minutes(client, counselor)
            if mins >= DURATION:
                edges.append((i, client.name, counselor.name, mins))

    # 각 내담자별 가능 상담사 수
    options: dict[str, set[str]] = {}
    for _, cn, kn, _ in edges:
        options.setdefault(cn, set()).add(kn)

    unassigned = {c.name for c in active}

    def pair_score(cn: str, kn: str) -> tuple[int, int]:
        base = next(m for _, c, k, m in edges if c == cn and k == kn)
        bonus = 0
        c_over = cn in OVERSEAS_CLIENTS
        k_over = kn in OVERSEAS_COUNSELORS
        if c_over and k_over:
            bonus += 500_000
        elif c_over and not k_over:
            bonus -= 200_000
        elif not c_over and k_over and len(assignment[kn]) == 0:
            bonus += 10_000
        return (bonus + base, base)

    def sort_key_client(name: str) -> tuple[int, int]:
        c_over = 0 if name in OVERSEAS_CLIENTS else 1
        return (c_over, len(options.get(name, set())))

    while unassigned:
        cn = min(unassigned, key=sort_key_client)
        cands = [
            kn
            for kn in options.get(cn, set())
            if len(assignment[kn]) < 2
        ]
        if not cands:
            assignment.setdefault("__UNMATCHED__", []).append(next(c for c in active if c.name == cn))
            unassigned.remove(cn)
            continue
        kn = max(cands, key=lambda k: (pair_score(cn, k), -len(assignment[k])))
        client = next(c for c in active if c.name == cn)
        assignment[kn].append(client)
        unassigned.remove(cn)

    return rebalance_unassigned(assignment, active, edges)


def schedule_all(assignment: dict[str, list[Person]]) -> list[dict]:
    counselor_map = {c.name: c for c in COUNSELORS}
    blocked: list[Blocked] = []
    rows: list[dict] = []

    pending: list[tuple[str, Person]] = []
    for kn in sorted(counselor_map):
        for client in assignment.get(kn, []):
            pending.append((kn, client))

    def constraint_score(kn: str, client: Person) -> int:
        end = START_DATE + timedelta(weeks=WEEKS)
        d = START_DATE
        count = 0
        while d <= end and count < 999:
            count += len(iter_starts(client, counselor_map[kn], d))
            d += timedelta(days=1)
        return count

    pending.sort(key=lambda x: constraint_score(x[0], x[1]))

    for kn, client in pending:
        slot = find_slot(client, counselor_map[kn], blocked)
        c_over = client.name in OVERSEAS_CLIENTS
        k_over = kn in OVERSEAS_COUNSELORS
        note = "확정"
        if c_over and k_over:
            note = "확정(해외-해외)"
        elif k_over:
            note = "확정(해외상담사)"
        elif c_over:
            note = "확정(해외내담자)"
        if slot:
            blocked.append(Blocked(slot, slot + timedelta(minutes=DURATION)))
            rows.append(
                {
                    "counselor": kn,
                    "client": client.name,
                    "email": client.email,
                    "datetime": slot,
                    "status": note,
                    "counselor_overseas": k_over,
                    "client_overseas": c_over,
                }
            )
        else:
            rows.append(
                {
                    "counselor": kn,
                    "client": client.name,
                    "email": client.email,
                    "datetime": None,
                    "status": "시간대 배정 불가",
                    "counselor_overseas": k_over,
                    "client_overseas": c_over,
                }
            )

    for client in assignment.get("__UNMATCHED__", []):
        rows.append(
            {
                "counselor": "-",
                "client": client.name,
                "email": client.email,
                "datetime": None,
                "status": "상담사 매칭 불가",
            }
        )

    for client in CLIENTS:
        if client.skip:
            rows.append(
                {
                    "counselor": "-",
                    "client": client.name,
                    "email": client.email,
                    "datetime": None,
                    "status": client.skip_reason,
                }
            )

    return rows


def main():
    assignment = assign_clients()
    rows = schedule_all(assignment)

    weekdays = "월화수목금토일"
    import json
    from pathlib import Path

    out_path = Path(__file__).resolve().parents[1] / "data" / "import" / "session1_match_result_20260615.json"
    serializable = []
    for r in rows:
        serializable.append(
            {
                **r,
                "datetime": r["datetime"].strftime("%Y-%m-%d %H:%M") if r["datetime"] else None,
                "weekday": weekdays[r["datetime"].weekday()] if r["datetime"] else "",
            }
        )
    out_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 88)
    print("1회기 상담 일정 매칭 (시작일: 2026-06-15, 회기 50분, Zoom 동시 1회·동일일 2시간 간격)")
    print("=" * 88)
    print(f"{'No':<4} {'상담사':<8} {'내담자':<8} {'1회기 일시':<22} {'요일':<4} {'비고'}")
    print("-" * 88)

    matched = sorted(
        [r for r in rows if r["datetime"]],
        key=lambda r: r["datetime"],
    )
    others = [r for r in rows if not r["datetime"]]

    for i, r in enumerate(matched, 1):
        dt: datetime = r["datetime"]
        wd = weekdays[dt.weekday()]
        print(
            f"{i:<4} {r['counselor']:<8} {r['client']:<8} "
            f"{dt.strftime('%Y-%m-%d %H:%M'):<22} {wd:<4} {r['status']}"
        )

    print("\n--- 미배정 / 수동 조율 ---")
    for r in others:
        print(f"  {r['client']} ({r['email']}) → {r['counselor']}: {r['status']}")

    print("\n--- 상담사별 배정 (2명 기준) ---")
    for kn in sorted({c.name for c in COUNSELORS}):
        clients = [r["client"] for r in matched if r["counselor"] == kn]
        if clients:
            print(f"  {kn}: {', '.join(clients)} ({len(clients)}명)")

    print(f"\n총 확정: {len(matched)}명 / 대상 {len([c for c in CLIENTS if not c.skip])}명")


if __name__ == "__main__":
    main()
