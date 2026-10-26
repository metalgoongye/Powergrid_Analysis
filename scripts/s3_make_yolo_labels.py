# -*- coding: utf-8 -*-
"""[3단계 GeoAI] OSM 점 라벨 → YOLO 약지도 학습 라벨 생성.

제로샷 실패(정밀도 ~1%) 후속: OSM 철탑·변전소 좌표(점)를 자동 라벨로 삼아
소형 탐지기(YOLO)를 파인튜닝하기 위한 데이터셋을 만든다.

- 약지도(weak): OSM은 점만 주므로 고정 크기 박스를 가정(철탑 24px≈12m,
  변전소 96px≈48m). 설비 실제 크기의 거친 근사 — 한계로 명기.
- 권역 분리: 학습 = 화성·세종(라벨 957), 평가 = 서천·보령·은평(라벨 567).
  OSM→OSM 순환을 끊기 위해 학습/평가 권역을 겹치지 않게 나눈다.
- 좌표 역변환: 타일 원점(manifest x0,y0)+장면 아핀(a,b,d,e)으로 OSM 좌표를
  타일 내 픽셀로. 타일 겹침(128px) 때문에 한 설비가 복수 타일에 들어가면
  각 타일에 기록(자연 증강).
- 배경 타일: 라벨 없는 타일 일부를 음성 표본으로 섞어 오탐(밭고랑·철교) 억제.
  단 OSM 미등록 설비가 있을 수 있어 과하지 않게(양성 타일의 1배).

출력(out/internal/yolo/):
  labels_manifest.csv  — tile, split, class, cx_px, cy_px (검수용)
  <split>/labels/<tile stem>.txt — YOLO 포맷(class cx cy w h, 0~1 정규화)
  images_list_<split>.txt — 그 split에 필요한 타일 경로 목록(HF에서 받을 때 사용)
  data.yaml — ultralytics 학습 설정(이미지 경로는 Kaggle에서 채움)
"""
import csv
import os
import random
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INT = os.path.join(REPO, "out", "internal")
OUT = os.path.join(INT, "yolo")
TILE = 1024
BOX_PX = {"tower": 24, "substation": 96}   # 고정 박스 변 길이(px)
CLASS_ID = {"tower": 0, "substation": 1}
TRAIN_REGIONS = {"화성시", "세종시"}
VAL_REGIONS = {"서천군", "보령시", "은평구"}
NEG_RATIO = 1.0   # 양성 타일 수 대비 배경 타일 비율
random.seed(42)

MANIFEST = os.path.join(os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid"),
                        "powergrid_spatial_data", "정사영상_전국",
                        "tiles_pilot", "tiles_manifest.csv")


def load_osm():
    with open(os.path.join(INT, "s3_osm_groundtruth.csv"), encoding="utf-8") as f:
        return [r for r in csv.DictReader([l for l in f if not l.startswith("#")])]


def main():
    osm = load_osm()
    tiles = list(csv.DictReader(open(MANIFEST, encoding="utf-8")))
    # 장면별 타일 묶기
    by_scene = defaultdict(list)
    for t in tiles:
        by_scene[t["scene"]].append(t)

    # OSM 점별로 포함 타일을 역아핀으로 찾는다. 장면 bbox로 1차 필터해 속도 확보.
    import numpy as np
    # 장면별 타일 좌표 범위(빠른 필터용)
    scene_bbox = {}
    for sc, ts in by_scene.items():
        xs = [float(t["x0"]) for t in ts]; ys = [float(t["y0"]) for t in ts]
        scene_bbox[sc] = (min(xs) - 600, max(xs) + 600, min(ys) - 600, max(ys) + 600)
    tile_labels = defaultdict(list)
    tile_rgn = {}
    for g in osm:
        X, Y, cls, rgn = float(g["x_5179"]), float(g["y_5179"]), g["class"], g["sigungu"]
        for sc, ts in by_scene.items():
            x1, x2, y1, y2 = scene_bbox[sc]
            if not (x1 <= X <= x2 and y1 <= Y <= y2):
                continue
            for t in ts:
                a, b = float(t["a"]), float(t["b"])
                d, e = float(t["d"]), float(t["e"])
                x0, y0 = float(t["x0"]), float(t["y0"])
                det = a * e - b * d
                dc = (e * (X - x0) - b * (Y - y0)) / det
                dr = (-d * (X - x0) + a * (Y - y0)) / det
                if 0 <= dc < int(t["valid_w"]) and 0 <= dr < int(t["valid_h"]):
                    tile_labels[t["file"]].append((cls, dc, dr))
                    tile_rgn[t["file"]] = rgn
    print(f"라벨 보유 타일 {len(tile_labels)}개")

    # 배경(음성) 타일: 라벨 없는 타일 중 학습/평가 권역 장면에서 추출
    # 장면→권역: 그 장면에 라벨이 하나라도 있으면 그 권역, 없으면 스킵(권역 불명)
    scene_rgn = {}
    for ftile, rgn in tile_rgn.items():
        scene_rgn[ftile.split("/")[0]] = rgn
    pos_by_split = {"train": [], "val": []}
    for ftile in tile_labels:
        rgn = tile_rgn[ftile]
        sp = "train" if rgn in TRAIN_REGIONS else "val" if rgn in VAL_REGIONS else None
        if sp:
            pos_by_split[sp].append(ftile)

    neg_pool = defaultdict(list)
    for t in tiles:
        f = t["file"]
        if f in tile_labels:
            continue
        rgn = scene_rgn.get(t["scene"])
        if rgn in TRAIN_REGIONS:
            neg_pool["train"].append(f)
        elif rgn in VAL_REGIONS:
            neg_pool["val"].append(f)
    neg_by_split = {}
    for sp in ("train", "val"):
        random.shuffle(neg_pool[sp])
        neg_by_split[sp] = neg_pool[sp][:int(len(pos_by_split[sp]) * NEG_RATIO)]

    # 출력
    for sp in ("train", "val"):
        os.makedirs(os.path.join(OUT, sp, "labels"), exist_ok=True)
    mani = open(os.path.join(OUT, "labels_manifest.csv"), "w", newline="", encoding="utf-8")
    mw = csv.writer(mani)
    mw.writerow(["tile", "split", "class", "cx_px", "cy_px"])
    counts = defaultdict(int)
    for sp in ("train", "val"):
        imgs = pos_by_split[sp] + neg_by_split[sp]
        with open(os.path.join(OUT, f"images_list_{sp}.txt"), "w", encoding="utf-8") as il:
            il.write("\n".join(imgs) + "\n")
        for ftile in imgs:
            stem = os.path.splitext(os.path.basename(ftile))[0]
            lines = []
            for cls, dc, dr in tile_labels.get(ftile, []):
                w = h = BOX_PX[cls] / TILE
                cx, cy = dc / TILE, dr / TILE
                lines.append(f"{CLASS_ID[cls]} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
                mw.writerow([ftile, sp, cls, round(dc, 1), round(dr, 1)])
                counts[(sp, cls)] += 1
            # 배경 타일은 빈 txt
            open(os.path.join(OUT, sp, "labels", stem + ".txt"), "w").write("\n".join(lines))
        print(f"[{sp}] 타일 {len(imgs)} (양성 {len(pos_by_split[sp])}·배경 {len(neg_by_split[sp])})"
              f" · 철탑 {counts[(sp,'tower')]}·변전소 {counts[(sp,'substation')]}")
    mani.close()

    with open(os.path.join(OUT, "data.yaml"), "w", encoding="utf-8") as y:
        y.write("# YOLO 데이터셋 설정 — 경로는 Kaggle 학습 노트북에서 주입\n"
                "names:\n  0: tower\n  1: substation\n"
                "# train/val 이미지 경로는 노트북에서 images_list_*.txt로 구성\n")
    print(f"\n출력 → {OUT} (labels_manifest.csv, train/·val/ labels, images_list_*, data.yaml)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
