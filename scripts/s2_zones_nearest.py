# -*- coding: utf-8 -*-
"""2단계-1: 변전소 권역 배정 — 규칙 ① 최근접(티센).

KOR.gpkg에서 154kV 이상 변전소를 추출(폴리곤 중심+포인트, 근접 중복 제거)하고,
1단계 격자 수요지도의 각 셀을 가장 가까운 변전소에 배정한다(= 티센/보로노이).
권역별 수요(ⓐⓑⓒ 3벌)를 집계한다.

산출물
  out/internal/s2_cell_zone_nearest.csv.gz  셀→권역 (설비 위치 구조가 드러나므로 내부)
  out/public/s2_zone_demand_nearest.csv     권역별 집계 (명칭 수준 — 공개 가능)
사용법:  python scripts/s2_zones_nearest.py
"""
from __future__ import annotations

import csv
import gzip
import os
import sqlite3
import struct
import sys
from collections import defaultdict

import numpy as np
from pyproj import Transformer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
GPKG = os.path.join(DATA_ROOT, "powergrid_spatial_data", "36pJ2bwdQr", "KOR.gpkg")
GRID = os.path.join(REPO, "out", "public", "s1_grid_demand_2024.csv.gz")
OUT_PUB = os.path.join(REPO, "out", "public")
OUT_INT = os.path.join(REPO, "out", "internal")

VOLT_MIN = 154000.0
DEDUPE_M = 300.0
CELL = 100.0  # 격자 크기(m) — 셀 중심 = (gx+0.5)*100


def gpkg_geom_center(blob: bytes) -> tuple[float, float]:
    """GPKG 지오메트리 blob에서 중심 좌표(경위도)를 꺼낸다.
    헤더에 envelope가 있으면 그 중심, 없으면 WKB 포인트 좌표."""
    if blob[:2] != b"GP":
        raise ValueError("GPKG magic 아님")
    flags = blob[3]
    env_ind = (flags >> 1) & 0x07
    little = flags & 0x01
    off = 8
    if env_ind > 0:
        n = {1: 4, 2: 6, 3: 6, 4: 8}[env_ind]
        fmt = ("<" if little else ">") + f"{n}d"
        env = struct.unpack_from(fmt, blob, off)
        off += n * 8
        return (env[0] + env[1]) / 2, (env[2] + env[3]) / 2
    # envelope 없음 → WKB 직접 (포인트만 처리)
    wkb_little = blob[off]
    gtype = struct.unpack_from("<I" if wkb_little else ">I", blob, off + 1)[0]
    if gtype % 1000 == 1:  # Point
        x, y = struct.unpack_from("<2d" if wkb_little else ">2d", blob, off + 5)
        return x, y
    raise ValueError(f"envelope 없는 WKB type {gtype} — 미지원")


def load_substations() -> list[tuple[str, str, float, float]]:
    """(zone_id, 이름, lon, lat) — 154kV 이상, 근접 중복 제거."""
    db = sqlite3.connect(GPKG)
    subs = []
    for layer, tag in [("power_substation_polygon", "G"), ("power_substation_point", "P")]:
        for fid, name, blob in db.execute(
            f"SELECT fid, name, geometry FROM {layer} WHERE max_voltage >= ?", (VOLT_MIN,)
        ):
            lon, lat = gpkg_geom_center(blob)
            subs.append((f"{tag}{fid}", name or f"(무명 {tag}{fid})", lon, lat, tag))
    # 좌표 변환 후 근접 중복 제거(포인트가 폴리곤 300m 안이면 포인트 제거)
    tr = Transformer.from_crs(4326, 5179, always_xy=True)
    xs, ys = tr.transform([s[2] for s in subs], [s[3] for s in subs])
    xy = np.column_stack([xs, ys])
    keep = np.ones(len(subs), dtype=bool)
    poly_idx = [i for i, s in enumerate(subs) if s[4] == "G"]
    pxy = xy[poly_idx]
    for i, s in enumerate(subs):
        if s[4] != "P":
            continue
        d2 = ((pxy - xy[i]) ** 2).sum(axis=1)
        if len(d2) and d2.min() <= DEDUPE_M ** 2:
            keep[i] = False
    out = [(subs[i][0], subs[i][1], xy[i, 0], xy[i, 1])
           for i in range(len(subs)) if keep[i]]
    print(f"변전소: 추출 {len(subs)} → 중복 제거 후 {len(out)} "
          f"(포인트 중복 {int((~keep).sum())} 제거)")
    return out


def main() -> int:
    subs = load_substations()
    sx = np.array([s[2] for s in subs])
    sy = np.array([s[3] for s in subs])

    print("격자 적재…")
    gx, gy = [], []
    va, vb, vc = [], [], []
    with gzip.open(GRID, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            gx.append(int(row[0])); gy.append(int(row[1]))
            va.append(float(row[2])); vb.append(float(row[3])); vc.append(float(row[4]))
    gx = np.array(gx); gy = np.array(gy)
    va = np.array(va); vb = np.array(vb); vc = np.array(vc)
    cx = (gx + 0.5) * CELL
    cy = (gy + 0.5) * CELL
    print(f"  셀 {len(gx):,}, 변전소 {len(subs)}")

    print("최근접 배정(청크)…")
    assign = np.empty(len(gx), dtype=np.int32)
    CH = 20000
    for i in range(0, len(gx), CH):
        dx = cx[i:i + CH, None] - sx[None, :]
        dy = cy[i:i + CH, None] - sy[None, :]
        assign[i:i + CH] = np.argmin(dx * dx + dy * dy, axis=1)

    # 집계
    agg = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for k, a, b, c in zip(assign, va, vb, vc):
        g = agg[int(k)]
        g[0] += 1; g[1] += a; g[2] += b; g[3] += c

    os.makedirs(OUT_PUB, exist_ok=True)
    os.makedirs(OUT_INT, exist_ok=True)
    pub = os.path.join(OUT_PUB, "s2_zone_demand_nearest.csv")
    with open(pub, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["zone_id", "변전소명", "셀수", "GWh_raw", "GWh_adj", "GWh_excl"])
        for k in sorted(agg, key=lambda k: -agg[k][2]):
            zid, name = subs[k][0], subs[k][1]
            n, a, b, c = agg[k]
            w.writerow([zid, name, n, round(a / 1e6, 2), round(b / 1e6, 2), round(c / 1e6, 2)])

    intpath = os.path.join(OUT_INT, "s2_cell_zone_nearest.csv.gz")
    with gzip.open(intpath, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["gx", "gy", "zone_id"])
        for x, y, k in zip(gx, gy, assign):
            w.writerow([x, y, subs[int(k)][0]])

    used = len(agg)
    demands = sorted((agg[k][2] for k in agg), reverse=True)  # adj 기준 kWh
    tot = sum(demands)
    print(f"\n권역 수(수요 있는): {used} / 변전소 {len(subs)}")
    print(f"권역 수요(adj): 중위 {demands[used//2]/1e6:.1f} GWh · "
          f"최대 {demands[0]/1e6:.1f} GWh · 상위10 비중 {sum(demands[:10])/tot*100:.1f}%")
    top = sorted(agg, key=lambda k: -agg[k][2])[:5]
    for k in top:
        print(f"  상위: {subs[k][1]:<12} {agg[k][2]/1e6:>8.1f} GWh ({agg[k][0]:,}셀)")
    print(f"저장: {pub}\n      {intpath} (내부)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
