"""[3단계 GeoAI] 항공 25cm 본평가 ③ — 매칭된 철탑의 탐지-OSM 거리 분포.

s3_eval25_vs_osm.py와 같은 절차(경계 내부 탐지, 점수 내림차순 greedy, 50m)로
문턱 0.20의 매칭을 재현하고, 매칭 건의 거리 분포를 집계한다.
입력: out/internal/detections25_dedup.csv, out/internal/s3_osm_groundtruth.csv
출력: stdout 집계값만(좌표 비출력).
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
TH, R = 0.20, 50.0


def main():
    dets = [r for r in csv.DictReader(open(os.path.join(INT, "detections25_dedup.csv"),
                                           encoding="utf-8"))
            if r["cls"] == "tower"]
    with open(os.path.join(INT, "s3_osm_groundtruth.csv"), encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    osm = [g for g in csv.DictReader(lines)
           if g["sigungu"] in EVAL and g["class"] == "tower"]

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

    D = sorted((r for r in dets if float(r["score"]) >= TH),
               key=lambda r: -float(r["score"]))
    used, dist = set(), []
    for r in D:
        x, y = float(r["x_5179"]), float(r["y_5179"])
        near, nd = None, R
        for j, g in enumerate(osm):
            if j in used:
                continue
            dd = math.hypot(float(g["x_5179"]) - x, float(g["y_5179"]) - y)
            if dd < nd:
                near, nd = j, dd
        if near is not None:
            used.add(near)
            dist.append(nd)

    d = np.array(dist)
    print(f"OSM 철탑 {len(osm)}기 · 탐지(>= {TH}) {len(D)}건 · 매칭 {len(d)}건")
    for q in (5, 10, 25, 50, 75, 90, 95):
        print(f"  P{q:<2} {np.percentile(d, q):6.1f} m")
    print(f"  평균 {d.mean():.1f} m · 최대 {d.max():.1f} m")
    for lo, hi in ((0, 5), (5, 13), (13, 25), (25, 50)):
        n = int(((d >= lo) & (d < hi)).sum())
        print(f"  {lo:>2}~{hi:<2} m: {n:>3}건 ({100 * n / len(d):.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
