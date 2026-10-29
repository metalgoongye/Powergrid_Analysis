# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 항공 25cm 본평가 ② — 탐지 vs OSM 점-거리 대조.

입력: out/internal/detections25_dedup.csv (s3_eval25_detect.py, 5179 좌표)
      out/internal/s3_osm_groundtruth.csv (OSM 철탑·변전소)
평가 권역(서천·보령·은평)의 OSM 기준만 쓰고, 탐지도 권역 경계 내부만 평가.
매칭: 점수 내림차순 greedy — 탐지 하나당 반경 내 미매칭 OSM 최근접 1개
      (철탑 50m, 변전소 150m — s3_detect_vs_osm과 동일 규약)
출력: stdout 요약표 + out/internal/s3_eval25_match.csv (OSM별 매칭 여부)
"""
import csv
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s3_osm_groundtruth import SHP, read_dbf_names, read_shp_polys, \
    rings_to_edges, points_in_edges

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INT = os.path.join(REPO, "out", "internal")
EVAL = {"서천군", "보령시", "은평구"}
RADIUS = {"tower": 50.0, "substation": 150.0}
SWEEP = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70]


def main():
    dets = [r for r in csv.DictReader(open(os.path.join(INT, "detections25_dedup.csv"),
                                           encoding="utf-8"))
            if r["cls"] in RADIUS]
    with open(os.path.join(INT, "s3_osm_groundtruth.csv"), encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    osm = [g for g in csv.DictReader(lines) if g["sigungu"] in EVAL]

    # 경계 내부 탐지만 (도엽이 경계 밖까지 덮으므로 기준 없는 탐지는 제외)
    recs = read_dbf_names(SHP + ".dbf")
    polys = read_shp_polys(SHP + ".shp")
    name_f = next(k for k in recs[0] if k.endswith("_NM"))
    edges = {r[name_f]: rings_to_edges(p) for r, p in zip(recs, polys)
             if r[name_f] in EVAL}
    X = np.array([float(r["x_5179"]) for r in dets])
    Y = np.array([float(r["y_5179"]) for r in dets])
    inside = np.zeros(len(dets), dtype=bool)
    for e in edges.values():
        todo = np.flatnonzero(~inside)
        if len(todo):
            inside[todo[points_in_edges(X[todo], Y[todo], e)]] = True
    dets = [r for r, k in zip(dets, inside) if k]
    print(f"경계 내부 탐지 {len(dets)}건 (철탑 "
          f"{sum(1 for r in dets if r['cls']=='tower')} · 변전소 "
          f"{sum(1 for r in dets if r['cls']=='substation')}) vs "
          f"평가 권역 OSM {len(osm)}건 (철탑 "
          f"{sum(1 for g in osm if g['class']=='tower')} · 변전소 "
          f"{sum(1 for g in osm if g['class']=='substation')})")

    print(f"\n{'문턱':>5} {'클래스':<11} {'탐지':>6} {'매칭':>5} "
          f"{'정밀도':>7} {'재현율':>7}")
    best = {}
    for th in SWEEP:
        for cls in ("tower", "substation"):
            D = sorted((r for r in dets
                        if r["cls"] == cls and float(r["score"]) >= th),
                       key=lambda r: -float(r["score"]))
            G = [g for g in osm if g["class"] == cls]
            used = set()
            n_match = 0
            for r in D:
                x, y = float(r["x_5179"]), float(r["y_5179"])
                near, nd = None, RADIUS[cls]
                for j, g in enumerate(G):
                    if j in used:
                        continue
                    dd = math.hypot(float(g["x_5179"]) - x, float(g["y_5179"]) - y)
                    if dd < nd:
                        near, nd = j, dd
                if near is not None:
                    used.add(near)
                    n_match += 1
            p = n_match / len(D) if D else float("nan")
            rec = len(used) / len(G) if G else float("nan")
            print(f"{th:>5.2f} {cls:<11} {len(D):>6} {len(used):>5} "
                  f"{100*p:>6.1f}% {100*rec:>6.1f}%")
            best[(th, cls)] = used
        print()

    # 문턱 0.30에서 OSM별 매칭 여부 저장 (권역별 분해 확인용)
    with open(os.path.join(INT, "s3_eval25_match.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["class", "sigungu", "x_5179", "y_5179", "osm_fid", "detected"])
        for cls in ("tower", "substation"):
            used = best[(0.30, cls)]
            G = [g for g in osm if g["class"] == cls]
            for j, g in enumerate(G):
                w.writerow([cls, g["sigungu"], g["x_5179"], g["y_5179"],
                            g["osm_fid"], int(j in used)])
    print("저장: out/internal/s3_eval25_match.csv (비공개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
