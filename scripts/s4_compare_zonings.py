# -*- coding: utf-8 -*-
"""4단계-1: 획정 대안 종합 비교 — σ·효율 지수·격차 전달률·구역 수 (6장 9절).

다섯 획정(전국 단일·정부안 4광역권·시도·시군구·변전소 권역)을 세 축으로 비교:
  효율   = σ(Z) (기산출 CSV 3종에서 수집) 및 효율 지수(완전 신호=100)
  단순성 = 구역 수(수요 보유 기준)
  분배   = 두 지표.
    (a) 수도권-비수도권 집계 격차: 정부안·시도·시군구가 모두 수도권 경계에
        중첩(nested)이라 구역 평균화가 집계 격차를 바꾸지 못함 — 어떤 획정
        이든 동일하게 전달(수학적 불변). 이 불변성 자체를 보고.
    (b) 오신호율: 신호를 포화(여유 0)/여유(>0)로 이분할 때, 자기 구역의
        수요 가중 다수 신호와 '부호가 반대'인 수요 비중 — 포화 변전소
        수요인데 구역 요금은 여유 신호(또는 그 반대). 벽돌 = 0%.

출력: out/public/s4_zoning_compare.csv
"""
from __future__ import annotations

import csv
import gzip
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s2_sigma import CELL, cell_admin, load_cells, zone_margin  # noqa: E402
from s2_sigma_gov import SIDO_DBF, build_gov_map  # noqa: E402
from s3_osm_groundtruth import read_dbf_names  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INT = os.path.join(REPO, "out", "internal")
PUB = os.path.join(REPO, "out", "public")
OUT = os.path.join(PUB, "s4_zoning_compare.csv")


def sigma_ranges():
    """기산출 σ CSV 3종 → zoning별 (A1 구간, B 구간)."""
    acc = defaultdict(lambda: defaultdict(list))  # zoning → signal → [σ]
    for name in ("s2_sigma.csv", "s2_sigma34.csv", "s2_sigma_gov.csv"):
        p = os.path.join(PUB, name)
        with open(p, encoding="utf-8-sig") as f:
            rows = csv.DictReader(ln for ln in f if not ln.startswith("#"))
            for r in rows:
                sg, zn, s = r.get("signal"), r.get("zoning"), r.get("sigma")
                if name == "s2_sigma34.csv":
                    sg, zn, s = r.get("signal"), r.get("zoning"), r.get("sigma")
                if not (sg and zn and s):
                    continue
                if sg.startswith("A2"):
                    continue  # 구간은 A1·B 기준(본문 표기와 일치)
                try:
                    acc[zn][sg[0]].append(float(s))
                except ValueError:
                    pass
    return acc


def main() -> int:
    gx, gy, w = load_cells()
    cx = (gx + 0.5) * CELL
    cy = (gy + 0.5) * CELL
    sgg = cell_admin(gx, gy, cx, cy)
    gov_map = build_gov_map()
    govk = np.array([gov_map.get(c, "") for c in sgg])
    sido_cd = np.array([c[:2] if c else "" for c in sgg])

    # 수도권 = 서울·인천·경기 (SGIS 시도 사전의 이름으로 판별)
    sido_rows = read_dbf_names(SIDO_DBF)
    cd_f = next(k for k in sido_rows[0] if k.endswith("_CD"))
    nm_f = next(k for k in sido_rows[0] if k.endswith("_NM"))
    cap_cds = {r[cd_f] for r in sido_rows
               if r[nm_f].startswith(("서울", "인천", "경기"))}
    is_cap = np.isin(sido_cd, list(cap_cds))

    # 셀 신호: 규칙 ① 배정 변전소의 여유용량(2026)
    z1 = {}
    with gzip.open(os.path.join(INT, "s2_cell_zone_nearest.csv.gz"),
                   "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for x, y, z in r:
            z1[(int(x), int(y))] = z
    zone1 = np.array([z1[(x, y)] for x, y in zip(gx, gy)])
    zm = zone_margin()
    c = np.array([zm.get(z, (np.nan,))[0] for z in zone1])
    has = ~np.isnan(c) & (sgg != "")

    sat = c == 0  # 포화 벽돌 신호

    def agg_gap(zkey):
        """구역 평균화 후 수도권-비수도권 집계 격차(MW) — 중첩이면 불변."""
        m = has & (zkey != "")
        agg = defaultdict(lambda: [0.0, 0.0])
        for k, ci, wi in zip(zkey[m], c[m], w[m]):
            a = agg[k]
            a[0] += wi
            a[1] += ci * wi
        mean = {k: s / wt for k, (wt, s) in agg.items()}
        mz = np.array([mean.get(k, np.nan) for k in zkey])
        mm = m & ~np.isnan(mz)
        wc = w[mm & is_cap]
        wn = w[mm & ~is_cap]
        return float((mz[mm & ~is_cap] * wn).sum() / wn.sum()
                     - (mz[mm & is_cap] * wc).sum() / wc.sum())

    def mis_rate(zkey):
        """오신호율: 구역의 수요 가중 다수 신호(포화/여유)와 부호가 반대인
        수요 비중(%). 신호 보유 셀 기준."""
        m = has & (zkey != "")
        agg = defaultdict(lambda: [0.0, 0.0])
        for k, si, wi in zip(zkey[m], sat[m], w[m]):
            a = agg[k]
            a[0] += wi
            a[1] += si * wi
        rho = {k: s / wt for k, (wt, s) in agg.items()}  # 구역 포화 수요 비중
        zmaj = np.array([rho.get(k, np.nan) >= 0.5 for k in zkey])
        mism = m & (sat != zmaj)
        return 100.0 * w[mism].sum() / w[m].sum()

    nation = np.full(len(gx), "KR")
    zonings = [("전국단일", nation), ("정부안4광역권", govk),
               ("시도", sido_cd), ("시군구", sgg), ("변전소권역", zone1)]
    D = {name: agg_gap(zk) for name, zk in zonings}
    M = {name: mis_rate(zk) for name, zk in zonings}
    sat_share = 100.0 * w[has & sat].sum() / w[has].sum()
    print("포화 변전소 수요 비중(신호 보유 셀):", f"{sat_share:.1f}%")
    print("수도권-비수도권 집계 격차(MW):",
          {k: f"{v:.2f}" for k, v in D.items()},
          "— 중첩 획정에서 불변(수학적 성질)")

    nz = {"전국단일": 1, "정부안4광역권": 4, "시도": 17,
          "시군구": len({s for s in sgg if s}),
          "변전소권역": len({z for z in zone1})}
    sig = sigma_ranges()
    key_map = {"전국단일": "전국단일", "정부안4광역권": "정부안4광역권",
               "시도": "시도", "시군구": "시군구", "변전소권역": "변전소권역"}

    rows = []
    for name, _zk in zonings:
        a = sig.get(key_map[name], {}).get("A", [])
        b = sig.get(key_map[name], {}).get("B", [])
        a_rng = f"{min(a):.2f}~{max(a):.2f}" if a else ""
        b_rng = f"{min(b):.2f}~{max(b):.2f}" if b else ""
        eff = f"{100 * (min(a) + max(a)) / 2:.0f}" if a else ""
        rows.append([name, nz[name], a_rng, b_rng, eff,
                     f"{D[name]:.2f}", f"{M[name]:.1f}"])

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        f.write("# σ는 s2_sigma*.csv 기산출 수집(A=여유용량·B=공급거리). "
                "집계격차 = 구역 평균화 후 비수도권−수도권 평균 여유(MW, 중첩 획정 불변). "
                "오신호율 = 구역 다수 신호(포화/여유)와 부호 반대인 수요 비중(%).\n")
        wtr = csv.writer(f)
        wtr.writerow(["획정", "구역수", "σ_A구간", "σ_B구간",
                      "효율지수_A중간(완전신호=100)", "집계격차_MW", "오신호율_pct"])
        wtr.writerows(rows)

    print(f"\n{'획정':<12}{'구역수':>6}{'σ_A':>12}{'σ_B':>12}"
          f"{'효율지수':>8}{'오신호율%':>9}")
    for r in rows:
        print(f"{r[0]:<12}{r[1]:>6}{r[2]:>12}{r[3]:>12}{r[4]:>8}{r[6]:>9}")
    print("저장:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
