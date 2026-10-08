# -*- coding: utf-8 -*-
"""1단계-2: 건물에너지 전체 월(2023-11~2025-12)의 PNU 조인율·총량 추이.

구조(메모리 절약형 2패스):
  패스1  전 월 에너지 파일을 훑어 고유 PNU 전집을 수집
  중간   시도별 연속지적 PNU와 교차 → '조인 성공 PNU' 집합 생성(지적은 시도당 1회 적재)
  패스2  월별로 다시 훑어 레코드·사용량 기준 조인율과 총량 산출
판매통계(주택용+일반용)가 있는 달은 커버리지(%)도 함께 계산한다.

사용법:  python scripts/s1_join_trend.py
산출물:  out/public/s1_join_trend.csv  (월별 집계 — 공개 등급)
"""
from __future__ import annotations

import csv
import io
import os
import re
import struct
import sys
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
ENERGY_DIR = os.path.join(DATA_ROOT, "rdb", "건물에너지_지번별월별_24_25")
CADASTRE_DIR = os.path.join(DATA_ROOT, "powergrid_spatial_data", "연속지적도형정보")
SALES_DIR = os.path.join(DATA_ROOT, "rdb", "시군구별_월별_계약종별판매전력량_2023_2024")
OUT = os.path.join(REPO, "out", "public")

SIDOS = ["11", "26", "27", "28", "29", "30", "31", "36", "41",
         "43", "44", "46", "47", "48", "50", "51", "52"]


def member_name(info: zipfile.ZipInfo) -> str:
    try:
        return info.filename.encode("cp437").decode("cp949")
    except Exception:
        return info.filename


def months() -> list[str]:
    ms = []
    for f in sorted(os.listdir(ENERGY_DIR)):
        m = re.match(r"MART_KEY_ELCTY_ENERGY_DATA_(\d{6})\.zip$", f)
        if m:
            ms.append(m.group(1))
    return ms


def energy_rows(yyyymm: str):
    zf = zipfile.ZipFile(os.path.join(ENERGY_DIR, f"MART_KEY_ELCTY_ENERGY_DATA_{yyyymm}.zip"))
    txt = [i for i in zf.infolist() if member_name(i).lower().endswith(".txt")][0]
    with zf.open(txt) as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):
            p = line.rstrip("\n").split("|")
            if len(p) < 17:
                continue
            mt = p[7]
            if mt == "0":
                g = "1"
            elif mt == "1":
                g = "2"
            else:
                continue
            pnu = f"{p[2]:0>5}{p[3]:0>5}{g}{p[8]:0>4}{p[9]:0>4}"
            try:
                kwh = float(p[16])
            except ValueError:
                kwh = 0.0
            yield pnu, kwh


def load_parcel_pnus(sido: str) -> set[str]:
    zf = zipfile.ZipFile(os.path.join(CADASTRE_DIR, f"AL_D002_{sido}_20241204.zip"))
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
            pnu_off, pnu_len = 1 + flens[0], flens[1]
            remain = nrec * rsize
            rows_per_chunk = max(1, (8 << 20) // rsize)
            while remain > 0:
                chunk = f.read(min(rows_per_chunk * rsize, remain))
                if not chunk:
                    break
                for base in range(0, len(chunk) - rsize + 1, rsize):
                    if chunk[base:base + 1] != b"*":
                        pnus.add(chunk[base + pnu_off:base + pnu_off + pnu_len]
                                 .decode("ascii", "replace"))
                remain -= len(chunk)
    return pnus


def sales_home_general() -> dict[str, float]:
    """(YYYYMM) → 주택용+일반용 판매량 kWh (판매통계 보유 연도만)."""
    out: dict[str, float] = {}
    for f in os.listdir(SALES_DIR):
        if not f.endswith(".csv"):
            continue
        with open(os.path.join(SALES_DIR, f), encoding="utf-8-sig") as fh:
            for r in csv.reader(fh):
                if len(r) > 6 and r[4] in ("주택용", "일반용"):
                    try:
                        out[r[0] + r[1].zfill(2)] = out.get(r[0] + r[1].zfill(2), 0.0) + float(r[6])
                    except ValueError:
                        pass
    return out


def main() -> int:
    ms = months()
    print(f"대상: {len(ms)}개월 ({ms[0]} ~ {ms[-1]})")

    print("[패스1] 고유 PNU 전집 수집…")
    all_pnus: set[str] = set()
    for m in ms:
        before = len(all_pnus)
        for pnu, _ in energy_rows(m):
            all_pnus.add(pnu)
        print(f"  {m}: 누적 고유 PNU {len(all_pnus):,} (+{len(all_pnus)-before:,})")

    print("[중간] 시도별 지적 PNU 교차…")
    matched: set[str] = set()
    for sido in SIDOS:
        pp = load_parcel_pnus(sido)
        matched |= all_pnus & pp
        del pp
    print(f"  고유 PNU {len(all_pnus):,} 중 지적 일치 {len(matched):,} "
          f"({100*len(matched)/len(all_pnus):.2f}%)")

    print("[패스2] 월별 조인율·총량…")
    sales = sales_home_general()
    rows_out = []
    for m in ms:
        n = hit = 0
        kwh = kwh_hit = 0.0
        for pnu, k in energy_rows(m):
            n += 1
            kwh += k
            if pnu in matched:
                hit += 1
                kwh_hit += k
        cov = round(100 * kwh / sales[m], 2) if m in sales and sales[m] > 0 else ""
        rows_out.append([m, n, round(100 * hit / n, 2), round(kwh / 1e9, 3),
                         round(100 * kwh_hit / kwh, 2), cov])
        cov_s = f" | 커버리지(주택+일반) {cov}%" if cov != "" else ""
        print(f"  {m}: 레코드 {n:,} | 조인 {100*hit/n:6.2f}% | "
              f"{kwh/1e9:6.2f} TWh | 사용량조인 {100*kwh_hit/kwh:6.2f}%{cov_s}")

    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "s1_join_trend.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["사용년월", "에너지레코드", "조인율_레코드(%)", "총사용량_TWh",
                    "조인율_사용량(%)", "커버리지_주택일반대비(%)"])
        w.writerows(rows_out)
    print(f"저장: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
