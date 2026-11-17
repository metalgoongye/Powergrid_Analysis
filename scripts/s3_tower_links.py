# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 탐지 철탑 연결선(보기용) — ArcGIS 확인용 shapefile(선).

GeoAI는 철탑 점만 탐지한다. 이 스크립트는 그 점들을 거리만으로 이어 본 것이며,
실제 전선의 연결 순서가 아니다. 지도 확인용이고 분석에는 쓰지 않는다.

규칙   점수 TH(0.20) 이상 탐지 철탑을 최소신장나무(전체 길이가 가장 짧게, 고리 없이)로
       잇고, MAX_LEN(500m)을 넘는 선분은 버린다.
속성   LEN_M   선분 길이(m)
       ON_OSM  선분 중점이 OSM 송전선로(KOR.gpkg power_line) R_LINE(30m) 이내면 1, 아니면 0
입력   out/internal/detections25_dedup.csv, KOR.gpkg power_line(ODbL)
출력   Z:\\2026_WP_out\\internal\\s3_towers_shp\\s3_geoai_tower_links.shp
       (+ .shx·.dbf·.prj·.cpg·_meta.txt) — 설비 좌표, 비공개. ON_OSM은 OSM(ODbL) 파생.

사용법:  python scripts/s3_tower_links.py
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s1_grid_to_shp import EPSG, file_header
from s3_missing_towers import GPKG, INT, R_LINE, TH, T_4326_5179, T_5179_4326, \
    gpkg_lines, seg_dist
from s3_towers_to_shp import ODBL, OUT_DIR, write_table

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MAX_LEN = 500.0  # m — 이보다 긴 선분은 버림
FIELDS = [("LEN_M", "N", 8, 1), ("ON_OSM", "N", 1, 0)]

# shp 레코드(선, 부분 1개·점 2개) — 머리 8바이트 + 내용 80바이트
REC = np.dtype([("recno", ">i4"), ("clen", ">i4"), ("stype", "<i4"), ("box", "<f8", 4),
                ("nparts", "<i4"), ("npts", "<i4"), ("part0", "<i4"), ("pts", "<f8", (2, 2))])
IDX = np.dtype([("off", ">i4"), ("clen", ">i4")])
assert REC.itemsize == 88


def mst(x: np.ndarray, y: np.ndarray) -> list[tuple[int, int, float]]:
    """최소신장나무(Prim). 반환: (점 i, 점 j, 길이) 목록."""
    n = len(x)
    done = np.zeros(n, dtype=bool)
    done[0] = True
    best = np.hypot(x - x[0], y - y[0])
    parent = np.zeros(n, dtype=int)
    edges = []
    for _ in range(n - 1):
        j = int(np.where(done, np.inf, best).argmin())
        edges.append((int(parent[j]), j, float(best[j])))
        done[j] = True
        d = np.hypot(x - x[j], y - y[j])
        closer = d < best
        best[closer] = d[closer]
        parent[closer] = j
    return edges


def osm_segments(x: np.ndarray, y: np.ndarray):
    """탐지 범위(+3km) 안의 OSM 선로 선분 (ax, ay, bx, by) — EPSG:5179."""
    lo0, la0 = T_5179_4326.transform(x.min() - 3000, y.min() - 3000)
    lo1, la1 = T_5179_4326.transform(x.max() + 3000, y.max() + 3000)
    segs = []
    db = sqlite3.connect(GPKG)
    for (blob,) in db.execute("SELECT geometry FROM power_line"):
        for a in gpkg_lines(blob):
            if a[:, 0].max() < lo0 or a[:, 0].min() > lo1 or \
                    a[:, 1].max() < la0 or a[:, 1].min() > la1:
                continue
            sx, sy = T_4326_5179.transform(a[:, 0], a[:, 1])
            segs.append((sx[:-1], sy[:-1], sx[1:], sy[1:]))
    db.close()
    return tuple(np.concatenate([s[i] for s in segs]) for i in range(4))


def main() -> int:
    with open(os.path.join(INT, "detections25_dedup.csv"), encoding="utf-8") as f:
        dets = [r for r in csv.DictReader(f) if r["cls"] == "tower" and float(r["score"]) >= TH]
    x = np.array([float(r["x_5179"]) for r in dets])
    y = np.array([float(r["y_5179"]) for r in dets])

    tree = mst(x, y)
    edges = [(i, j, d) for i, j, d in tree if d <= MAX_LEN]
    n = len(edges)
    ax, ay, bx, by = osm_segments(x, y)
    on = [int(seg_dist((x[i] + x[j]) / 2, (y[i] + y[j]) / 2, ax, ay, bx, by).min() <= R_LINE)
          for i, j, _d in edges]

    os.makedirs(OUT_DIR, exist_ok=True)
    stem = os.path.join(OUT_DIR, "s3_geoai_tower_links")
    ii = np.array([e[0] for e in edges])
    jj = np.array([e[1] for e in edges])
    rec = np.zeros(n, dtype=REC)
    rec["recno"] = np.arange(1, n + 1)
    rec["clen"] = (REC.itemsize - 8) // 2
    rec["stype"] = 3
    rec["box"] = np.column_stack([np.minimum(x[ii], x[jj]), np.minimum(y[ii], y[jj]),
                                  np.maximum(x[ii], x[jj]), np.maximum(y[ii], y[jj])])
    rec["nparts"] = 1
    rec["npts"] = 2
    rec["pts"][:, 0] = np.column_stack([x[ii], y[ii]])
    rec["pts"][:, 1] = np.column_stack([x[jj], y[jj]])
    bbox = (rec["box"][:, 0].min(), rec["box"][:, 1].min(),
            rec["box"][:, 2].max(), rec["box"][:, 3].max())
    with open(stem + ".shp", "wb") as f:
        f.write(file_header(100 + REC.itemsize * n, bbox, stype=3))
        rec.tofile(f)
    idx = np.zeros(n, dtype=IDX)
    idx["off"] = (100 + REC.itemsize * np.arange(n)) // 2
    idx["clen"] = (REC.itemsize - 8) // 2
    with open(stem + ".shx", "wb") as f:
        f.write(file_header(100 + IDX.itemsize * n, bbox, stype=3))
        idx.tofile(f)
    write_table(stem, FIELDS, [[f"{d:.1f}", str(o)] for (_i, _j, d), o in zip(edges, on)])

    # 검산: 다시 읽어 선분 길이·끝점을 대조
    back = np.fromfile(stem + ".shp", dtype=REC, offset=100)
    assert len(back) == n and os.path.getsize(stem + ".shp") == 100 + REC.itemsize * n
    p = back["pts"]
    length = np.hypot(p[:, 1, 0] - p[:, 0, 0], p[:, 1, 1] - p[:, 0, 1])
    assert np.allclose(length, [e[2] for e in edges]) and length.max() <= MAX_LEN
    pts = set(zip(x.tolist(), y.tolist()))
    assert all((a, b) in pts for a, b in p.reshape(-1, 2).tolist()), "끝점이 탐지점이 아님"

    n_on = sum(on)
    meta = (
        f"탐지 철탑 연결선(보기용) — 선 shapefile, EPSG:{EPSG}, 설비 좌표 포함(비공개)\n"
        f"생성 스크립트 s3_tower_links.py · 입력 detections25_dedup.csv\n"
        f"실제 전선의 연결 순서가 아님. 거리만으로 이은 것이며 분석에 쓰지 않음.\n"
        f"규칙: 점수 {TH} 이상 탐지 철탑 {len(dets):,}점을 최소신장나무로 잇고 "
        f"{MAX_LEN:.0f}m 초과 선분 제외 → 선분 {n:,}개 (제외 {len(tree) - n:,}개)\n"
        f"LEN_M = 선분 길이(m) · ON_OSM = 선분 중점이 OSM 송전선로 {R_LINE:.0f}m 이내(1) / 아님(0)\n"
        f"ON_OSM = 1인 선분 {n_on:,}개 ({100 * n_on / n:.1f}%)\n"
        f"{ODBL}\n"
    )
    with open(stem + "_meta.txt", "w", encoding="utf-8") as f:
        f.write(meta)
    print(meta)
    print(f"저장: {stem}.shp · 검산 통과(건수·길이·끝점)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
