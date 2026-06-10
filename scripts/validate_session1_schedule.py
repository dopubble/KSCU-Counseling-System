"""배정된 1회기가 내담자·상담사 요구 시간과 겹치는지 검증."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from match_session1_20260615 import (
    CLIENTS,
    COUNSELORS,
    DURATION,
    counselor_slots_on,
    iter_starts,
    patch_leeyoungsil,
)

patch_leeyoungsil()

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "import" / "session1_schedule_full_table.csv"


def client_by_name(name: str):
    for c in CLIENTS:
        if c.name == name:
            return c
    return None


def counselor_by_name(name: str):
    for c in COUNSELORS:
        if c.name == name:
            return c
    return None


def slot_valid_at(client, counselor, at: datetime) -> bool:
    candidates = iter_starts(client, counselor, at.date())
    return any(c.replace(second=0, microsecond=0) == at.replace(second=0, microsecond=0) for c in candidates)


def main():
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    ok, bad = [], []
    for row in rows:
        kn, cn, dt_str = row["상담사"], row["내담자"], row["1회기"]
        if not dt_str.strip():
            bad.append((kn, cn, dt_str, "1회기 미배정"))
            continue
        at = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        client = client_by_name(cn)
        counselor = counselor_by_name(kn)
        if not client or not counselor:
            bad.append((kn, cn, dt_str, "시드 데이터 없음"))
            continue
        if slot_valid_at(client, counselor, at):
            ok.append((kn, cn, dt_str))
        else:
            # suggest first valid
            from match_session1_20260615 import find_slot, Blocked

            alt = find_slot(client, counselor, [])
            bad.append((kn, cn, dt_str, f"불일치 (대안: {alt.strftime('%Y-%m-%d %H:%M') if alt else '없음'})"))

    print(f"=== 검증 결과: OK {len(ok)} / 불일치 {len(bad)} ===\n")
    for kn, cn, dt, reason in bad:
        print(f"  {kn} - {cn} | {dt} | {reason}")
    if not bad:
        print("  전원 시간 교집합 일치")


if __name__ == "__main__":
    main()
