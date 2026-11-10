# -*- coding: utf-8 -*-
"""1단계-5: 산업용 전력의 격자 배분 — 공장 연면적 비례 (기준년 2024).

건물에너지 공개본은 건물계 부하(주택+일반)만 담아 산업용(연 ~285TWh)이
수요지도에서 빠져 있다. 한전 시군구별 '산업용' 판매량을 그 시군구 안
공장 용도 건물(GIS건물통합 A8=17000)의 연면적(A14) 비례로 배분한다.

  cell_kWh = Σ_건물∈cell  판매_산업용(단위 u) × 연면적_b / Σ_{b'∈u} 연면적_b'

- 판매 단위 u = 한전 판매 통계의 (시도, 시군구) — references/sgg_name_code_map.csv
  로 건물의 시군구코드(법정동코드 앞 5자리)를 판매 단위에 귀속(사전 미매칭 0).
- 건물 좌표 = shp 레코드 bbox 중심(5186) → 5179 → 100m 격자. dbf와 shp를
  레코드 순서대로 나란히 스트리밍.
- 공장 연면적이 0인 판매 단위는 배분 불가 — 미배분으로 보고(임의 대체 없음).

검증: 단위별 배분 합 = 판매량(배분 가능 단위), 전국 배분률, dbf/shp 레코드 수 일치.

출력: out/public/s1_grid_industrial_2024.csv.gz (gx, gy, kWh_ind)
      out/public/s1_industrial_alloc_summary.csv
"""
from __future__ import annotations

import csv
import gzip
import os
import struct
import sys
import zipfile
from collections import defaultdict

import numpy as np
from pyproj import Transformer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
BLD_DIR = os.path.join(DATA_ROOT, "powergrid_spatial_data", "GIS건물통합정보")
SALES = os.path.join(DATA_ROOT, "rdb", "시군구별_월별_계약종별판매전력량_2023_2024",
                     "한전_시군구별_계약종별_월별_판매전력량_2024.csv")
SGG_MAP = os.path.join(REPO, "references", "sgg_name_code_map.csv")
PUB = os.path.join(REPO, "out", "public")
SIDOS = ["11", "26", "27", "28", "29", "30", "31", "36", "41",
         "43", "44", "46", "47", "48", "50", "51", "52"]
GRID = 100.0
FACTORY = b"17000"
CAPITAL = ("서울", "인천", "경기")
T = Transformer.from_crs(5186, 5179, always_xy=True)


def load_code_map():
    """시군구 사전 → (건물 시군구코드 → 정준 단위, 판매 명칭 → 정준 단위, 라벨).

    정준 단위 = 그 명칭이 가리키는 코드 집합. 같은 코드 집합을 가리키는 명칭
    (예: 2024-01의 '전라북도 X'와 02~12월의 '전북특별자치도 X')은 한 단위로 합친다.
    """
    codes = defaultdict(set)
    with open(SGG_MAP, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            codes[(r["판매_시도"], r["판매_시군구"])].add(r["에너지_시군구코드"])
    name2canon, code2canon, label = {}, {}, {}
    for name, cs in codes.items():
        canon = tuple(sorted(cs))
        name2canon[name] = canon
        label[canon] = name  # 마지막 명칭을 표시용으로
        for c in cs:
            code2canon[c] = canon
    return code2canon, name2canon, label


def load_sales(name2canon):
    """정준 판매 단위 → 2024 산업용 연간 kWh."""
    out = defaultdict(float)
    kinds = set()
    with open(SALES, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            kinds.add(r["계약종별"])
            if r["계약종별"] == "산업용":
                name = (r["시도"], r["시군구"])
                assert name in name2canon, f"판매 단위가 사전에 없음: {name}"
                out[name2canon[name]] += float(r["사용량(kWh)"])
    assert "산업용" in kinds, f"계약종별에 '산업용' 없음: {kinds}"
    return out


def dbf_layout(f):
    hdr = f.read(32)
    n = struct.unpack("<I", hdr[4:8])[0]
    hl, rl = struct.unpack("<2H", hdr[8:12])
    desc = f.read(hl - 32)
    off, lay = 1, {}
    for k in range(0, len(desc) - 1, 32):
        fd = desc[k:k + 32]
        if fd[0] == 0x0D:
            break
        name = fd[:11].split(b"\0")[0].decode("ascii")
        lay[name] = (off, fd[16])
        off += fd[16]
    assert off == rl, f"dbf 레이아웃 불일치 {off} != {rl}"
    return n, rl, lay


def read_part(zf, dbf_name, cmap, acc):
    """zip 안 shapefile 한 파트(dbf+shp)를 나란히 읽어 공장 건물을 acc에 누적."""
    with zf.open(dbf_name) as fd, zf.open(dbf_name[:-4] + ".shp") as fs:
        n, rl, lay = dbf_layout(fd)
        o3 = lay["A3"][0]
        o8, l8 = lay["A8"]
        o14, l14 = lay["A14"]
        fs.read(100)
        done = 0
        while done < n:
            k = min(4096, n - done)
            buf = fd.read(rl * k)
            for j in range(k):
                base = j * rl
                rh = fs.read(8)  # shp 레코드 헤더
                body = fs.read(struct.unpack(">i", rh[4:8])[0] * 2)
                if buf[base + o8:base + o8 + l8] != FACTORY:
                    continue
                acc["n_fac"] += 1
                try:
                    a = float(buf[base + o14:base + o14 + l14])
                except ValueError:
                    a = 0.0
                if a <= 0:
                    acc["n_noarea"] += 1
                    continue
                if struct.unpack("<i", body[:4])[0] == 0:
                    continue  # 도형 없음
                u = cmap.get(buf[base + o3:base + o3 + 5].decode("ascii", "replace"))
                if u is None:
                    acc["n_nomap"] += 1
                    continue
                x0, y0, x1, y1 = struct.unpack("<4d", body[4:36])
                acc["units"].append(u)
                acc["xs"].append((x0 + x1) / 2)
                acc["ys"].append((y0 + y1) / 2)
                acc["areas"].append(a)
            done += k
    return n


def main() -> int:
    cmap, name2canon, label = load_code_map()
    sales = load_sales(name2canon)
    tot_sales = sum(sales.values())
    print(f"산업용 판매 2024: {tot_sales / 1e9:,.1f} TWh · 판매 단위 {len(sales)}")

    # 패스: 공장 건물 (단위, x5186, y5186, 연면적) 수집
    acc = {"units": [], "xs": [], "ys": [], "areas": [],
           "n_fac": 0, "n_nomap": 0, "n_noarea": 0}
    n_bld = 0
    for sd in SIDOS:
        zf = zipfile.ZipFile(os.path.join(BLD_DIR, f"AL_D010_{sd}_20241204.zip"))
        # zip 안에 shapefile이 여러 파트(…(2).dbf 등)로 나뉘어 있다 — 전 파트 순회
        dbfs = sorted(i.filename for i in zf.infolist()
                      if i.filename.lower().endswith(".dbf"))
        n_sd = sum(read_part(zf, d, cmap, acc) for d in dbfs)
        n_bld += n_sd
        print(f"  {sd}: 파트 {len(dbfs)} · 건물 {n_sd:,} · "
              f"누적 공장(면적>0) {len(acc['areas']):,}", flush=True)
    units, xs, ys, areas = acc["units"], acc["xs"], acc["ys"], acc["areas"]
    n_fac, n_nomap, n_noarea = acc["n_fac"], acc["n_nomap"], acc["n_noarea"]

    print(f"건물 {n_bld:,} · 공장 {n_fac:,} (면적 0/결측 {n_noarea:,}, "
          f"시군구 사전 미매칭 {n_nomap:,})")
    assert n_nomap == 0, "시군구 코드 사전 미매칭 발생 — 임의 매핑 금지, 중단"

    X, Y = T.transform(np.array(xs), np.array(ys))
    gx = np.floor(X / GRID).astype(np.int64)
    gy = np.floor(Y / GRID).astype(np.int64)
    areas = np.array(areas)

    area_u = defaultdict(float)
    for u, a in zip(units, areas):
        area_u[u] += a

    cell = defaultdict(float)
    alloc_u = defaultdict(float)
    for u, a, cx_, cy_ in zip(units, areas, gx, gy):
        s = sales.get(u)
        if not s:
            continue
        v = s * a / area_u[u]
        cell[(int(cx_), int(cy_))] += v
        alloc_u[u] += v

    unalloc = {u: s for u, s in sales.items() if s > 0 and area_u.get(u, 0) <= 0}
    max_err = max((abs(alloc_u[u] - sales[u]) / sales[u]
                   for u in alloc_u if sales[u] > 0), default=0.0)
    assert max_err < 1e-9, f"단위별 배분 합 불일치 {max_err}"
    tot_alloc = sum(cell.values())
    cap_alloc = sum(v for u, v in alloc_u.items()
                    if label[u][0].startswith(CAPITAL))
    print(f"배분 {tot_alloc / 1e9:,.1f} TWh ({100 * tot_alloc / tot_sales:.2f}%) · "
          f"미배분 단위 {len(unalloc)} ({sum(unalloc.values()) / 1e9:.2f} TWh) · "
          f"점유 셀 {len(cell):,}")
    print(f"산업용 수도권 비중: {100 * cap_alloc / tot_alloc:.1f}%")

    os.makedirs(PUB, exist_ok=True)
    p1 = os.path.join(PUB, "s1_grid_industrial_2024.csv.gz")
    with gzip.open(p1, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["gx", "gy", "kWh_ind"])
        for (a, b), v in cell.items():
            w.writerow([a, b, f"{v:.1f}"])

    vals = np.sort(np.array(list(cell.values())))[::-1]
    p2 = os.path.join(PUB, "s1_industrial_alloc_summary.csv")
    with open(p2, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerows([
            ("산업용_판매_TWh", f"{tot_sales / 1e9:.2f}"),
            ("배분_TWh", f"{tot_alloc / 1e9:.2f}"),
            ("배분률_pct", f"{100 * tot_alloc / tot_sales:.2f}"),
            ("미배분_단위수", len(unalloc)),
            ("미배분_TWh", f"{sum(unalloc.values()) / 1e9:.3f}"),
            ("공장건물_면적보유", len(areas)),
            ("점유셀", len(cell)),
            ("상위1pct셀_비중_pct",
             f"{100 * vals[:max(1, len(vals) // 100)].sum() / vals.sum():.1f}"),
            ("산업용_수도권비중_pct", f"{100 * cap_alloc / tot_alloc:.1f}"),
        ])
        for u, s in sorted(unalloc.items(), key=lambda kv: -kv[1]):
            w.writerow([f"미배분:{label[u][0]} {label[u][1]}", f"{s / 1e9:.3f}"])
    print("저장:", p1, p2, sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
