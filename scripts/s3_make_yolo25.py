# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 항공 25cm — OSM 설비 중심 크롭 + YOLO 라벨 생성.

위성(0.5m) 때의 전면 타일링(5.2만 장) 대신, OSM 설비(철탑·변전소) 중심
1024px 크롭과 동수의 배경 크롭만 생성한다(학습·평가에 필요한 최소 집합).
- 분할: 학습 = 화성·세종 / 평가 = 서천·보령·은평 (위성 §9-b와 동일 설계)
- 상자: 철탑 48px(지상 12m)·변전소 192px(48m) 고정 — 0.5m 때의 2배(해상도 비례)
- 배경: 설비 보유 도엽에서 설비로부터 400px(100m) 이상 떨어진 무작위 창
- 좌표 사슬: GT(5179) → 5186 → 도엽 아핀(s3_sheet_georef.csv) → 픽셀
- 산출물은 좌표 유도 가능(파일명=도엽+오프셋)이므로 out/internal/yolo25/

사용: python s3_make_yolo25.py          # 전체 생성 (기존 출력 삭제 후 재생성)
"""
import csv
import os
import random
import shutil
import sys
from collections import defaultdict

import numpy as np
from PIL import Image
from pyproj import Transformer

sys.stdout.reconfigure(encoding="utf-8")
Image.MAX_IMAGE_PIXELS = None  # 도엽 1장 ≈ 1억 화소 — 폭탄 경고 해제(출처 신뢰)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AERIAL = r"Z:\powergrid_spatial_data\위성정사영상\위성정사영상_분석대상지역\261026_25cm"
GEOREF = os.path.join(REPO, "out", "public", "s3_sheet_georef.csv")
GT = os.path.join(REPO, "out", "internal", "s3_osm_groundtruth.csv")
OUT = os.path.join(REPO, "out", "internal", "yolo25")

CROP = 1024
BOX = {"tower": 48, "substation": 192}   # px (0.25m 기준)
CLS = {"tower": 0, "substation": 1}
TRAIN = {"화성시", "세종시"}
EVAL = {"서천군", "보령시", "은평구"}
NEG_MIN_DIST = 400   # px — 배경 크롭의 설비 최소 이격(100m)
JITTER = 300         # px — 양성 크롭 중심 흔들기(상자 위치 다양화)
SEED = 20261027

T_5179_4326 = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
T_4326_5186 = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)


def sheet_codes(lon, lat):
    """경위도 배열 → 1:5,000 도엽번호(8자리 str) 배열 (s3_sheet_georef.py 규약)."""
    latd = np.floor(lat).astype(int)
    lond = np.floor(lon).astype(int)
    cell = latd * 10 + (lond % 10)
    r50 = np.floor((latd + 1 - lat) / 0.25).astype(int).clip(0, 3)
    c50 = np.floor((lon - lond) / 0.25).astype(int).clip(0, 3)
    i50 = r50 * 4 + c50 + 1
    lat_top = latd + 1 - r50 * 0.25
    lon_lft = lond + c50 * 0.25
    r5 = np.floor((lat_top - lat) / 0.025).astype(int).clip(0, 9)
    c5 = np.floor((lon - lon_lft) / 0.025).astype(int).clip(0, 9)
    n5 = r5 * 10 + c5 + 1
    return np.array([f"{c}{i:02d}{n:03d}" for c, i, n in zip(cell, i50, n5)])


def load_georef():
    """도엽 → (rel_path, x0, y0, W, H). 동일 도엽 복수 연도면 2024(기준년) 우선."""
    sheets = {}
    with open(GEOREF, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["flag"]:
                continue
            cur = sheets.get(r["sheet"])
            if cur is None or (r["year"] == "2024" and cur["year"] != "2024"):
                sheets[r["sheet"]] = r
    return sheets


def load_gt():
    with open(GT, encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    rows = list(csv.DictReader(lines))
    X = np.array([float(r["x_5179"]) for r in rows])
    Y = np.array([float(r["y_5179"]) for r in rows])
    lon, lat = T_5179_4326.transform(X, Y)
    X6, Y6 = T_4326_5186.transform(lon, lat)
    codes = sheet_codes(lon, lat)
    return rows, X6, Y6, codes


def yolo_lines(objs, wx, wy):
    """창(wx, wy 원점) 안에 걸치는 객체들의 YOLO 라벨. 중심이 창 밖이면 제외."""
    lines = []
    for cls, px, py in objs:
        cx, cy = px - wx, py - wy
        if not (0 <= cx < CROP and 0 <= cy < CROP):
            continue
        b = BOX[cls]
        lines.append(f"{CLS[cls]} {cx / CROP:.6f} {cy / CROP:.6f} "
                     f"{min(b, 2 * cx, 2 * (CROP - cx)) / CROP:.6f} "
                     f"{min(b, 2 * cy, 2 * (CROP - cy)) / CROP:.6f}")
    return lines


def main():
    random.seed(SEED)
    rng = np.random.default_rng(SEED)
    sheets = load_georef()
    rows, X6, Y6, codes = load_gt()

    # 도엽별 객체 목록 (georef 있는 도엽만)
    by_sheet = defaultdict(list)   # sheet -> [(cls, px, py, split)]
    n_skip = 0
    for i, r in enumerate(rows):
        code = codes[i]
        if code not in sheets:
            n_skip += 1
            continue
        g = sheets[code]
        px = (X6[i] - float(g["x0"])) / 0.25
        py = (float(g["y0"]) - Y6[i]) / 0.25
        split = "train" if r["sigungu"] in TRAIN else "val"
        by_sheet[code].append((r["class"], px, py, split))

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    for sp in ("train", "val"):
        os.makedirs(os.path.join(OUT, sp, "images"))
        os.makedirs(os.path.join(OUT, sp, "labels"))

    stats = defaultdict(int)
    for si, (code, objs) in enumerate(sorted(by_sheet.items())):
        g = sheets[code]
        W, H = int(g["width"]), int(g["height"])
        img = Image.open(os.path.join(AERIAL, g["rel_path"])).convert("RGB")
        pts = np.array([(px, py) for _c, px, py, _s in objs])
        all_objs = [(c, px, py) for c, px, py, _s in objs]
        covered = set()
        windows = []   # (wx, wy, is_pos, split)

        # 양성: 객체마다 1창 (이미 다른 창에 들어간 객체는 생략)
        for oi, (cls, px, py, split) in enumerate(objs):
            if oi in covered:
                continue
            wx = int(px - CROP / 2 + rng.integers(-JITTER, JITTER + 1))
            wy = int(py - CROP / 2 + rng.integers(-JITTER, JITTER + 1))
            wx = max(0, min(wx, W - CROP))
            wy = max(0, min(wy, H - CROP))
            # 이 창에 들어오는 객체 전부 covered 처리 (단, 같은 분할만 소비)
            inside = (pts[:, 0] >= wx) & (pts[:, 0] < wx + CROP) & \
                     (pts[:, 1] >= wy) & (pts[:, 1] < wy + CROP)
            for j in np.flatnonzero(inside):
                if objs[j][3] == split:
                    covered.add(int(j))
            windows.append((wx, wy, True, split))

        # 배경: 이 도엽의 양성 수만큼, 모든 설비에서 NEG_MIN_DIST 이상
        n_pos = len(windows)
        tries = 0
        negs = []
        while len(negs) < n_pos and tries < n_pos * 60:
            tries += 1
            wx = int(rng.integers(0, max(1, W - CROP)))
            wy = int(rng.integers(0, max(1, H - CROP)))
            cx, cy = wx + CROP / 2, wy + CROP / 2
            d = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy).min()
            if d >= NEG_MIN_DIST + CROP / 2:
                negs.append((wx, wy, False, objs[0][3]))
        windows += negs

        for wx, wy, is_pos, split in windows:
            crop = img.crop((wx, wy, wx + CROP, wy + CROP))
            name = f"{code}_{g['year']}_x{wx:05d}_y{wy:05d}"
            crop.save(os.path.join(OUT, split, "images", name + ".jpg"),
                      quality=90)
            lines = yolo_lines(all_objs, wx, wy) if is_pos else []
            with open(os.path.join(OUT, split, "labels", name + ".txt"),
                      "w", encoding="ascii") as f:
                f.write("\n".join(lines) + ("\n" if lines else ""))
            stats[f"{split}_{'pos' if is_pos else 'neg'}"] += 1
            stats[f"{split}_boxes"] += len(lines)
        img.close()
        if (si + 1) % 25 == 0:
            print(f"  진행 {si + 1}/{len(by_sheet)} 도엽", flush=True)

    print(f"\n크롭 생성 완료 → {OUT}")
    print(f"georef 밖 객체 제외 {n_skip}건 (커버리지 잔여 4도엽분)")
    for k in sorted(stats):
        print(f"  {k}: {stats[k]}")

    # dataset.yaml (ultralytics)
    with open(os.path.join(OUT, "dataset.yaml"), "w", encoding="utf-8") as f:
        f.write("path: .\ntrain: train/images\nval: val/images\n"
                "names:\n  0: tower\n  1: substation\n")
    print("dataset.yaml 작성 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
