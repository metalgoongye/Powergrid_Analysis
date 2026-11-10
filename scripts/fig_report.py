# -*- coding: utf-8 -*-
r"""보고서 그림 4매 생성 (워킹페이퍼 삽입용).

  fig_3_1_pipeline.png   분석 파이프라인 개요(모식도)
  fig_5_1_demand.png     전력수요 지도 — 건물계 / 산업용 (1km 집계, 로그 색)
  fig_6_3_sigma_k.png    구역 수와 신호 보존율 — 행정 획정 vs 계통 정합 군집
  fig_5_2_towers.png     철탑 탐지 성공례 몽타주 (항공 25cm 크롭 — 비공개 폴더)

앞의 3매는 격자·행정 집계 수준이라 out/public/, 몽타주는 영상 크롭이라
Z:\2026_WP_out\internal\figs\ 에 쓴다(좌표·주기 없음).
"""
from __future__ import annotations

import csv
import gzip
import math
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUB = os.path.join(REPO, "out", "public")
INT = os.path.join(REPO, "out", "internal")
ZFIG = r"Z:\2026_WP_out\internal\figs"

for fam in ("KoPubDotum", "KoPubDotum Medium", "Malgun Gothic"):
    if any(f.name == fam for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = fam
        break
plt.rcParams["axes.unicode_minus"] = False
print("글꼴:", plt.rcParams["font.family"])


def fig_pipeline():
    fig, ax = plt.subplots(figsize=(7.1, 3.5), dpi=200)
    ax.set_xlim(0, 100); ax.set_ylim(0, 50); ax.axis("off")
    stages = [
        ("1단계\n수요 지도", "건물에너지 검침 × 지적\n→ 100m 격자 수요\n+ 산업용 연면적 배분", "#dbe9f6"),
        ("2단계\n권역 배정", "변전소 권역(벽돌)\n배정규칙 4종\n(최근접·망 거리·용량·무작위)", "#e2f0d9"),
        ("3단계\n전력망 검증", "철탑: 항공영상 GeoAI 탐지\n변전소: 한전 목록 대조\n(2기둥 검증)", "#fff2cc"),
        ("4단계\n획정 비교", "신호 보존율 σ(Z)\n효율·단순성·분배 3축\n정부안 vs 계통 정합", "#f8d7da"),
    ]
    w, gap, x0 = 21.5, 3.5, 1.5
    for i, (title, body, col) in enumerate(stages):
        x = x0 + i * (w + gap)
        ax.add_patch(plt.Rectangle((x, 14), w, 30, fc=col, ec="#555", lw=0.8))
        ax.text(x + w / 2, 38.5, title, ha="center", va="center", fontsize=9.5, weight="bold")
        ax.text(x + w / 2, 24, body, ha="center", va="center", fontsize=7.2, linespacing=1.5)
        if i < len(stages) - 1:
            ax.annotate("", xy=(x + w + gap - 0.4, 29), xytext=(x + w + 0.4, 29),
                        arrowprops=dict(arrowstyle="-|>", color="#333", lw=1.1))
    ax.text(50, 7.5, "투입 모수: 혼잡료 λ(SMP 격차) · 귀속률 s(지역소득) · 진입 비중 μ(공장등록) · "
            "임금 w(고용조사) · 수요 ε·e(격자 지도)", ha="center", va="center", fontsize=7.4,
            bbox=dict(boxstyle="round,pad=0.5", fc="#f2f2f2", ec="#888", lw=0.6))
    ax.text(50, 47.5, "공개 공간정보 → 검증 → 권역 → 후생 비교", ha="center", fontsize=8.5, color="#333")
    p = os.path.join(PUB, "fig_3_1_pipeline.png")
    fig.savefig(p, bbox_inches="tight", pad_inches=0.08); plt.close(fig)
    return p


def _grid_1km(path, col):
    acc = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            k = (int(row[0]) // 10, int(row[1]) // 10)
            acc[k] = acc.get(k, 0.0) + float(row[col])
    return acc


def fig_demand():
    b = _grid_1km(os.path.join(PUB, "s1_grid_demand_2024.csv.gz"), 3)
    ind = _grid_1km(os.path.join(PUB, "s1_grid_industrial_2024.csv.gz"), 2)
    xs = [k[0] for k in list(b) + list(ind)]
    ys = [k[1] for k in list(b) + list(ind)]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    fig, axes = plt.subplots(1, 2, figsize=(7.1, 4.6), dpi=200)
    for ax, acc, title in ((axes[0], b, "건물계(주택·일반) 212.6TWh"),
                           (axes[1], ind, "산업용 286.2TWh")):
        img = np.full((y1 - y0 + 1, x1 - x0 + 1), np.nan)
        for (gx, gy), v in acc.items():
            if v > 0:
                img[gy - y0, gx - x0] = v / 1e6  # GWh per km²
        im = ax.imshow(img, origin="lower", cmap="inferno_r",
                       norm=LogNorm(vmin=0.05, vmax=500), interpolation="nearest")
        ax.set_title(title, fontsize=9); ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_linewidth(0.4)
    cb = fig.colorbar(im, ax=axes, orientation="horizontal", fraction=0.045, pad=0.03)
    cb.set_label("연간 전력수요 (GWh/km², 로그 눈금)", fontsize=8)
    cb.ax.tick_params(labelsize=7)
    p = os.path.join(PUB, "fig_5_1_demand.png")
    fig.savefig(p, bbox_inches="tight", pad_inches=0.06); plt.close(fig)
    return p


def fig_sigma_k():
    adm = []
    with open(os.path.join(PUB, "s4_zoning_compare.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(ln for ln in f if not ln.startswith("#")):
            if r["σ_A구간"] and r["획정"] != "전국단일":
                lo, hi = (float(x) for x in r["σ_A구간"].split("~"))
                adm.append((r["획정"], int(r["구역수"]), lo, hi))
    ks, ss = [], []
    with open(os.path.join(PUB, "s4_cluster_sigma.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(ln for ln in f if not ln.startswith("#")):
            ks.append(int(r["구역수_K"])); ss.append(float(r["sigma"]))
    fig, ax = plt.subplots(figsize=(6.3, 3.6), dpi=200)
    ax.plot(ks, ss, "-o", color="#c0392b", ms=3.5, lw=1.4, label="계통 정합 군집(시제품)")
    label = {"정부안4광역권": "정부안\n(4광역권)", "시도": "시도", "시군구": "시군구", "변전소권역": "변전소 권역"}
    for name, n, lo, hi in adm:
        mid = (lo + hi) / 2
        ax.errorbar([n], [mid], yerr=[[mid - lo], [hi - mid]], fmt="s", color="#1f4e79",
                    ms=5, capsize=3, lw=1)
        ax.annotate(label.get(name, name), (n, mid), textcoords="offset points",
                    xytext=(7, -11), fontsize=7.5, color="#1f4e79")
    ax.errorbar([], [], fmt="s", color="#1f4e79", ms=5, label="행정구역 기반 획정(구간 = 배정규칙 4종)")
    ax.set_xscale("log"); ax.set_xlim(1.5, 1200); ax.set_ylim(0, 1.05)
    ax.set_xlabel("구역 수 (로그 눈금)", fontsize=8.5)
    ax.set_ylabel("신호 보존율 σ(Z)", fontsize=8.5)
    ax.tick_params(labelsize=7.5); ax.grid(alpha=0.3, lw=0.4)
    ax.legend(fontsize=7.5, loc="lower right", frameon=False)
    p = os.path.join(PUB, "fig_6_3_sigma_k.png")
    fig.savefig(p, bbox_inches="tight", pad_inches=0.06); plt.close(fig)
    return p


def fig_towers():
    """고신뢰 탐지 중 OSM 철탑과 매칭된 상위 8건의 256px 크롭 몽타주."""
    from PIL import Image
    from pyproj import Transformer
    Image.MAX_IMAGE_PIXELS = None
    aerial = r"Z:\powergrid_spatial_data\위성정사영상\위성정사영상_분석대상지역\261026_25cm"
    t1 = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
    t2 = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
    sheets = {}
    with open(os.path.join(PUB, "s3_sheet_georef.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["flag"]:
                continue
            cur = sheets.get(r["sheet"])
            if cur is None or (r["year"] == "2024" and cur["year"] != "2024"):
                sheets[r["sheet"]] = r
    with open(os.path.join(INT, "s3_osm_groundtruth.csv"), encoding="utf-8") as f:
        osm = [g for g in csv.DictReader(ln for ln in f if not ln.startswith("#"))
               if g["class"] == "tower" and g["sigungu"] in ("서천군", "보령시", "은평구")]
    ox = np.array([float(g["x_5179"]) for g in osm])
    oy = np.array([float(g["y_5179"]) for g in osm])
    dets = [r for r in csv.DictReader(open(os.path.join(INT, "detections25_dedup.csv"),
                                           encoding="utf-8")) if r["cls"] == "tower"]
    dets.sort(key=lambda r: -float(r["score"]))
    picks, per_region = [], {}
    for r in dets:
        x, y = float(r["x_5179"]), float(r["y_5179"])
        if np.hypot(ox - x, oy - y).min() > 50:
            continue
        if per_region.get(r["region"], 0) >= 3:
            continue
        per_region[r["region"]] = per_region.get(r["region"], 0) + 1
        picks.append(r)
        if len(picks) == 8:
            break
    tiles = []
    for r in picks:
        g = sheets[r["sheet"]]
        lon, lat = t1.transform(float(r["x_5179"]), float(r["y_5179"]))
        x6, y6 = t2.transform(lon, lat)
        px = (x6 - float(g["x0"])) / 0.25
        py = (float(g["y0"]) - y6) / 0.25
        img = Image.open(os.path.join(aerial, g["rel_path"])).convert("RGB")
        tiles.append(img.crop((int(px) - 128, int(py) - 128, int(px) + 128, int(py) + 128)))
        img.close()
    mont = Image.new("RGB", (4 * 256 + 3 * 6, 2 * 256 + 6), "white")
    for i, t in enumerate(tiles):
        mont.paste(t, ((i % 4) * 262, (i // 4) * 262))
    os.makedirs(ZFIG, exist_ok=True)
    p = os.path.join(ZFIG, "fig_5_2_towers.png")
    mont.save(p)
    return p


def main() -> int:
    for fn in (fig_pipeline, fig_demand, fig_sigma_k, fig_towers):
        print("생성:", fn())
    return 0


if __name__ == "__main__":
    sys.exit(main())
