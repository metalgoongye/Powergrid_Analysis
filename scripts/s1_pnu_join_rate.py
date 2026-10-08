# -*- coding: utf-8 -*-
"""1단계-1: 건물에너지(지번별) × 연속지적도 PNU 조인율 측정.

에너지 레코드의 (시군구코드+법정동코드+산구분+본번+부번)으로 19자리 PNU를 만들어
연속지적도(AL_D002)의 PNU 집합과 대조한다. 시도별로 지적 PNU 집합을 하나씩만
메모리에 올리는 2-패스 구조(표준 라이브러리만 사용).

사용법
    python scripts/s1_pnu_join_rate.py            # 기본: 2024-12
    python scripts/s1_pnu_join_rate.py 202406     # 다른 달
산출물
    out/public/s1_join_rate_<YYYYMM>.csv  (시도별 집계 — 좌표 없음, 공개 등급)
"""
from __future__ import annotations

import csv
import io
import os
import struct
import sys
import zipfile
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
ENERGY_DIR = os.path.join(DATA_ROOT, "rdb", "건물에너지_지번별월별_24_25")
CADASTRE_DIR = os.path.join(DATA_ROOT, "powergrid_spatial_data", "연속지적도형정보")
OUT = os.path.join(REPO, "out", "public")

SIDO_NAME = {
    "11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주",
    "30": "대전", "31": "울산", "36": "세종", "41": "경기", "43": "충북",
    "44": "충남", "46": "전남", "47": "경북", "48": "경남", "50": "제주",
    "51": "강원", "52": "전북",
}


def member_name(info: zipfile.ZipInfo) -> str:
    try:
        return info.filename.encode("cp437").decode("cp949")
    except Exception:
        return info.filename


def energy_rows(yyyymm: str):
    """에너지 월 파일에서 (시도2, PNU19, kWh) 생성. 산구분: 0→1(일반), 1→2(산)."""
    zpath = os.path.join(ENERGY_DIR, f"MART_KEY_ELCTY_ENERGY_DATA_{yyyymm}.zip")
    zf = zipfile.ZipFile(zpath)
    txt = [i for i in zf.infolist() if member_name(i).lower().endswith(".txt")][0]
    bad_flag = 0
    with zf.open(txt) as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):
            p = line.rstrip("\n").split("|")
            if len(p) < 17:
                continue
            sgg, emd, mt, bon, bu = p[2], p[3], p[7], p[8], p[9]
            if mt == "0":
                g = "1"
            elif mt == "1":
                g = "2"
            else:
                bad_flag += 1
                continue
            pnu = f"{sgg:0>5}{emd:0>5}{g}{bon:0>4}{bu:0>4}"
            try:
                kwh = float(p[16])
            except ValueError:
                kwh = 0.0
            yield sgg[:2], pnu, kwh
    if bad_flag:
        print(f"  [주의] 산구분이 0/1이 아닌 행 {bad_flag:,}건 제외")


def load_parcel_pnus(sido: str) -> set[str]:
    """해당 시도 연속지적 zip의 모든 파트에서 PNU(필드 1) 집합을 만든다."""
    zpath = os.path.join(CADASTRE_DIR, f"AL_D002_{sido}_20241204.zip")
    zf = zipfile.ZipFile(zpath)
    pnus: set[str] = set()
    for info in zf.infolist():
        if not member_name(info).lower().endswith(".dbf"):
            continue
        with zf.open(info) as f:
            hdr = f.read(32)
            nrec = struct.unpack("<I", hdr[4:8])[0]
            hsize = struct.unpack("<H", hdr[8:10])[0]
            rsize = struct.unpack("<H", hdr[10:12])[0]
            rest = f.read(hsize - 32)
            flens = []
            for off in range(0, len(rest) - 31, 32):
                fd = rest[off:off + 32]
                if fd[0:1] == b"\r":
                    break
                flens.append(fd[16])
            # PNU = 두 번째 필드: 오프셋 = 삭제플래그(1) + 필드0 길이
            pnu_off = 1 + flens[0]
            pnu_len = flens[1]
            buf_target = nrec * rsize
            read_done = 0
            chunk_rows = max(1, (8 << 20) // rsize)
            while read_done < buf_target:
                chunk = f.read(min(chunk_rows * rsize, buf_target - read_done))
                if not chunk:
                    break
                for base in range(0, len(chunk) - rsize + 1, rsize):
                    if chunk[base:base + 1] == b"*":  # 삭제 레코드
                        continue
                    pnus.add(
                        chunk[base + pnu_off:base + pnu_off + pnu_len].decode("ascii", "replace")
                    )
                read_done += len(chunk)
    return pnus


def main() -> int:
    yyyymm = sys.argv[1] if len(sys.argv) > 1 else "202412"
    print(f"대상 월: {yyyymm}\n[1/2] 에너지 레코드 적재·시도별 분류 중…")
    by_sido: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for sido, pnu, kwh in energy_rows(yyyymm):
        by_sido[sido].append((pnu, kwh))
    n_total = sum(len(v) for v in by_sido.values())
    print(f"  에너지 레코드 {n_total:,}건, 시도 {len(by_sido)}개")

    unknown = sorted(set(by_sido) - set(SIDO_NAME))
    if unknown:
        print(f"  [주의] 지적도에 없는 시도코드: {unknown} — 해당 레코드는 미조인 처리")

    print("[2/2] 시도별 지적 PNU 대조 중…")
    rows_out = []
    g_rec = g_hit = 0
    g_kwh = g_kwh_hit = 0.0
    for sido in sorted(by_sido):
        recs = by_sido[sido]
        nrec = len(recs)
        kwh_sum = sum(k for _, k in recs)
        if sido in SIDO_NAME:
            pnus = load_parcel_pnus(sido)
            hit = sum(1 for p, _ in recs if p in pnus)
            kwh_hit = sum(k for p, k in recs if p in pnus)
            del pnus
        else:
            hit, kwh_hit = 0, 0.0
        g_rec += nrec
        g_hit += hit
        g_kwh += kwh_sum
        g_kwh_hit += kwh_hit
        name = SIDO_NAME.get(sido, "?")
        rows_out.append(
            [sido, name, nrec, hit, round(100 * hit / nrec, 2) if nrec else 0,
             round(kwh_sum), round(kwh_hit),
             round(100 * kwh_hit / kwh_sum, 2) if kwh_sum else 0]
        )
        print(f"  {sido} {name:<3} 레코드 {nrec:>9,} | 조인 {100*hit/max(nrec,1):6.2f}% "
              f"| 사용량 기준 {100*kwh_hit/max(kwh_sum,1e-9):6.2f}%")

    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, f"s1_join_rate_{yyyymm}.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시도코드", "시도", "에너지레코드", "조인성공", "조인율_레코드(%)",
                    "사용량_kWh", "조인사용량_kWh", "조인율_사용량(%)"])
        w.writerows(rows_out)
        w.writerow(["전국", "", g_rec, g_hit, round(100 * g_hit / g_rec, 2),
                    round(g_kwh), round(g_kwh_hit), round(100 * g_kwh_hit / g_kwh, 2)])
    print(f"\n전국: 레코드 기준 {100*g_hit/g_rec:.2f}% · 사용량 기준 {100*g_kwh_hit/g_kwh:.2f}%")
    print(f"저장: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
