# -*- coding: utf-8 -*-
"""4단계-2: 수요 3벌 감도 · 배분효율 편익 환산 · 계통 정합 소수 구역 시제품.

(1) 수요지도 3벌(ⓐ무보정·ⓑ규율보정·ⓒ플래그제외) 가중의 σ 감도 + 2구역
    (수도권/비수도권) σ — 규칙 ①·여유용량 신호.
(2) 배분효율 편익 환산(탐색적): 모형 원고 6절의 2구역 예시 시나리오 편익
    B₂ = 140억 원/년(λ 15원·탄력성 0.2·반영률 0.3)을 σ 비율로 스케일 —
    B(Z) = B₂ × σ(Z)/σ(2구역). 하버거 근사에서 편익 ∝ 보존 분산이라는
    성질만 쓰는 환산이며 절대 수준은 예시 모수에 종속.
(3) 계통 정합 소수 구역 시제품: 변전소 권역(벽돌)을 인접 제약(변전소 kNN
    그래프, k=8) 하의 Ward 병합으로 K개 구역까지 묶으며 σ(K) 기록 —
    구역 수를 정부안 수준으로 유지하면서 얻을 수 있는 σ의 실증.
    좌표는 출력하지 않는다(구역별 수요 비중만 공개, 벽돌→구역은 internal).

출력: out/public/s4_welfare_scale.csv, out/public/s4_cluster_sigma.csv,
      out/internal/s4_brick_cluster.csv
"""
from __future__ import annotations

import csv
import gzip
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s2_zones_nearest import GRID, load_substations  # noqa: E402
from s2_sigma import ADMIN, sigma, zone_margin  # noqa: E402
from s2_sigma_gov import build_gov_map  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUB = os.path.join(REPO, "out", "public")
INT = os.path.join(REPO, "out", "internal")
B2_EOK = 140.0   # 2구역 예시 시나리오 배분효율 편익(억 원/년) — 모형 원고 식 (19)
KNN = 8
K_REPORT = (2, 4, 6, 8, 10, 12, 17, 30, 50, 100)


def main() -> int:
    # 셀: 수요 3벌 + 규칙① 권역 + 시군구
    gx, gy, wa, wb, wc = [], [], [], [], []
    with gzip.open(GRID, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            gx.append(int(row[0])); gy.append(int(row[1]))
            wa.append(float(row[2])); wb.append(float(row[3])); wc.append(float(row[4]))
    wa, wb, wc = np.array(wa), np.array(wb), np.array(wc)

    def load_map(path):
        d = {}
        with gzip.open(path, "rt", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r)
            for x, y, v in r:
                d[(int(x), int(y))] = v
        return np.array([d.get((x, y), "") for x, y in zip(gx, gy)])

    zone = load_map(os.path.join(INT, "s2_cell_zone_nearest.csv.gz"))
    sgg = load_map(ADMIN)
    sido = np.array([c[:2] if c else "" for c in sgg])
    gov_map = build_gov_map()
    govk = np.array([gov_map.get(c, "") for c in sgg])
    two = np.array(["수도권" if g.startswith("수도권") else ("비수도권" if g else "")
                    for g in govk])

    zm = zone_margin()
    c = np.array([zm.get(z, (np.nan,))[0] for z in zone])
    has = ~np.isnan(c)

    # (1) 수요 3벌 감도
    zonings = (("2구역", two), ("정부안4광역권", govk), ("시도", sido),
               ("시군구", sgg), ("변전소권역", zone))
    sig = {}
    rows1 = []
    for wn, w in (("ⓐ무보정", wa), ("ⓑ규율보정", wb), ("ⓒ플래그제외", wc)):
        for zn, zk in zonings:
            s, _ = sigma(c, zk, w, has & (zk != "") & (w > 0))
            sig[(wn, zn)] = s
            rows1.append(["σ_수요3벌", wn, zn, f"{s:.3f}"])
    print("σ (규칙①·여유용량) 수요 3벌:")
    for zn, _ in zonings:
        print(f"  {zn:<10}", "  ".join(f"{sig[(wn, zn)]:.3f}"
                                      for wn in ("ⓐ무보정", "ⓑ규율보정", "ⓒ플래그제외")))

    # (2) 배분효율 편익 환산 (본안 = ⓑ)
    s2 = sig[("ⓑ규율보정", "2구역")]
    rows2 = []
    print(f"\n배분효율 편익 환산 (B₂={B2_EOK:.0f}억, σ(2구역)={s2:.3f} → 완전 신호 "
          f"{B2_EOK / s2:,.0f}억):")
    for zn, _ in zonings:
        b = B2_EOK * sig[("ⓑ규율보정", zn)] / s2
        rows2.append(["배분효율편익_억원", "ⓑ규율보정", zn, f"{b:.0f}"])
        print(f"  {zn:<10} σ {sig[('ⓑ규율보정', zn)]:.3f} → {b:,.0f}억 원/년")
    rows2.append(["완전신호편익_억원", "ⓑ규율보정", "-", f"{B2_EOK / s2:.0f}"])

    with open(os.path.join(PUB, "s4_welfare_scale.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        f.write("# σ = 규칙①·여유용량 신호. 편익 = 2구역 예시 시나리오 140억 원/년을 "
                "σ 비율로 스케일한 탐색적 환산(예시 모수 종속)\n")
        w = csv.writer(f)
        w.writerow(["지표", "수요가중", "획정", "값"])
        w.writerows(rows1 + rows2)

    # (3) 벽돌 군집화 — 신호 보유 벽돌만
    agg = defaultdict(float)
    for z, wi, h in zip(zone, wb, has):
        if h and wi > 0:
            agg[z] += wi
    subs = {s[0]: (s[2], s[3]) for s in load_substations()}
    ids = sorted(agg)
    W = np.array([agg[z] for z in ids])
    M = np.array([zm[z][0] for z in ids])
    XY = np.array([subs[z] for z in ids])
    n = len(ids)
    gm = (W * M).sum() / W.sum()
    tot = (W * (M - gm) ** 2).sum()

    d2 = ((XY[:, None, :] - XY[None, :, :]) ** 2).sum(axis=2)
    np.fill_diagonal(d2, np.inf)
    nbr = np.argsort(d2, axis=1)[:, :KNN]
    adj = defaultdict(set)
    for i in range(n):
        for j in nbr[i]:
            adj[i].add(int(j)); adj[int(j)].add(i)

    cw = {i: W[i] for i in range(n)}
    cm = {i: M[i] for i in range(n)}
    member = {i: [i] for i in range(n)}
    within = 0.0
    snap = {}
    k = n
    while k > 2:
        best, pair = None, None
        for a in adj:
            for b in adj[a]:
                if a < b:
                    cost = cw[a] * cw[b] / (cw[a] + cw[b]) * (cm[a] - cm[b]) ** 2
                    if best is None or cost < best:
                        best, pair = cost, (a, b)
        if pair is None:
            break  # 더 병합할 인접 쌍 없음(분리 성분)
        a, b = pair
        within += best
        cm[a] = (cw[a] * cm[a] + cw[b] * cm[b]) / (cw[a] + cw[b])
        cw[a] += cw[b]
        member[a] += member.pop(b)
        for x in adj.pop(b):
            adj[x].discard(b)
            if x != a:
                adj[x].add(a); adj[a].add(x)
        adj[a].discard(a)
        del cw[b], cm[b]
        k -= 1
        if k in K_REPORT:
            shares = sorted((100 * v / W.sum() for v in cw.values()), reverse=True)
            snap[k] = (1 - within / tot, shares, {i: list(m) for i, m in member.items()})

    rows3 = []
    print(f"\n계통 정합 소수 구역 시제품 (신호 보유 벽돌 {n}개, 인접 kNN={KNN}):")
    for kk in sorted(snap):
        s, shares, _ = snap[kk]
        rows3.append([kk, f"{s:.3f}", f"{shares[0]:.1f}", f"{shares[-1]:.1f}"])
        print(f"  K={kk:<4} σ {s:.3f} · 최대 구역 수요 {shares[0]:.1f}% · 최소 {shares[-1]:.1f}%")
    with open(os.path.join(PUB, "s4_cluster_sigma.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        f.write("# 변전소 권역(벽돌)의 인접 제약 Ward 병합 — 규칙①·여유용량 신호·"
                "건물계 ⓑ 가중. 비교: 정부안 4광역권 σ, 시도 σ는 s4_welfare_scale.csv\n")
        w = csv.writer(f)
        w.writerow(["구역수_K", "sigma", "최대구역_수요비중_pct", "최소구역_수요비중_pct"])
        w.writerows(rows3)
    with open(os.path.join(INT, "s4_brick_cluster.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["K", "cluster", "zone_id"])
        for kk in (4, 8, 12, 17):
            if kk in snap:
                for ci, (_root, mem) in enumerate(snap[kk][2].items()):
                    for i in mem:
                        w.writerow([kk, ci, ids[i]])
    print("저장: s4_welfare_scale.csv, s4_cluster_sigma.csv (+internal s4_brick_cluster.csv)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
