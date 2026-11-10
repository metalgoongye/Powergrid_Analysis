# -*- coding: utf-8 -*-
"""2단계-4: 배정규칙 ③(용량제약 균형배정)·④(경계 무작위화) + σ(Z) 구간 완성.

규칙 ③ — 용량제약 균형배정(스트레스 상한):
  한전 공개 '여유용량'은 신규 접속 여유지 총 공급능력이 아니므로 기존 수요
  재배정의 제약으로 쓸 수 없다(쓰면 자기 기준선이 항상 가능해 제약이 안 묶임).
  → 제약은 균등용량 상한 cap = CAP_MULT × (전국 수요/변전소 수)로 두고,
  후회값(2순위−1순위 거리) 내림차순 그리디로 '여유 있는 최근접'에 배정.
  수요 집중이 금지될 때 σ가 어디까지 움직이는지의 스트레스 경계.
  겸행 — 수급 정합 점검: 수요 상위 권역의 한전 여유용량 0(포화) 비율 보고.

규칙 ④ — 경계 무작위화:
  1·2순위 변전소가 거의 등거리(차이 ≤ EPS_M)인 경계 셀을 복제 시행마다
  둘 중 무작위 배정(나머지는 규칙 ①). N_REP회 반복 → σ의 흔들림(최소~최대).

σ는 s2_sigma.sigma()를 재사용 — 신호 A1(여유 2026)·B(공급 거리, 평면),
획정 Z = 전국 단일·시도·시군구·변전소 권역.

출력: out/public/s2_sigma34.csv (rule, signal, zoning, sigma, sigma_min, sigma_max)
      out/public/s2_cap_consistency.csv (수요 상위 권역 × 포화 여부)
      out/internal/s2_cell_zone_cap.csv.gz (규칙③ 셀→권역)
"""
from __future__ import annotations

import csv
import gzip
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s2_zones_nearest import load_substations  # noqa: E402
from s2_sigma import CELL, cell_admin, load_cells, sigma, zone_margin  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "out", "public", "s2_sigma34.csv")
OUT_CAP = os.path.join(REPO, "out", "public", "s2_cap_consistency.csv")
OUT_INT = os.path.join(REPO, "out", "internal", "s2_cell_zone_cap.csv.gz")

K = 12            # 셀별 후보 변전소 수(최근접순)
CAP_MULT = 2.0    # 균등용량 상한 = 평균 부하 × 배수
EPS_M = 200.0     # 규칙④ 경계 판정: 1·2순위 거리 차(m)
N_REP = 20
SEED = 20261110
ODBL = "# 원천: OpenInfraMap/OSM KOR.gpkg (ODbL) + 한전ON 공개자료 파생"


def topk(cx, cy, sx, sy, k=K, chunk=20000):
    """셀별 최근접 k개 변전소 인덱스·거리."""
    n = len(cx)
    idx = np.empty((n, k), dtype=np.int32)
    dst = np.empty((n, k))
    for s in range(0, n, chunk):
        dx = cx[s:s + chunk, None] - sx[None, :]
        dy = cy[s:s + chunk, None] - sy[None, :]
        d2 = dx * dx + dy * dy
        part = np.argpartition(d2, k, axis=1)[:, :k]
        rows = np.arange(part.shape[0])[:, None]
        order = np.argsort(d2[rows, part], axis=1)
        sel = part[rows, order]
        idx[s:s + chunk] = sel
        dst[s:s + chunk] = np.sqrt(d2[rows, sel])
    return idx, dst


def main() -> int:
    gx, gy, w = load_cells()
    cx = (gx + 0.5) * CELL
    cy = (gy + 0.5) * CELL
    subs = load_substations()
    sx = np.array([s[2] for s in subs])
    sy = np.array([s[3] for s in subs])
    zid = np.array([s[0] for s in subs])
    print(f"셀 {len(gx):,} · 변전소 {len(subs)}")

    print("셀별 최근접 K 변전소…")
    nidx, ndst = topk(cx, cy, sx, sy)

    # ---- 규칙 ③: 균등용량 상한 그리디 ----
    cap = CAP_MULT * w.sum() / len(subs)
    print(f"규칙③ cap = {cap / 1e6:,.1f} GWh (평균 부하 × {CAP_MULT})")
    regret = ndst[:, 1] - ndst[:, 0]
    order = np.argsort(-regret)
    rem = np.full(len(subs), cap)
    a3 = np.full(len(gx), -1, dtype=np.int32)
    n_over = 0
    for i in order:
        for j, s_ in zip(nidx[i], ndst[i]):
            if rem[j] >= w[i]:
                a3[i] = j
                rem[j] -= w[i]
                break
        else:
            a3[i] = nidx[i, 0]  # K개 모두 포화 — 최근접 초과 배정
            rem[nidx[i, 0]] -= w[i]
            n_over += 1
    moved = a3 != nidx[:, 0]
    print(f"  규칙① 대비 이동 {int(moved.sum()):,}셀 "
          f"({100 * w[moved].sum() / w.sum():.1f}% 수요) · 한도 초과 배정 {n_over:,}셀")
    d3 = np.hypot(cx - sx[a3], cy - sy[a3])
    zone3 = zid[a3]

    # ---- 수급 정합 점검: 수요 상위 권역 × 한전 여유 0 ----
    zm = zone_margin()
    agg1 = defaultdict(float)
    for j, wi in zip(nidx[:, 0], w):
        agg1[zid[j]] += wi
    top = sorted(agg1.items(), key=lambda kv: -kv[1])
    rows_cap = []
    for rank, (z, dem) in enumerate(top[:50], 1):
        m = zm.get(z)
        rows_cap.append([rank, z, f"{dem / 1e6:.1f}",
                         "" if m is None else f"{m[0]:.0f}",
                         "" if m is None else ("포화" if m[0] == 0 else "여유")])
    n_sat = sum(1 for r in rows_cap if r[4] == "포화")
    n_known = sum(1 for r in rows_cap if r[4])
    print(f"  정합 점검: 수요 상위 50권역 중 여유 매칭 {n_known} · 포화 {n_sat}")

    # ---- σ 재료 ----
    sgg = cell_admin(gx, gy, cx, cy)
    sido = np.array([s[:2] if s else "" for s in sgg])
    nation = np.full(len(gx), "KR")
    zonings = (("전국단일", nation), ("시도", sido), ("시군구", sgg))

    def sig_rows(rule, zone, dist):
        a1v = np.array([zm.get(z, (np.nan,))[0] for z in zone])
        has = ~np.isnan(a1v)
        out = []
        for sig_name, sig, mask in (("A1_여유2026", a1v, has),
                                    ("B_공급거리", dist, np.ones(len(w), bool))):
            for zn, zk in zonings + (("변전소권역", zone),):
                m = mask & (zk != "")
                s_, _cov = sigma(sig, zk, w, m)
                out.append((rule, sig_name, zn, s_))
        return out

    rows = [(r, sg, zn, f"{s_:.3f}", "", "")
            for r, sg, zn, s_ in sig_rows("③용량제약", zone3, d3)]

    # ---- 규칙 ④: 경계 무작위화 ----
    near = (ndst[:, 1] - ndst[:, 0]) <= EPS_M
    print(f"규칙④ 경계 셀(1·2순위 차 ≤{EPS_M:.0f}m): {int(near.sum()):,} "
          f"({100 * w[near].sum() / w.sum():.1f}% 수요)")
    rng = np.random.default_rng(SEED)
    acc = defaultdict(list)
    for rep in range(N_REP):
        pick = rng.integers(0, 2, size=int(near.sum()))
        aj = nidx[:, 0].copy()
        aj[near] = nidx[near, pick]
        zone4 = zid[aj]
        d4 = np.hypot(cx - sx[aj], cy - sy[aj])
        for r, sg, zn, s_ in sig_rows("④무작위화", zone4, d4):
            acc[(sg, zn)].append(s_)
    for (sg, zn), vals in acc.items():
        v = np.array(vals)
        rows.append(("④무작위화", sg, zn, f"{v.mean():.3f}",
                     f"{v.min():.3f}", f"{v.max():.3f}"))

    # ---- 저장 ----
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        f.write(ODBL + "\n")
        wtr = csv.writer(f)
        wtr.writerow(["rule", "signal", "zoning", "sigma", "sigma_min", "sigma_max"])
        wtr.writerows(rows)
    with open(OUT_CAP, "w", encoding="utf-8-sig", newline="") as f:
        f.write(ODBL + "\n")
        wtr = csv.writer(f)
        wtr.writerow(["수요순위", "zone_id", "GWh_adj", "여유2026_MW", "판정"])
        wtr.writerows(rows_cap)
    with gzip.open(OUT_INT, "wt", encoding="utf-8", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["gx", "gy", "zone_id"])
        for x, y, z in zip(gx, gy, zone3):
            wtr.writerow([x, y, z])

    print(f"\n{'규칙':<10}{'신호':<14}{'획정':<8}{'σ':>8}{'범위':>16}")
    for r in rows:
        rng_s = f"[{r[4]}, {r[5]}]" if r[4] else ""
        print(f"{r[0]:<10}{r[1]:<14}{r[2]:<8}{r[3]:>8}{rng_s:>16}")
    print("저장:", OUT, OUT_CAP, OUT_INT, sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
