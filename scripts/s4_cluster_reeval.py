# -*- coding: utf-8 -*-
"""4단계-3: 변전소 권역 묶음(병합 구역)의 재평가 — 묶을 때 쓰지 않은 신호로.

s4_welfare_cluster.py가 2026년 여유용량(A1)으로 묶은 K=4·8·17 구역을
  A2 = 2026~2032년 평균 여유용량,  B = 공급 거리(규칙 ① 평면거리)
로 다시 평가하고, 소수 상태 수요 비중(포화/여유 이분 — A1 기준)도 구한다.

비교 기준을 맞추려고 행정 단위 획정도 '같은 셀'(여유용량 신호가 있는 권역의
셀, 규칙 ①·ⓑ 가중)만으로 다시 계산한다. 따라서 B 신호의 행정 단위 σ는 전 셀
기준인 s2_sigma.csv 값과 다르다(전 셀 기준 값은 검산에만 쓴다).

검산(불일치 시 중단): 묶음의 A1 σ = s4_cluster_sigma.csv, 행정 단위 A1 σ·전 셀
B σ = s2_sigma.csv·s2_sigma_gov.csv, 행정 단위 소수 상태 = s4_zoning_compare.csv.

출력: out/public/s4_cluster_reeval.csv (구역 단위 집계값만 — 좌표 없음)
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
from s2_zones_network import CELL  # noqa: E402
from s2_sigma import ZONE1, cell_admin, load_cells, sigma, zone_margin  # noqa: E402
from s2_sigma_gov import build_gov_map  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUB = os.path.join(REPO, "out", "public")
INT = os.path.join(REPO, "out", "internal")
OUT = os.path.join(PUB, "s4_cluster_reeval.csv")
KS = (4, 8, 17)
RULE = "①최근접"


def read_pub(name):
    with open(os.path.join(PUB, name), encoding="utf-8-sig") as f:
        return list(csv.reader(ln for ln in f if ln.strip() and not ln.startswith("#")))


def check(label, got, ref, fmt):
    ok = fmt.format(got) == ref
    print(f"  검산 {label}: 계산 {fmt.format(got)} · 기산출 {ref} {'OK' if ok else '불일치'}")
    assert ok, f"검산 불일치 — {label}"


def mis_rate(sat, zkey, w, m):
    """구역의 수요 가중 다수 상태(포화/여유)와 반대 상태인 수요 비중(%)."""
    agg = defaultdict(lambda: [0.0, 0.0])
    for k, si, wi in zip(zkey[m], sat[m], w[m]):
        agg[k][0] += wi
        agg[k][1] += si * wi
    rho = {k: s / wt for k, (wt, s) in agg.items()}
    zmaj = np.array([rho.get(k, np.nan) >= 0.5 for k in zkey])
    return 100.0 * w[m & (sat != zmaj)].sum() / w[m].sum()


def main() -> int:
    gx, gy, w = load_cells()
    cx, cy = (gx + 0.5) * CELL, (gy + 0.5) * CELL

    z1 = {}
    with gzip.open(ZONE1, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for x, y, z in r:
            z1[(int(x), int(y))] = z
    zone = np.array([z1[(x, y)] for x, y in zip(gx, gy)])

    sgg = cell_admin(gx, gy, cx, cy)
    sido = np.array([c[:2] if c else "" for c in sgg])
    gov_map = build_gov_map()
    gov = np.array([gov_map.get(c, "") for c in sgg])

    zm = zone_margin()
    a1 = np.array([zm.get(z, (np.nan, np.nan))[0] for z in zone])
    a2 = np.array([zm.get(z, (np.nan, np.nan))[1] for z in zone])
    sxy = {s[0]: (s[2], s[3]) for s in load_substations()}
    zx = np.array([sxy[z][0] for z in zone])
    zy = np.array([sxy[z][1] for z in zone])
    dist = np.hypot(cx - zx, cy - zy)
    has = ~np.isnan(a1)
    sat = a1 == 0

    # 권역 → 묶음(K별)
    cl = {k: {} for k in KS}
    with open(os.path.join(INT, "s4_brick_cluster.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if int(r["K"]) in cl:
                cl[int(r["K"])][r["zone_id"]] = r["cluster"]
    ck = {k: np.array([cl[k].get(z, "") for z in zone]) for k in KS}

    # ── 검산
    print("검산:")
    ref_cl = {int(r[0]): r[1] for r in read_pub("s4_cluster_sigma.csv")[1:]}
    for k in KS:
        check(f"묶음 K={k} A1 σ", sigma(a1, ck[k], w, has & (ck[k] != ""))[0],
              ref_cl[k], "{:.3f}")
    ref_s = {(r[0], r[1], r[2]): r[3]
             for n in ("s2_sigma.csv", "s2_sigma_gov.csv")
             for r in read_pub(n) if len(r) == 5}
    admin = (("정부안4광역권", gov), ("시도", sido), ("시군구", sgg))
    for zn, zk in admin + (("변전소권역", zone),):
        if zn != "변전소권역":
            check(f"{zn} A1 σ", sigma(a1, zk, w, has & (zk != ""))[0],
                  ref_s[("A1_여유2026", RULE, zn)], "{:.3f}")
        check(f"{zn} B σ(전 셀)", sigma(dist, zk, w, zk != "")[0],
              ref_s[("B_공급거리", RULE, zn)], "{:.3f}")
    ref_m = {r[0]: r[6] for r in read_pub("s4_zoning_compare.csv")[1:]}
    for zn, zk in admin:
        check(f"{zn} 소수 상태", mis_rate(sat, zk, w, has & (zk != "")), ref_m[zn], "{:.1f}")

    # ── 같은 셀에서 다시 평가
    m = has & (sgg != "") & (ck[KS[0]] != "")
    print(f"\n평가 셀: 수요의 {100 * w[m].sum() / w.sum():.1f}%")
    zonings = [(zn, len(set(zk[m])), zk) for zn, zk in
               (("4광역권(예시)", gov), ("시도", sido), ("시군구", sgg), ("변전소권역", zone))]
    zonings += [("권역묶음", k, ck[k]) for k in KS]
    rows = []
    for zn, nz, zk in zonings:
        shares = defaultdict(float)
        for key, wi in zip(zk[m], w[m]):
            shares[key] += wi
        rows.append([zn, nz,
                     f"{sigma(a1, zk, w, m)[0]:.3f}", f"{sigma(a2, zk, w, m)[0]:.3f}",
                     f"{sigma(dist, zk, w, m)[0]:.3f}", f"{mis_rate(sat, zk, w, m):.1f}",
                     f"{100 * max(shares.values()) / w[m].sum():.1f}"])

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 권역묶음 = 2026년 여유용량(A1)으로 묶은 구역(s4_welfare_cluster.py). "
                "A1 σ와 소수 상태는 묶을 때 쓴 신호 기준(표본 내), A2·B는 묶을 때 쓰지 않은 신호. "
                "전 획정을 같은 셀(여유용량 신호 보유 권역, 규칙 ①, ⓑ 가중)로 계산 — "
                "B 신호의 행정 단위 σ는 전 셀 기준인 s2_sigma.csv와 다름\n")
        wtr = csv.writer(f)
        wtr.writerow(["획정", "구역수", "σ_A1_여유2026", "σ_A2_여유26-32평균",
                      "σ_B_공급거리", "소수상태_수요비중_pct", "최대구역_수요비중_pct"])
        wtr.writerows(rows)

    print(f"\n{'획정':<12}{'구역수':>5}{'σ_A1':>8}{'σ_A2':>8}{'σ_B':>8}{'소수%':>7}{'최대구역%':>9}")
    for r in rows:
        print(f"{r[0]:<12}{r[1]:>5}{r[2]:>8}{r[3]:>8}{r[4]:>8}{r[5]:>7}{r[6]:>9}")
    print("저장:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
