# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 장면 타일링 — 제로샷 탐지(Colab) 입력 생성.

- 입력: layers.yml 등록 장면(raster_scene_set) × RGB PS 3밴드(16비트) +
  georef 매니페스트(out/public/s3_scene_georef.csv, status=OK만).
- 타일: 1024×1024px(지상 512m), 겹침 128px(스트라이드 896). 경계 타일은
  0으로 패딩, 전부 0(여백)인 타일은 생략.
- 밝기: 장면·밴드별 2–98 백분위(표본 행, 0 제외) 선형 스트레칭 → 8비트 JPEG
  (품질 90). 0(nodata)은 0 유지.
- georeference: 타일 (c0, r0)의 월드 원점 = 장면 아핀에 픽셀 대입. 매니페스트에
  장면 아핀(a,b,d,e)과 타일 원점(x0, y0)을 기록 — X = x0 + a·dc + b·dr.
- 출력(저장소 밖, 대용량): DATA_ROOT/powergrid_spatial_data/정사영상_전국/
  tiles_pilot/<장면ID>/*.jpg + tiles_manifest.csv (루트에 통합).

사용: python scripts/s3_tile_scenes.py            # 등록 전 장면
      python scripts/s3_tile_scenes.py <장면ID>   # 한 장면만
"""
import csv
import io
import os
import sys

import numpy as np
import yaml
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s3_scene_georef import StripTiff  # noqa: E402  (행 스트립 TIFF 리더 재사용)

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
TILES = os.path.join(DATA_ROOT, "powergrid_spatial_data", "정사영상_전국", "tiles_pilot")
GEOREF = os.path.join(REPO, "out", "public", "s3_scene_georef.csv")
TILE, STRIDE = 1024, 896  # 겹침 128px
P_LO, P_HI = 2.0, 98.0
JPEG_Q = 90
BANDS = ("R_PS", "G_PS", "B_PS")


def read_rows(tif, r0, r1):
    """행 [r0, r1) 블록을 (r1-r0, w) uint16로. 연속 오프셋 구간은 한 번에 읽음."""
    n = r1 - r0
    out = np.empty((n, tif.w), dtype=np.uint16)
    rb = tif.w * 2
    i = r0
    while i < r1:
        j = i
        while j + 1 < r1 and int(tif.offsets[j + 1]) - int(tif.offsets[j]) == rb:
            j += 1
        tif.f.seek(int(tif.offsets[i]))
        buf = tif.f.read(rb * (j - i + 1))
        out[i - r0:j - r0 + 1] = np.frombuffer(buf, dtype="<u2").reshape(-1, tif.w)
        i = j + 1
    return out


def band_stretch(tif, n_sample=60):
    """표본 행(균등 간격)의 0 제외 화소로 2–98 백분위."""
    vals = []
    for r in np.linspace(0, tif.h - 1, n_sample).astype(int):
        a = tif.row(r)
        nz = a[a > 0]
        if len(nz):
            vals.append(nz[:: max(1, len(nz) // 5000)])
    v = np.concatenate(vals)
    return float(np.percentile(v, P_LO)), float(np.percentile(v, P_HI))


def scenes_from_registry():
    with open(os.path.join(REPO, "config", "layers.yml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for ds in cfg["datasets"]:
        if ds["kind"] == "raster_scene_set":
            folder = os.path.join(DATA_ROOT, ds["path"])
            for sid in ds["scenes"]:
                yield sid, folder


def load_georef():
    out = {}
    with open(GEOREF, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["status"] == "OK":
                out[row["scene"]] = {k: float(row[k]) for k in "abcdef"}
    return out


def tile_scene(sid, folder, aff, writer):
    tifs = [StripTiff(os.path.join(folder, f"{sid}_L2G_{b}.tif")) for b in BANDS]
    W, H = tifs[0].w, tifs[0].h
    assert all(t.w == W and t.h == H for t in tifs), f"{sid}: 밴드 크기 불일치"
    stretch = [band_stretch(t) for t in tifs]
    os.makedirs(os.path.join(TILES, sid), exist_ok=True)
    n_tile = n_skip = 0
    for r0 in range(0, H, STRIDE):
        r1 = min(r0 + TILE, H)
        rows = [read_rows(t, r0, r1) for t in tifs]
        band8 = []
        for (lo, hi), a in zip(stretch, rows):
            b = np.clip((a.astype(np.float32) - lo) / (hi - lo), 0, 1)
            b = (b * 254 + 1).astype(np.uint8)  # 유효 화소는 1~255
            b[a == 0] = 0                        # nodata는 0 유지
            band8.append(b)
        for c0 in range(0, W, STRIDE):
            c1 = min(c0 + TILE, W)
            tile = np.zeros((TILE, TILE, 3), dtype=np.uint8)
            for k in range(3):
                tile[: r1 - r0, : c1 - c0, k] = band8[k][:, c0:c1]
            if not tile.any():
                n_skip += 1
                continue
            name = f"{sid}_r{r0:05d}_c{c0:05d}.jpg"
            Image.fromarray(tile).save(os.path.join(TILES, sid, name),
                                       quality=JPEG_Q)
            x0 = aff["a"] * c0 + aff["b"] * r0 + aff["c"]
            y0 = aff["d"] * c0 + aff["e"] * r0 + aff["f"]
            writer.writerow({"scene": sid, "file": f"{sid}/{name}",
                             "c0": c0, "r0": r0,
                             "x0": round(x0, 2), "y0": round(y0, 2),
                             "a": aff["a"], "b": aff["b"],
                             "d": aff["d"], "e": aff["e"],
                             "valid_w": c1 - c0, "valid_h": r1 - r0})
            n_tile += 1
    for t in tifs:
        t.close()
    return n_tile, n_skip


def main():
    only = set(sys.argv[1:])
    georef = load_georef()
    os.makedirs(TILES, exist_ok=True)
    manifest = os.path.join(TILES, "tiles_manifest.csv")
    new = not os.path.exists(manifest) or not only  # 전체 실행이면 새로 씀
    seen = set()
    if not new:
        with open(manifest, encoding="utf-8") as f:
            seen = {r["scene"] for r in csv.DictReader(f)}
    mode = "w" if new else "a"
    with open(manifest, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["scene", "file", "c0", "r0",
                                               "x0", "y0", "a", "b", "d", "e",
                                               "valid_w", "valid_h"])
        if mode == "w":
            writer.writeheader()
        total = 0
        for sid, folder in scenes_from_registry():
            if only and sid not in only:
                continue
            if sid not in georef:
                print(f"[건너뜀] {sid}: georef 매니페스트에 OK 없음")
                continue
            if sid in seen:
                print(f"[건너뜀] {sid}: 매니페스트에 이미 존재")
                continue
            n, skip = tile_scene(sid, folder, georef[sid], writer)
            f.flush()
            total += n
            print(f"[완료] {sid}: 타일 {n} (여백 생략 {skip})")
        print(f"\n총 타일 {total} → {TILES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
