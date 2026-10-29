# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 항공 25cm 본평가 ① — 평가 권역 도엽 전수 추론.

2판 파인튜닝 가중치(best.pt, 카글 학습)를 평가 권역(서천·보령·은평) 도엽
전체에 슬라이딩 윈도(1024px, 보폭 896px)로 돌려 탐지점을 모은다.
- 도엽 선정: 도엽 bbox(georef)와 평가 시군구 폴리곤(EPSG:5179)의 교차
- 좌표 사슬: 타일 픽셀 → 도엽 아핀(5186) → 4326 → 5179 (크롭 생성과 동일)
- 중복 제거: 타일 겹침·도엽 여백 겹침에서 생기는 같은 설비의 중복 탐지를
  점수 내림차순 greedy로 반경 내 억제(철탑 15m, 변전소 60m)
- 출력: out/internal/detections25_dedup.csv (좌표 포함 — 비공개)

사용: python s3_eval25_detect.py [--dry]   # --dry = 도엽 수·타일 수만 추정
"""
import csv
import os
import sys

import numpy as np
from PIL import Image
from pyproj import Transformer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s3_osm_groundtruth import SHP, read_dbf_names, read_shp_polys, \
    rings_to_edges, points_in_edges

sys.stdout.reconfigure(encoding="utf-8")
Image.MAX_IMAGE_PIXELS = None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AERIAL = r"Z:\powergrid_spatial_data\위성정사영상\위성정사영상_분석대상지역\261026_25cm"
GEOREF = os.path.join(REPO, "out", "public", "s3_sheet_georef.csv")
WEIGHTS = r"Z:\2026_WP_out\internal\yolo25_out\best.pt"
OUT = os.path.join(REPO, "out", "internal", "detections25_dedup.csv")

EVAL = {"서천군", "보령시", "은평구"}
TILE, STRIDE = 1024, 896
CONF_MIN = 0.10
BATCH = 8
DEDUP_M = {"tower": 15.0, "substation": 60.0}
NAMES = {0: "tower", 1: "substation"}

T_5186_4326 = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
T_4326_5179 = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)


def load_georef():
    """도엽 → georef 행. 동일 도엽 복수 연도면 2024(기준년) 우선."""
    sheets = {}
    with open(GEOREF, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["flag"]:
                continue
            cur = sheets.get(r["sheet"])
            if cur is None or (r["year"] == "2024" and cur["year"] != "2024"):
                sheets[r["sheet"]] = r
    return sheets


def eval_sheets(sheets):
    """도엽 bbox(5179 근사)와 평가 시군구 폴리곤 교차 → {sheet: 시군구}."""
    recs = read_dbf_names(SHP + ".dbf")
    polys = read_shp_polys(SHP + ".shp")
    name_f = next(k for k in recs[0] if k.endswith("_NM"))
    edges = {r[name_f]: rings_to_edges(p) for r, p in zip(recs, polys)
             if r[name_f] in EVAL}
    verts = {}
    for (r, p) in zip(recs, polys):
        if r[name_f] in EVAL:
            vx = np.concatenate([np.asarray(ring)[:, 0] for ring in p])
            vy = np.concatenate([np.asarray(ring)[:, 1] for ring in p])
            verts[r[name_f]] = (vx, vy)

    out = {}
    for code, g in sheets.items():
        x0, y0 = float(g["x0"]), float(g["y0"])
        W, H = int(g["width"]), int(g["height"])
        xs6 = np.array([x0, x0 + W * 0.25, x0 + W * 0.25, x0, x0 + W * 0.125])
        ys6 = np.array([y0, y0, y0 - H * 0.25, y0 - H * 0.25, y0 - H * 0.125])
        lon, lat = T_5186_4326.transform(xs6, ys6)
        X, Y = T_4326_5179.transform(lon, lat)
        for nm, e in edges.items():
            if points_in_edges(X, Y, e).any():
                out[code] = nm
                break
        else:
            # 폴리곤 꼭짓점이 도엽 bbox 안에 드는 경우(도엽이 돌출부를 덮음)
            for nm, (vx, vy) in verts.items():
                if ((vx >= X.min()) & (vx <= X.max()) &
                        (vy >= Y.min()) & (vy <= Y.max())).any():
                    out[code] = nm
                    break
    return out


def tile_origins(W, H):
    xs = list(range(0, max(W - TILE, 0) + 1, STRIDE))
    if xs[-1] != W - TILE and W > TILE:
        xs.append(W - TILE)
    ys = list(range(0, max(H - TILE, 0) + 1, STRIDE))
    if ys[-1] != H - TILE and H > TILE:
        ys.append(H - TILE)
    return [(x, y) for y in ys for x in xs]


def dedup(dets):
    """점수 내림차순 greedy — 반경 내 같은 클래스 탐지 억제 (격자 해시)."""
    kept = []
    for cls in ("tower", "substation"):
        rad = DEDUP_M[cls]
        D = sorted((d for d in dets if d["cls"] == cls),
                   key=lambda d: -d["score"])
        grid = {}
        for d in D:
            gx, gy = int(d["x"] // rad), int(d["y"] // rad)
            ok = True
            for ix in (gx - 1, gx, gx + 1):
                for iy in (gy - 1, gy, gy + 1):
                    for k in grid.get((ix, iy), ()):
                        if (d["x"] - k["x"]) ** 2 + (d["y"] - k["y"]) ** 2 < rad * rad:
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    break
            if ok:
                grid.setdefault((gx, gy), []).append(d)
                kept.append(d)
    return kept


def main():
    dry = "--dry" in sys.argv
    sheets = load_georef()
    ev = eval_sheets(sheets)
    n_tiles = sum(len(tile_origins(int(sheets[c]["width"]), int(sheets[c]["height"])))
                  for c in ev)
    from collections import Counter
    print(f"평가 도엽 {len(ev)}개 {dict(Counter(ev.values()))} · 타일 {n_tiles}개")
    if dry:
        return 0

    from ultralytics import YOLO
    model = YOLO(WEIGHTS)

    dets = []
    for si, (code, region) in enumerate(sorted(ev.items())):
        g = sheets[code]
        W, H = int(g["width"]), int(g["height"])
        x0, y0 = float(g["x0"]), float(g["y0"])
        img = Image.open(os.path.join(AERIAL, g["rel_path"])).convert("RGB")
        origins = tile_origins(W, H)
        for b0 in range(0, len(origins), BATCH):
            batch = origins[b0:b0 + BATCH]
            crops = [img.crop((x, y, x + TILE, y + TILE)) for x, y in batch]
            results = model.predict(crops, imgsz=TILE, conf=CONF_MIN,
                                    verbose=False)
            for (wx, wy), res in zip(batch, results):
                for box in res.boxes:
                    cx, cy = box.xywh[0][:2].tolist()
                    px, py = wx + cx, wy + cy
                    x6 = x0 + px * 0.25
                    y6 = y0 - py * 0.25
                    lon, lat = T_5186_4326.transform(x6, y6)
                    X, Y = T_4326_5179.transform(lon, lat)
                    dets.append({"sheet": code, "year": g["year"],
                                 "region": region,
                                 "cls": NAMES[int(box.cls)],
                                 "score": float(box.conf), "x": X, "y": Y})
        img.close()
        print(f"  [{si + 1}/{len(ev)}] {code} ({region}) 누적 탐지 {len(dets)}",
              flush=True)

    kept = dedup(dets)
    print(f"탐지 {len(dets)} → 중복 제거 후 {len(kept)}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sheet", "year", "region", "cls", "score", "x_5179", "y_5179"])
        for d in sorted(kept, key=lambda d: -d["score"]):
            w.writerow([d["sheet"], d["year"], d["region"], d["cls"],
                        f"{d['score']:.4f}", f"{d['x']:.1f}", f"{d['y']:.1f}"])
    print(f"저장: {OUT} (비공개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
