# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 제로샷 탐지 vs OSM 대조 — 정밀도·재현율과 OSM 결측 후보.

- 입력: out/internal/detections_dedup.csv (Colab 탐지, 5179 좌표)
        out/internal/s3_osm_groundtruth.csv (OSM 철탑·변전소, 대조 기준)
- 권역 배정: 파일럿 시군구 경계 내부의 탐지만 평가(장면이 경계 밖까지
  덮으므로, 경계 밖 탐지는 기준이 없어 공정하게 제외).
- 매칭: 점수 내림차순 greedy — 탐지 하나당 반경 내 미매칭 OSM 최근접 1개
  (철탑 50m, 변전소 150m — georef RMS≤35m + OSM 좌표 오차 고려).
- 점수 문턱 스윕(0.30~0.60)으로 정밀도/재현율 곡선 산출.
- 출력: stdout 요약표(건수만) + out/internal/s3_match_osm.csv(OSM별 매칭 여부)
        + out/internal/s3_new_candidates.csv(고점수 비매칭 탐지 = OSM 결측 후보)
"""
import csv
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s3_osm_groundtruth import (SHP, TARGETS, read_dbf_names, read_shp_polys,
                                rings_to_edges, points_in_edges)

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INT = os.path.join(REPO, "out", "internal")
RADIUS = {"tower": 50.0, "substation": 150.0}
SWEEP = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
CAND_MIN = 0.45   # OSM 결측 후보로 보고할 최소 점수


def main():
    dets = [r for r in csv.DictReader(open(os.path.join(INT, "detections_dedup.csv"),
                                           encoding="utf-8"))
            if r["cls"] in RADIUS]
    with open(os.path.join(INT, "s3_osm_groundtruth.csv"), encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.startswith("#")]  # ODbL 고지 주석 제거
    osm = list(csv.DictReader(lines))

    # 권역 배정 (경계 내부 탐지만)
    recs = read_dbf_names(SHP + ".dbf")
    polys = read_shp_polys(SHP + ".shp")
    name_f = next(k for k in recs[0] if k.endswith("_NM"))
    edges = {r[name_f]: rings_to_edges(p) for r, p in zip(recs, polys)
             if r[name_f] in TARGETS}
    X = np.array([float(r["x_5179"]) for r in dets])
    Y = np.array([float(r["y_5179"]) for r in dets])
    region = np.array([""] * len(dets), dtype=object)
    for nm, e in edges.items():
        x1, y1, x2, y2 = e
        bb = ((X >= min(x1.min(), x2.min())) & (X <= max(x1.max(), x2.max())) &
              (Y >= min(y1.min(), y2.min())) & (Y <= max(y1.max(), y2.max())))
        idx = np.flatnonzero(bb & (region == ""))
        if len(idx):
            hit = points_in_edges(X[idx], Y[idx], e)
            region[idx[hit]] = nm
    for r, nm in zip(dets, region):
        r["region"] = nm
    dets = [r for r in dets if r["region"]]
    print(f"경계 내부 탐지 {len(dets)}건 (철탑 "
          f"{sum(1 for r in dets if r['cls']=='tower')} · 변전소 "
          f"{sum(1 for r in dets if r['cls']=='substation')}) vs OSM {len(osm)}건")

    # 문턱 스윕
    print(f"\n{'문턱':>5} {'클래스':<11} {'탐지':>6} {'매칭':>5} "
          f"{'정밀도':>7} {'재현율':>7}")
    best = {}
    for th in SWEEP:
        for cls in ("tower", "substation"):
            D = sorted((r for r in dets if r["cls"] == cls and float(r["score"]) >= th),
                       key=lambda r: -float(r["score"]))
            G = [g for g in osm if g["class"] == cls]
            used = set()
            matched_det = []
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
                    matched_det.append(r)
            p = len(matched_det) / len(D) if D else float("nan")
            rec = len(used) / len(G) if G else float("nan")
            print(f"{th:>5.2f} {cls:<11} {len(D):>6} {len(used):>5} "
                  f"{100*p:>6.1f}% {100*rec:>6.1f}%")
            best[(th, cls)] = (used, D)
        print()

    # 기준 문턱(0.30)에서 OSM별 매칭 여부 + 고점수 비매칭(결측 후보) 저장
    used30_t, D30_t = best[(0.30, "tower")]
    used30_s, D30_s = best[(0.30, "substation")]
    with open(os.path.join(INT, "s3_match_osm.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["class", "sigungu", "x_5179", "y_5179", "osm_fid", "detected"])
        for cls, used in (("tower", used30_t), ("substation", used30_s)):
            G = [g for g in osm if g["class"] == cls]
            for j, g in enumerate(G):
                w.writerow([cls, g["sigungu"], g["x_5179"], g["y_5179"],
                            g["osm_fid"], int(j in used)])

    # 결측 후보: 문턱 CAND_MIN 이상인데 OSM 반경 내 어떤 기준과도 안 붙는 탐지
    cand = []
    for cls in ("tower", "substation"):
        G = [g for g in osm if g["class"] == cls]
        for r in (r for r in dets if r["cls"] == cls and float(r["score"]) >= CAND_MIN):
            x, y = float(r["x_5179"]), float(r["y_5179"])
            if all(math.hypot(float(g["x_5179"]) - x, float(g["y_5179"]) - y)
                   >= RADIUS[cls] for g in G):
                cand.append(r)
    with open(os.path.join(INT, "s3_new_candidates.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(cand[0].keys()) if cand else
                           ["tile", "scene", "cls", "score"])
        w.writeheader()
        w.writerows(sorted(cand, key=lambda r: -float(r["score"])))
    from collections import Counter
    cc = Counter((r["region"], r["cls"]) for r in cand)
    print(f"OSM 결측 후보(점수≥{CAND_MIN}): {len(cand)}건")
    for (nm, cls), n in sorted(cc.items()):
        print(f"  {nm} {cls}: {n}")
    print("저장: out/internal/s3_match_osm.csv, s3_new_candidates.csv (비공개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
