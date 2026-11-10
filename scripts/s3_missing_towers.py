# -*- coding: utf-8 -*-
r"""[3단계 GeoAI] OSM 결측 철탑 후보 — 선로 선형성 필터와 보정판 생성.

본평가(s3_eval25_detect → s3_eval25_vs_osm)에서 고신뢰 탐지(문턱 0.20)인데
OSM 철탑과 무대응인 점들을 OSM 결측 후보로 추리고, 송전선로 축과의 선형성
(최근접 OSM power_line 선분까지의 거리)으로 자동 선별한다. 선별분은 크롭
육안 확정(--crops)을 거쳐 'OSM+GeoAI 보정판' 철탑 집합에 더한다.

판정 규칙: d_line ≤ 30m → 선로정합(철탑은 선로 축 위에 서 있어야 함).
30m 초과는 비정합(오탐 또는 OSM 결측 '선로'의 가능성 — 후보 간 군집만 기록).

출력:
  out/internal/s3_missing_towers.csv      후보별 판정 (좌표 — 비공개)
  out/internal/s3_towers_corrected.csv    보정판 = OSM 철탑 + 정합 후보 (비공개)
  out/public/s3_missing_towers_summary.csv 집계만
  --crops 시 Z:\2026_WP_out\internal\missing_crops\ 에 후보 크롭·몽타주 저장
"""
import csv
import math
import os
import struct
import sys

import numpy as np
from pyproj import Transformer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s3_osm_groundtruth import SHP, read_dbf_names, read_shp_polys, \
    rings_to_edges, points_in_edges

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
GPKG = os.path.join(DATA_ROOT, "powergrid_spatial_data", "OSM_powergrid", "KOR.gpkg")
INT = os.path.join(REPO, "out", "internal")
PUB = os.path.join(REPO, "out", "public")
CROPS = r"Z:\2026_WP_out\internal\missing_crops"
AERIAL = r"Z:\powergrid_spatial_data\위성정사영상\위성정사영상_분석대상지역\261026_25cm"
GEOREF = os.path.join(REPO, "out", "public", "s3_sheet_georef.csv")

EVAL = {"서천군", "보령시", "은평구"}
TH = 0.20          # 고신뢰 문턱 (본평가 운용점)
R_MATCH = 50.0     # OSM 철탑 매칭 반경 (s3_eval25_vs_osm와 동일)
R_LINE = 30.0      # 선로 정합 문턱
ODBL = "# 원천: OpenInfraMap/OSM KOR.gpkg (ODbL) 파생 — 배포 시 고지·동일조건"

T_4326_5179 = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
T_5186_4326 = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
T_5179_4326 = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)


def gpkg_lines(blob):
    """GPKG LineString/MultiLineString → [np.ndarray(N,2), ...] (lon,lat)."""
    flags = blob[3]
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 7]
    off = 8 + env_len
    bo = "<" if blob[off] else ">"
    gtype = struct.unpack_from(bo + "I", blob, off + 1)[0] & 0xFF
    if gtype == 2:  # LineString
        npts = struct.unpack_from(bo + "I", blob, off + 5)[0]
        pts = struct.unpack_from(bo + f"{2 * npts}d", blob, off + 9)
        return [np.array(pts).reshape(-1, 2)]
    if gtype == 5:  # MultiLineString
        nline = struct.unpack_from(bo + "I", blob, off + 5)[0]
        out, p = [], off + 9
        for _ in range(nline):
            bo2 = "<" if blob[p] else ">"
            npts = struct.unpack_from(bo2 + "I", blob, p + 5)[0]
            pts = struct.unpack_from(bo2 + f"{2 * npts}d", blob, p + 9)
            out.append(np.array(pts).reshape(-1, 2))
            p += 9 + 16 * npts
        return out
    return []


def seg_dist(px, py, ax, ay, bx, by):
    """점 (px,py)에서 선분들 (a→b)까지 거리 배열."""
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = np.clip(((px - ax) * dx + (py - ay) * dy) / np.where(L2 == 0, 1, L2), 0, 1)
    qx, qy = ax + t * dx, ay + t * dy
    return np.hypot(px - qx, py - qy)


def main():
    do_crops = "--crops" in sys.argv

    # 1. 고신뢰 철탑 탐지 (경계 내부)
    dets = [r for r in csv.DictReader(open(os.path.join(INT, "detections25_dedup.csv"),
                                           encoding="utf-8"))
            if r["cls"] == "tower" and float(r["score"]) >= TH]
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

    # 2. OSM 철탑 매칭 (greedy, s3_eval25_vs_osm와 동일) → 무대응 = 결측 후보
    with open(os.path.join(INT, "s3_osm_groundtruth.csv"), encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    osm = [g for g in csv.DictReader(lines)
           if g["sigungu"] in EVAL and g["class"] == "tower"]
    gx = np.array([float(g["x_5179"]) for g in osm])
    gy = np.array([float(g["y_5179"]) for g in osm])
    used = set()
    cands = []
    for r in sorted(dets, key=lambda r: -float(r["score"])):
        x, y = float(r["x_5179"]), float(r["y_5179"])
        d = np.hypot(gx - x, gy - y)
        d[list(used)] = np.inf
        j = int(d.argmin())
        if d[j] < R_MATCH:
            used.add(j)
        else:
            cands.append(dict(r, x=x, y=y, d_tower=float(np.hypot(gx - x, gy - y).min())))
    print(f"고신뢰 탐지 {len(dets)}건 중 매칭 {len(used)} · 결측 후보 {len(cands)}건")

    # 3. 평가 권역 주변 선로 선분 수집 (bbox + 2km)
    bx0, bx1 = X.min() - 2000, X.max() + 2000
    by0, by1 = Y.min() - 2000, Y.max() + 2000
    lo0, la0 = T_5179_4326.transform(bx0, by0)
    lo1, la1 = T_5179_4326.transform(bx1, by1)
    import sqlite3
    db = sqlite3.connect(GPKG)
    segs = []
    for (blob,) in db.execute("SELECT geometry FROM power_line"):
        for arr in gpkg_lines(blob):
            if arr[:, 0].max() < lo0 or arr[:, 0].min() > lo1 or \
                    arr[:, 1].max() < la0 or arr[:, 1].min() > la1:
                continue
            sx, sy = T_4326_5179.transform(arr[:, 0], arr[:, 1])
            segs.append((sx[:-1], sy[:-1], sx[1:], sy[1:]))
    db.close()
    ax = np.concatenate([s[0] for s in segs])
    ay = np.concatenate([s[1] for s in segs])
    bx = np.concatenate([s[2] for s in segs])
    by = np.concatenate([s[3] for s in segs])
    print(f"권역 주변 선로 선분 {len(ax)}개")

    # 4. 후보별 선로 거리 → 판정
    for c in cands:
        c["d_line"] = float(seg_dist(c["x"], c["y"], ax, ay, bx, by).min())
        c["verdict"] = "선로정합" if c["d_line"] <= R_LINE else "비정합"
    n_ok = sum(1 for c in cands if c["verdict"] == "선로정합")

    # 비정합 후보 간 군집(600m 내 2개 이상) — OSM 결측 '선로' 단서만 기록
    bad = [c for c in cands if c["verdict"] == "비정합"]
    for c in bad:
        c["n_near_cand"] = sum(1 for o in bad if o is not c and
                               math.hypot(o["x"] - c["x"], o["y"] - c["y"]) < 600)

    # 5. 저장 — 후보 상세(내부), 보정판(내부), 집계(공개)
    os.makedirs(INT, exist_ok=True)
    os.makedirs(PUB, exist_ok=True)
    p1 = os.path.join(INT, "s3_missing_towers.csv")
    with open(p1, "w", newline="", encoding="utf-8-sig") as f:
        f.write(ODBL + "\n")
        w = csv.writer(f)
        w.writerow(["id", "region", "sheet", "score", "x_5179", "y_5179",
                    "d_line_m", "d_tower_m", "판정", "비정합군집수"])
        for i, c in enumerate(sorted(cands, key=lambda c: c["d_line"])):
            c["id"] = i
            w.writerow([i, c["region"], c["sheet"], c["score"],
                        f"{c['x']:.1f}", f"{c['y']:.1f}", f"{c['d_line']:.1f}",
                        f"{c['d_tower']:.1f}", c["verdict"],
                        c.get("n_near_cand", "")])

    p2 = os.path.join(INT, "s3_towers_corrected.csv")
    with open(p2, "w", newline="", encoding="utf-8-sig") as f:
        f.write(ODBL + "\n")
        w = csv.writer(f)
        w.writerow(["source", "sigungu", "x_5179", "y_5179", "score"])
        for g in osm:
            w.writerow(["osm", g["sigungu"], g["x_5179"], g["y_5179"], ""])
        for c in cands:
            if c["verdict"] == "선로정합":
                w.writerow(["geoai", c["region"], f"{c['x']:.1f}",
                            f"{c['y']:.1f}", c["score"]])

    n_corr = len(osm) + n_ok
    p3 = os.path.join(PUB, "s3_missing_towers_summary.csv")
    with open(p3, "w", newline="", encoding="utf-8-sig") as f:
        f.write(ODBL + "\n")
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerows([
            ("고신뢰탐지_문턱", TH), ("고신뢰탐지_경계내부", len(dets)),
            ("OSM매칭", len(used)), ("결측후보", len(cands)),
            ("선로정합_30m", n_ok), ("비정합", len(cands) - n_ok),
            ("OSM철탑_평가권역", len(osm)),
            ("보정판_철탑수", n_corr),
            ("보정_증가율_pct", f"{100.0 * n_ok / len(osm):.1f}"),
        ])
    for c in sorted(cands, key=lambda c: c["d_line"])[:40]:
        print(f"  #{c['id']:>2} {c['region']} score={c['score']} "
              f"d_line={c['d_line']:.1f} d_tower={c['d_tower']:.0f} {c['verdict']}"
              f"{' 군집' + str(c['n_near_cand']) if c.get('n_near_cand') else ''}")
    print(f"선로정합 {n_ok} / 비정합 {len(cands) - n_ok} → 보정판 {n_corr}기 "
          f"(+{100.0 * n_ok / len(osm):.1f}%)")
    print("저장:", p1, p2, p3, sep="\n  ")

    # 6. 크롭 (육안 확정용) — 후보별 256px(64m) + 8열 몽타주
    if do_crops:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        os.makedirs(CROPS, exist_ok=True)
        sheets = {}
        with open(GEOREF, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["flag"]:
                    continue
                cur = sheets.get(r["sheet"])
                if cur is None or (r["year"] == "2024" and cur["year"] != "2024"):
                    sheets[r["sheet"]] = r
        half = 128
        tiles = []
        for c in sorted(cands, key=lambda c: c["id"]):
            g = sheets[c["sheet"]]
            x0, y0 = float(g["x0"]), float(g["y0"])
            lon, lat = T_5179_4326.transform(c["x"], c["y"])
            T_4326_5186 = Transformer.from_crs("EPSG:4326", "EPSG:5186",
                                               always_xy=True)
            x6, y6 = T_4326_5186.transform(lon, lat)
            px, py = (x6 - x0) / 0.25, (y0 - y6) / 0.25
            img = Image.open(os.path.join(AERIAL, g["rel_path"])).convert("RGB")
            box = (int(px) - half, int(py) - half, int(px) + half, int(py) + half)
            crop = img.crop(box)
            img.close()
            crop.save(os.path.join(CROPS, f"cand_{c['id']:02d}_{c['verdict']}.png"))
            tiles.append((c["id"], c["verdict"], crop))
        cols = 8
        rows = (len(tiles) + cols - 1) // cols
        from PIL import ImageDraw
        mont = Image.new("RGB", (cols * 256, rows * 256), (30, 30, 30))
        dr = ImageDraw.Draw(mont)
        for i, (cid, v, crop) in enumerate(tiles):
            ox, oy = (i % cols) * 256, (i // cols) * 256
            mont.paste(crop, (ox, oy))
            dr.rectangle([ox + 112, oy + 112, ox + 144, oy + 144],
                         outline=(255, 60, 60), width=2)
            dr.text((ox + 6, oy + 6), f"#{cid} {v}", fill=(255, 255, 0))
        mp = os.path.join(CROPS, "montage_candidates.png")
        mont.save(mp)
        print("크롭·몽타주:", mp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
