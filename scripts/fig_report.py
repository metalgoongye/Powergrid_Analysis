# -*- coding: utf-8 -*-
r"""보고서 그림 생성 (워킹페이퍼 삽입용).

  fig_3_1_pipeline.png     분석 파이프라인 개요(모식도)
  fig_5_1_demand.png       전력수요 지도 — 주택·일반용 / 산업용 (1km 집계, 로그 색)
  fig_6_1_twozone.png      2구역 공간구조(개념도)
  fig_6_2_welfare.png      반영률 θ와 나라 전체 후생(개념도)
  fig_6_3_sigma_k.png      구역 수와 신호 보존율 — 행정 획정 vs 여유용량으로 묶은 시험(상한)
  fig_6_3_sigma_k_alt.png  구역 수와 신호 보존율 — 행정 획정 vs 포화 여부로 묶은 구역
  fig_5_2_towers.png       철탑 탐지 성공례 몽타주 (항공 25cm 크롭 — 비공개 폴더)

몽타주만 영상 크롭이라 Z:\2026_WP_out\internal\figs\ 에 쓰고(좌표·주기 없음),
나머지는 격자·행정 집계나 개념도라 out/public/ 에 쓴다.
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
    fig, ax = plt.subplots(figsize=(7.1, 3.3), dpi=200)
    ax.set_xlim(0, 100); ax.set_ylim(0, 46); ax.axis("off")
    stages = [
        ("1단계\n수요 지도", "건물별 전력사용량 × 필지\n→ 100m 격자 수요\n+ 산업용: 공장 연면적 배분", "#dbe9f6"),
        ("2단계\n권역 배정", "격자 수요를 변전소에 배정\n(공급권역 생성)\n규칙 4종: 최근접·선로 거리·\n용량 제약·경계 무작위", "#e2f0d9"),
        ("3단계\n전력망 지도 검증", "OSM 지도를 독립자료로 점검\n철탑: 항공영상 GeoAI 탐지\n변전소: 한전 목록 대조", "#fff2cc"),
        ("4단계\n획정 비교", "신호 보존율 σ(Z) 계산\n(여유용량·공급 거리 신호)\n행정 단위 획정과\n변전소 권역을 묶은 구역 비교", "#f8d7da"),
    ]
    w, gap, x0 = 21.5, 3.5, 1.5
    for i, (title, body, col) in enumerate(stages):
        x = x0 + i * (w + gap)
        ax.add_patch(plt.Rectangle((x, 14), w, 30, fc=col, ec="#555", lw=0.8))
        ax.text(x + w / 2, 38.5, title, ha="center", va="center", fontsize=9.5, weight="bold")
        ax.text(x + w / 2, 24, body, ha="center", va="center", fontsize=6.9, linespacing=1.5)
        if i < len(stages) - 1:
            ax.annotate("", xy=(x + w + gap - 0.4, 29), xytext=(x + w + 0.4, 29),
                        arrowprops=dict(arrowstyle="-|>", color="#333", lw=1.1))
    ax.text(50, 6.5, "4단계의 해석에 쓰는 2구역 모형의 모수(정리 1~5의 조건 판정)\n"
            "혼잡료 λ(육지-제주 도매가격 차) · 이윤 귀속률 s(지역소득) · 신규 진입 비중 μ(공장등록) · "
            "인구 비중 $n_2/n$(고용조사)", ha="center", va="center", fontsize=7.2, linespacing=1.5,
            bbox=dict(boxstyle="round,pad=0.5", fc="#f2f2f2", ec="#888", lw=0.6))
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
    for ax, acc, name in ((axes[0], b, "주택·일반용(건물 검침 기반)"), (axes[1], ind, "산업용")):
        title = f"{name} {sum(acc.values()) / 1e9:.1f}TWh"
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
    cb.set_ticks([0.1, 1, 10, 100]); cb.set_ticklabels(["0.1", "1", "10", "100"])
    cb.ax.tick_params(labelsize=7)
    p = os.path.join(PUB, "fig_5_1_demand.png")
    fig.savefig(p, bbox_inches="tight", pad_inches=0.06); plt.close(fig)
    return p


def _plain_xticks(ax, ticks):
    """로그 축의 눈금을 10의 거듭제곱 표기 대신 구역 수 그대로 적는다."""
    ax.set_xticks(ticks); ax.set_xticklabels([str(t) for t in ticks]); ax.minorticks_off()


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
    ax.plot(ks, ss, "-o", color="#c0392b", ms=3.5, lw=1.4,
            label="여유용량이 비슷한 변전소 권역끼리 묶은 시험(묶을 때 쓴 값으로 잰 상한)")
    label = {"정부안4광역권": "4광역권\n(예시 분할)", "시도": "시도", "시군구": "시군구", "변전소권역": "변전소 권역"}
    for name, n, lo, hi in adm:
        mid = (lo + hi) / 2
        ax.errorbar([n], [mid], yerr=[[mid - lo], [hi - mid]], fmt="s", color="#1f4e79",
                    ms=5, capsize=3, lw=1)
        ax.annotate(label.get(name, name), (n, mid), textcoords="offset points",
                    xytext=(-50, -13) if name == "변전소권역" else (7, -11), fontsize=7.5, color="#1f4e79")
    ax.errorbar([], [], fmt="s", color="#1f4e79", ms=5, label="행정구역 기반 획정(구간 = 배정규칙 4종)")
    ax.set_xscale("log"); ax.set_xlim(1.5, 1200); ax.set_ylim(0, 1.05)
    _plain_xticks(ax, [2, 4, 8, 17, 50, 100] + [n for _, n, _, _ in adm if n > 100])
    ax.set_xlabel("구역 수 (로그 눈금)", fontsize=8.5)
    ax.set_ylabel("여유용량 신호 보존율 σ(Z)", fontsize=8.5)
    ax.tick_params(labelsize=7.5); ax.grid(alpha=0.3, lw=0.4)
    ax.legend(fontsize=7, loc="lower right", frameon=False)
    p = os.path.join(PUB, "fig_6_3_sigma_k.png")
    fig.savefig(p, bbox_inches="tight", pad_inches=0.06); plt.close(fig)
    return p


def fig_sigma_k_alt():
    """구역 수와 신호 보존율 — 행정 단위 획정 vs 포화 여부로 묶은 변전소 권역(s4_zone_alt.csv).

    왼쪽 = 묶을 때 쓴 해(2026년), 오른쪽 = 묶을 때 쓰지 않은 해(2028~2032년 평균).
    막대 = 배정 규칙 ①②③의 최소~최대."""
    with open(os.path.join(PUB, "s4_zone_alt.csv"), encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(ln for ln in f if not ln.startswith("#")))

    def rng(sel, col):
        v = [float(r[col]) for r in rows if sel(r)]
        assert len(v) == 3, f"규칙 ①②③ 값이 3개가 아님: {len(v)}"
        return min(v), max(v)

    adm = [("2구역(수도권·비수도권)", "2구역", (7, -12)), ("4광역권(예시)", "4광역권(예시)", (7, -12)),
           ("시도", "시도", (7, -12)), ("시군구", "시군구", (7, -12))]
    ks = (2, 4, 8, 17)
    fig, axes = plt.subplots(1, 2, figsize=(7.1, 3.3), dpi=200, sharey=True)
    for ax, col, title in ((axes[0], "σ_MW2026", "2026년 여유용량\n(권역 설정 기준 연도)"),
                           (axes[1], "σ_MW2028-32평균", "2028~2032년 평균 여유용량\n(권역 설정에 쓰지 않은 연도)")):
        lo, hi = zip(*[rng(lambda r, k=k: r["획정"] == "권역묶음" and r["묶는기준"] == "포화여부"
                           and r["인접k"] == "8" and int(r["구역수"]) == k, col) for k in ks])
        mid = [(a + b) / 2 for a, b in zip(lo, hi)]
        ax.errorbar(ks, mid, yerr=[[m - a for m, a in zip(mid, lo)], [b - m for m, b in zip(mid, hi)]],
                    fmt="-o", color="#c0392b", ms=3.5, lw=1.2, capsize=3, label="포화 여부로 병합한 변전소 권역")
        for name, lab, off in adm:
            a, b = rng(lambda r, name=name: r["획정"] == name, col)
            n = next(int(r["구역수"]) for r in rows if r["획정"] == name)
            m = (a + b) / 2
            ax.errorbar([n], [m], yerr=[[m - a], [b - m]], fmt="s", color="#1f4e79", ms=5, capsize=3, lw=1)
            ax.annotate(lab, (n, m), textcoords="offset points", xytext=off, fontsize=7, color="#1f4e79")
        ax.errorbar([], [], fmt="s", color="#1f4e79", ms=5, label="행정 단위 획정")
        ax.set_xscale("log"); ax.set_xlim(1.5, 600); ax.set_ylim(0, 0.75)
        _plain_xticks(ax, list(ks) + [next(int(r["구역수"]) for r in rows if r["획정"] == "시군구")])
        ax.set_title(title, fontsize=8.5)
        ax.set_xlabel("구역 수 (로그 눈금)", fontsize=8)
        ax.tick_params(labelsize=7.5); ax.grid(alpha=0.3, lw=0.4)
    axes[0].set_ylabel("여유용량 신호 보존율 σ(Z)", fontsize=8.5)
    axes[0].legend(fontsize=7.5, loc="upper left", frameon=False)
    p = os.path.join(PUB, "fig_6_3_sigma_k_alt.png")
    fig.savefig(p, bbox_inches="tight", pad_inches=0.06); plt.close(fig)
    return p


def fig_two_zone():
    """2구역 공간구조(개념도) — 기호는 보고서 6장 2절과 같게(융통량 K, 융통용량 T, 혼잡료 λ)."""
    fig, ax = plt.subplots(figsize=(6.3, 2.9), dpi=200)
    ax.set_xlim(0, 100); ax.set_ylim(0, 46); ax.set_aspect("equal"); ax.axis("off")
    for x, col, edge, title, sub, foot in (
            (2, "#dbe9f6", "#3b6ea8", "구역 1: 수도권", "수요 중심 · 전력을 받는 쪽", "가구 $n_1$ · 기업 $m_1$ · 환경 수준 $E_1$"),
            (64, "#e2f0d9", "#4b8b3b", "구역 2: 비수도권", "공급 중심 · 전력을 보내는 쪽", "가구 $n_2$ · 기업 $m_2$ · 환경 수준 $E_2$")):
        ax.add_patch(plt.Rectangle((x, 4), 34, 39, fc=col, ec=edge, lw=0.9))
        ax.text(x + 17, 39, title, ha="center", va="center", fontsize=9.5, weight="bold", color=edge)
        ax.text(x + 17, 34.6, sub, ha="center", va="center", fontsize=7.5)
        ax.text(x + 17, 8, foot, ha="center", va="center", fontsize=7.5)
    ax.add_patch(plt.Circle((19, 21.5), 7.5, fc="#5b84c4", ec="none"))
    ax.text(19, 21.5, "대도시\n수요", ha="center", va="center", fontsize=8, color="white", linespacing=1.3)
    for cx, cy in ((76, 28), (86, 23), (76, 18)):
        ax.add_patch(plt.Polygon([(cx - 3.2, cy - 2.6), (cx + 3.2, cy - 2.6), (cx, cy + 3.2)], fc="#f28e2b", ec="none"))
    ax.text(81, 12.3, "발전 단지", ha="center", va="center", fontsize=7.5, color="#b35c00")
    ax.annotate("", xy=(36.6, 21.5), xytext=(63.4, 21.5), arrowprops=dict(arrowstyle="-|>", color="#333", lw=2.2))
    ax.text(50, 26.5, "융통량 $K$ ≤ 융통용량 $T$", ha="center", va="center", fontsize=8)
    ax.text(50, 14.5, "선로가 꽉 차면($K = T$)\n두 구역의 한계비용 차이\nλ(혼잡료) 발생", ha="center", va="center",
            fontsize=7.2, color="#555", linespacing=1.45)
    p = os.path.join(PUB, "fig_6_1_twozone.png")
    fig.savefig(p, bbox_inches="tight", pad_inches=0.06); plt.close(fig)
    return p


def fig_welfare_curve():
    """반영률 θ와 나라 전체 후생(개념도). θ는 0(단일요금)~1(원가 차이 전부 반영).

    곡선은 설명을 위한 예시 함수 W = θ(1.1 − θ)이며 추정값이 아니다. 정점이 1보다 안쪽에 있는 것은
    환경 부담과 정산·행정 비용이 θ와 함께 커지는 경우를 그린 것(모델링 노트 v2.2 그림 6의 설명)."""
    def w(t):
        return t * (1.1 - t)

    th = np.linspace(0, 1, 201)
    tb, tc = 0.55, 0.9
    fig, ax = plt.subplots(figsize=(5.6, 3.2), dpi=200)
    ax.plot(th, w(th), color="#222", lw=1.8)
    for t in (tb, tc):
        ax.plot([t, t], [-0.03, w(t)], ls="--", color="#888", lw=0.8)
    ax.plot([0, tb, tc], [w(0), w(tb), w(tc)], "o", color="#c0392b", ms=5, zorder=3)
    for t, lab, off, ha in ((0, "A  지금의 단일요금", (8, -3), "left"), (tb, "B  후생이 가장 높은 점", (0, 8), "center"),
                            (tc, "C  과도한 차등", (6, 6), "left")):
        ax.annotate(lab, (t, w(t)), textcoords="offset points", xytext=off, ha=ha, fontsize=8)
    ax.annotate("", xy=(0.17, w(0.17) - 0.035), xytext=(0.04, w(0.04) - 0.035),
                arrowprops=dict(arrowstyle="-|>", color="#1f4e79", lw=1.1))
    ax.text(0.2, 0.085, "정리 2: 단일요금에서 조금\n차등하면 후생이 오름", fontsize=7.2, color="#1f4e79", linespacing=1.4)
    ax.text(1.0, 0.345, "곡선의 모양과 B의 위치는 설명을 위한 예시", ha="right", fontsize=6.8, color="#777")
    ax.set_xlim(-0.03, 1.04); ax.set_ylim(-0.03, 0.37)
    ax.set_xticks([0, 1]); ax.set_xticklabels(["0\n(단일요금)", "1\n(원가 차이 전부 반영)"], fontsize=7.5)
    ax.set_yticks([])
    ax.set_xlabel("반영률 θ", fontsize=8.5, labelpad=-8)
    ax.set_ylabel("나라 전체 후생 W", fontsize=8.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    p = os.path.join(PUB, "fig_6_2_welfare.png")
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
    for fn in (fig_pipeline, fig_demand, fig_two_zone, fig_welfare_curve, fig_sigma_k, fig_sigma_k_alt, fig_towers):
        print("생성:", fn())
    return 0


if __name__ == "__main__":
    sys.exit(main())
