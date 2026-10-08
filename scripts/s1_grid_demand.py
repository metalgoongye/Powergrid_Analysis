# -*- coding: utf-8 -*-
"""1단계-4: 100m 격자 전력수요 지도 — 기준년 2024, 3벌 산출(ⓐ무보정·ⓑ규율보정·ⓒ플래그제외).

구조
  패스1  에너지 12개월 → PNU별 연간 kWh (dict)
  패스2  연속지적 zip의 dbf(PNU)·shp(레코드 bbox)를 레코드 단위로 나란히 스트리밍,
         에너지 PNU에 해당하는 필지만 bbox 중심점 추출 → 5186→5179 변환 →
         100m 격자(EPSG:5179, floor(x/100), floor(y/100))에 누적
  보정   시군구 커버리지(s1_sgg_coverage_2024.csv)에서 자치단체별 계수 산출:
         신뢰구간(55~130%)이면 계수 = 100/커버리지, 아니면 플래그.
         ⓐ 원값 그대로 / ⓑ 신뢰구간은 ×계수·플래그는 원값 / ⓒ 플래그 단위 제외

주의: 중심점은 필지 bbox 중심(대형 임야 필지에서 근사 오차 가능 — 에너지 필지는
대부분 도시 소형이라 100m 격자에서 무해. 산출물 메타에 명시).

사용법:  python scripts/s1_grid_demand.py [연도=2024]
산출물:  out/public/s1_grid_demand_<년>.csv.gz  (격자 집계 — 공개 등급)
         out/public/s1_grid_demand_<년>_meta.txt
"""
from __future__ import annotations

import csv
import gzip
import io
import os
import struct
import sys
import zipfile
from collections import defaultdict

from pyproj import Transformer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
ENERGY_DIR = os.path.join(DATA_ROOT, "rdb", "건물에너지_지번별월별_24_25")
CADASTRE_DIR = os.path.join(DATA_ROOT, "powergrid_spatial_data", "연속지적도형정보")
COVER = os.path.join(REPO, "out", "public", "s1_sgg_coverage_2024.csv")
OUT = os.path.join(REPO, "out", "public")

SIDOS = ["11", "26", "27", "28", "29", "30", "31", "36", "41",
         "43", "44", "46", "47", "48", "50", "51", "52"]
GRID = 100.0  # m (EPSG:5179)
TRUST_LO, TRUST_HI = 55.0, 130.0


def member_name(info: zipfile.ZipInfo) -> str:
    try:
        return info.filename.encode("cp437").decode("cp949")
    except Exception:
        return info.filename


def energy_annual(year: str) -> dict[str, float]:
    kwh: dict[str, float] = defaultdict(float)
    for mm in range(1, 13):
        zf = zipfile.ZipFile(os.path.join(
            ENERGY_DIR, f"MART_KEY_ELCTY_ENERGY_DATA_{year}{mm:02d}.zip"))
        txt = [i for i in zf.infolist() if member_name(i).lower().endswith(".txt")][0]
        with zf.open(txt) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):
                p = line.split("|")
                if len(p) < 17:
                    continue
                mt = p[7]
                if mt == "0":
                    g = "1"
                elif mt == "1":
                    g = "2"
                else:
                    continue
                try:
                    kwh[f"{p[2]:0>5}{p[3]:0>5}{g}{p[8]:0>4}{p[9]:0>4}"] += float(p[16])
                except ValueError:
                    pass
        print(f"  에너지 {year}-{mm:02d} 적재")
    return kwh


def unit_coefs() -> tuple[dict[str, float], dict[str, bool]]:
    """시군구코드 → (보정계수, 플래그 여부). 커버리지 CSV의 '에너지코드' 열 사용."""
    coef: dict[str, float] = {}
    flag: dict[str, bool] = {}
    with open(COVER, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            cov = float(row["커버리지_주택일반(%)"])
            is_flag = not (TRUST_LO <= cov <= TRUST_HI)
            for code in row["에너지코드"].split(";"):
                coef[code] = 1.0 if is_flag else 100.0 / cov
                flag[code] = is_flag
    return coef, flag


def shp_record_bboxes(zf: zipfile.ZipFile, info: zipfile.ZipInfo):
    """shp 스트림에서 레코드별 (xmin,ymin,xmax,ymax)를 순서대로 생성."""
    with zf.open(info) as f:
        f.read(100)  # 파일 헤더
        while True:
            rh = f.read(8)
            if len(rh) < 8:
                break
            clen = struct.unpack(">i", rh[4:8])[0] * 2  # 내용 바이트 수
            body = f.read(clen)
            if len(body) < clen:
                break
            stype = struct.unpack("<i", body[:4])[0]
            if stype == 5 and clen >= 36:  # Polygon
                yield struct.unpack("<4d", body[4:36])
            else:  # null 등 — 자리 유지
                yield None


def dbf_pnus(zf: zipfile.ZipFile, info: zipfile.ZipInfo):
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
        off1, len1 = 1 + flens[0], flens[1]
        remain = nrec * rsize
        rows = max(1, (8 << 20) // rsize)
        while remain > 0:
            chunk = f.read(min(rows * rsize, remain))
            if not chunk:
                break
            for base in range(0, len(chunk) - rsize + 1, rsize):
                yield chunk[base + off1:base + off1 + len1].decode("ascii", "replace")
            remain -= len(chunk)


def main() -> int:
    year = sys.argv[1] if len(sys.argv) > 1 else "2024"
    print(f"[패스1] 에너지 {year} 연간 PNU 집계…")
    kwh = energy_annual(year)
    print(f"  고유 PNU {len(kwh):,}, 총 {sum(kwh.values())/1e9:.2f} TWh")

    coef, flag = unit_coefs()
    tr = Transformer.from_crs(5186, 5179, always_xy=True)

    grid_a: dict[tuple[int, int], float] = defaultdict(float)
    grid_b: dict[tuple[int, int], float] = defaultdict(float)
    grid_c: dict[tuple[int, int], float] = defaultdict(float)
    npt = miss_geom = 0

    print("[패스2] 지적 중심점 추출·격자 누적…")
    for sido in SIDOS:
        zf = zipfile.ZipFile(os.path.join(CADASTRE_DIR, f"AL_D002_{sido}_20241204.zip"))
        infos = {member_name(i): i for i in zf.infolist()}
        parts = sorted(n[:-4] for n in infos if n.lower().endswith(".dbf"))
        xs, ys, vals, codes = [], [], [], []
        for stem in parts:
            for pnu, bbox in zip(dbf_pnus(zf, infos[stem + ".dbf"]),
                                 shp_record_bboxes(zf, infos[stem + ".shp"])):
                v = kwh.get(pnu)
                if v is None:
                    continue
                if bbox is None:
                    miss_geom += 1
                    continue
                xs.append((bbox[0] + bbox[2]) / 2)
                ys.append((bbox[1] + bbox[3]) / 2)
                vals.append(v)
                codes.append(pnu[:5])
        if xs:
            tx, ty = tr.transform(xs, ys)
            for x, y, v, cd in zip(tx, ty, vals, codes):
                cell = (int(x // GRID), int(y // GRID))
                grid_a[cell] += v
                grid_b[cell] += v * coef.get(cd, 1.0)
                if not flag.get(cd, False):
                    grid_c[cell] += v
            npt += len(xs)
        print(f"  {sido}: 누적 필지 {npt:,}")

    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, f"s1_grid_demand_{year}.csv.gz")
    cells = sorted(set(grid_a) | set(grid_b) | set(grid_c))
    with gzip.open(out_path, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["gx", "gy", "kwh_raw", "kwh_adj", "kwh_excl"])
        for c in cells:
            w.writerow([c[0], c[1], round(grid_a.get(c, 0.0), 1),
                        round(grid_b.get(c, 0.0), 1), round(grid_c.get(c, 0.0), 1)])

    meta = os.path.join(OUT, f"s1_grid_demand_{year}_meta.txt")
    with open(meta, "w", encoding="utf-8") as f:
        f.write(
            f"격자: 100m, EPSG:5179, 셀ID = floor(x/100), floor(y/100)\n"
            f"기준년 {year} · 생성 스크립트 s1_grid_demand.py\n"
            f"중심점 = 필지 bbox 중심(근사) · 좌표 변환 pyproj 5186→5179\n"
            f"격자화 필지 {npt:,} / 에너지 PNU {len(kwh):,} "
            f"(기하 결측 {miss_geom:,})\n"
            f"ⓐ raw  합계 {sum(grid_a.values())/1e9:.3f} TWh\n"
            f"ⓑ adj  합계 {sum(grid_b.values())/1e9:.3f} TWh "
            f"(신뢰구간 {TRUST_LO}~{TRUST_HI}% 단위만 계수 적용, 플래그는 원값)\n"
            f"ⓒ excl 합계 {sum(grid_c.values())/1e9:.3f} TWh (플래그 단위 제외)\n"
            f"점유 셀 {len(cells):,}\n"
            f"원천: 건물에너지 공개본(주택+일반 성격)·연속지적 2024-12-04 — "
            f"산업용은 별도 배분 트랙(미포함)\n"
        )
    print(open(meta, encoding="utf-8").read())
    print(f"저장: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
