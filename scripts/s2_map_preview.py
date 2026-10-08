# -*- coding: utf-8 -*-
"""2단계 보조: 수요 격자·권역 배정 미리보기 지도 2장 (out/internal 전용 PNG).

- 수요 지도: 250m 집계, log10(kWh_adj) 색상
- 권역 지도: 250m 셀의 지배 권역을 의사난수 색으로 (티센 경계 확인용)
전력망 위치 구조가 드러나므로 산출물은 out/internal에만 둔다(공개·커밋 금지).
사용법:  python scripts/s2_map_preview.py
"""
from __future__ import annotations

import csv
import gzip
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects
import matplotlib.pyplot as plt
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRID = os.path.join(REPO, "out", "public", "s1_grid_demand_2024.csv.gz")
ZONES = os.path.join(REPO, "out", "internal", "s2_cell_zone_nearest.csv.gz")
OUT = os.path.join(REPO, "out", "internal")
BIN = 250.0  # m
NOTE = "KOR.gpkg © OpenStreetMap contributors (ODbL) · 내부 검토용 — 반출 금지"

plt.rcParams["font.family"] = ["Malgun Gothic", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False


def main() -> int:
    gx, gy, adj = [], [], []
    with gzip.open(GRID, "rt", encoding="utf-8") as f:
        r = csv.reader(f); next(r)
        for row in r:
            gx.append(int(row[0])); gy.append(int(row[1])); adj.append(float(row[3]))
    gx = np.array(gx); gy = np.array(gy); adj = np.array(adj)
    zid = []
    with gzip.open(ZONES, "rt", encoding="utf-8") as f:
        r = csv.reader(f); next(r)
        for row in r:
            zid.append(row[2])
    zuniq, zidx = np.unique(np.array(zid), return_inverse=True)
    print(f"셀 {len(gx):,} · 권역 {len(zuniq)}")

    # 100m 셀 → 250m 빈
    bx = (gx * 100 // BIN).astype(int)
    by = (gy * 100 // BIN).astype(int)
    x0, y0 = bx.min(), by.min()
    W, H = bx.max() - x0 + 1, by.max() - y0 + 1
    print(f"래스터 {W} x {H}")

    # ① 수요 지도 (--zones-only 시 건너뜀 — 뷰어가 파일을 잠근 경우 등)
    skip_demand = "--zones-only" in sys.argv
    dem = np.zeros((H, W))
    np.add.at(dem, (by - y0, bx - x0), adj)
    p1 = os.path.join(OUT, "s2_map_demand_2024.png")
    if not skip_demand:
        img = np.full((H, W), np.nan)
        m = dem > 0
        img[m] = np.log10(dem[m])
        fig, ax = plt.subplots(figsize=(10, 13), dpi=150)
        im = ax.imshow(img, origin="lower", cmap="magma", interpolation="nearest")
        ax.set_title("전력수요 격자 지도 2024 (250m, 규율보정 ⓑ, log10 kWh)")
        ax.axis("off")
        fig.colorbar(im, ax=ax, shrink=0.6, label="log10(kWh/년)")
        fig.text(0.01, 0.01, NOTE, fontsize=7)
        fig.savefig(p1, bbox_inches="tight"); plt.close(fig)

    # ② 권역 지도 v2 — 색 = 권역 연간 수요(로그), 시도 경계선 + 상위 권역 라벨
    import struct as _st

    # 권역별 수요 합과 수요가중 중심
    ztot = np.zeros(len(zuniq)); zcx = np.zeros(len(zuniq)); zcy = np.zeros(len(zuniq))
    np.add.at(ztot, zidx, adj)
    np.add.at(zcx, zidx, adj * (gx + 0.5) * 100)
    np.add.at(zcy, zidx, adj * (gy + 0.5) * 100)
    with np.errstate(invalid="ignore"):
        zcx /= ztot; zcy /= ztot

    best = {}
    for x, y, z, v in zip(bx - x0, by - y0, zidx, adj):
        k = (y, x)
        cur = best.get(k)
        if cur is None or v > cur[1]:
            best[k] = (z, v)
    zone_of = np.full((H, W), -1, dtype=np.int32)
    for (y, x), (z, _) in best.items():
        zone_of[y, x] = z
    zimg = np.full((H, W), np.nan)
    m2 = zone_of >= 0
    zimg[m2] = np.log10(np.maximum(ztot[zone_of[m2]], 1.0) / 1e6)  # GWh 로그

    # 권역 경계(이웃 빈의 권역이 다른 곳)
    edge = np.zeros((H, W), dtype=bool)
    edge[:, 1:] |= (zone_of[:, 1:] != zone_of[:, :-1]) & (zone_of[:, 1:] >= 0) & (zone_of[:, :-1] >= 0)
    edge[1:, :] |= (zone_of[1:, :] != zone_of[:-1, :]) & (zone_of[1:, :] >= 0) & (zone_of[:-1, :] >= 0)

    fig, ax = plt.subplots(figsize=(10, 13), dpi=150)
    im = ax.imshow(zimg, origin="lower", cmap="viridis", interpolation="nearest")
    ey, ex = np.where(edge)
    ax.scatter(ex, ey, s=0.05, c="black", alpha=0.35, linewidths=0)
    # 시도 경계선 (SGIS bnd_sido, EPSG:5179) — 위치 참조용
    shp = os.path.join(os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid"),
                       "행정경계", "bnd_sido_00_2025_2Q", "bnd_sido_00_2025_2Q.shp")
    with open(shp, "rb") as f:
        f.read(100)
        while True:
            rh = f.read(8)
            if len(rh) < 8:
                break
            clen = _st.unpack(">i", rh[4:8])[0] * 2
            body = f.read(clen)
            if _st.unpack("<i", body[:4])[0] != 5:
                continue
            nparts, npts = _st.unpack("<2i", body[36:44])
            parts = _st.unpack(f"<{nparts}i", body[44:44 + 4 * nparts])
            pts = np.frombuffer(body[44 + 4 * nparts:44 + 4 * nparts + 16 * npts],
                                dtype="<f8").reshape(-1, 2)
            for pi in range(nparts):
                s = parts[pi]
                e = parts[pi + 1] if pi + 1 < nparts else npts
                ring = pts[s:e]
                ax.plot(ring[:, 0] / BIN - x0, ring[:, 1] / BIN - y0,
                        color="black", linewidth=0.5, alpha=0.55)
    # 상위 권역 라벨
    top_z = np.argsort(-ztot)[:15]
    for z in top_z:
        name = zuniq[z]
        # zone_id → 이름은 공개 집계 CSV에서
        pass
    names = {}
    with open(os.path.join(REPO, "out", "public", "s2_zone_demand_nearest.csv"),
              encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            names[row["zone_id"]] = row["변전소명"]
    placed = []
    MIN_GAP = 55  # 빈(=250m) 단위 — 라벨 겹침 방지
    for z in np.argsort(-ztot):  # 수요 순으로 훑되, 겹치면 건너뜀
        label = names.get(zuniq[z], zuniq[z]).replace("변전소", "")
        if label.startswith("(무명"):
            continue
        lx, ly = zcx[z] / BIN - x0, zcy[z] / BIN - y0
        if any((lx - px) ** 2 + (ly - py) ** 2 < MIN_GAP ** 2 for px, py in placed):
            continue
        ax.annotate(label, (lx, ly), fontsize=7.5, fontweight="bold", color="white",
                    ha="center",
                    path_effects=[matplotlib.patheffects.withStroke(linewidth=2,
                                                                    foreground="black")])
        placed.append((lx, ly))
        if len(placed) >= 12:
            break
    ax.set_title(f"변전소 권역 배정 — 규칙① 최근접/티센 ({len(zuniq)}권역)\n"
                 f"색 = 권역 연간 수요(보정 ⓑ) · 검은 실선 = 시도 경계 · 라벨 = 수요 상위 15")
    ax.axis("off")
    fig.colorbar(im, ax=ax, shrink=0.55, label="log10(권역 수요 GWh/년)")
    fig.text(0.01, 0.01, NOTE, fontsize=7)
    p2 = os.path.join(OUT, "s2_map_zones_nearest_v2.png")
    try:
        fig.savefig(p2, bbox_inches="tight")
    except OSError:  # 뷰어가 파일을 잠근 경우
        p2 = p2.replace(".png", "_new.png")
        fig.savefig(p2, bbox_inches="tight")
    plt.close(fig)

    print("저장:", p1)
    print("저장:", p2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
