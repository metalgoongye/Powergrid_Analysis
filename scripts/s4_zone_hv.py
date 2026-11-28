# -*- coding: utf-8 -*-
"""4단계-5: 상위 전압(345kV 이상) 변전소로 묶은 구역 — 점검용.

물음: 변전소 권역(154kV 이상 845개소)을 그대로 쓰면 구역이 너무 많다. 한 단계 위인
345kV(또는 765kV) 변전소를 중심으로 권역을 묶으면 구역 수가 줄면서도 신호를 잘 담는가?

설계
  재료   = 변전소 권역(규칙 ① 배정) — s4_zone_alt.py와 같은 셀·같은 신호
  허브   = OSM max_voltage ≥ 345kV 변전소 / 765kV 변전소
  묶는 법 = 각 변전소를 가장 가까운 허브에 붙임. ⓐ 직선거리 ⓑ 공개 선로망을 따라 잰 거리
           (실제 154kV-345kV 연결 관계는 공개되지 않아 근사임)
  기준선 = 845개소에서 허브와 같은 수를 무작위로 뽑아 ⓐ와 같은 방법으로 만든 구역(200회)
           — 상위 전압 변전소라는 점이 '같은 수의 임의 중심'보다 신호를 더 담는지 본다
평가 — 구역(지리)은 규칙 ① 권역으로 고정하고 신호만 배정 규칙·연도별로 바꾼다(s4_zone_alt와 같음).

검산(불일치 시 중단): 시도·시군구의 σ = s2_sigma.csv.
출력: out/public/s4_zone_hv.csv            σ·소수 상태·구역 크기(집계)
      out/internal/s4_zone_hv_brick.csv    변전소→허브(내부)
"""
from __future__ import annotations

import csv
import gzip
import heapq
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s2_zones_nearest import GPKG, load_substations  # noqa: E402
from s2_zones_network import CELL, build_graph  # noqa: E402
from s2_sigma import cell_admin, load_cells  # noqa: E402
from s2_sigma_gov import build_gov_map  # noqa: E402
from s4_zone_alt import INT, PUB, RULES, YEARS, check, margin_years, mis_rate, read_pub, sigma  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HUBS = (("345kV이상", 345000.0), ("765kV", 765000.0))
N_RAND, SEED = 200, 20261128


def nearest_hub(sxy, hub_idx):
    """각 변전소 → 직선거리로 가장 가까운 허브(변전소 색인)."""
    d2 = ((sxy[:, None, :] - sxy[hub_idx][None, :, :]) ** 2).sum(axis=2)
    return hub_idx[np.argmin(d2, axis=1)]


def network_hub(sxy, hub_idx, graph):
    """각 변전소 → 선로망을 따라 가장 가까운 허브. 망으로 닿지 않으면 직선거리로 대신한다."""
    coords, adj, snap_node, snap_d = graph
    n_node = len(coords)
    N = n_node + len(sxy)
    dist = np.full(N, np.inf)
    label = np.full(N, -1, dtype=np.int64)
    pq = []
    for h in hub_idx:
        dist[n_node + h] = 0.0
        label[n_node + h] = h
        pq.append((0.0, n_node + int(h)))
    heapq.heapify(pq)
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        for v, w in adj[u]:
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                label[v] = label[u]
                heapq.heappush(pq, (nd, v))
    lab = label[n_node:].copy()
    miss = lab < 0
    if miss.any():
        lab[miss] = nearest_hub(sxy, hub_idx)[miss]
    return lab, int(miss.sum())


def main() -> int:
    gx, gy, w = load_cells()
    cx, cy = (gx + 0.5) * CELL, (gy + 0.5) * CELL
    subs = load_substations()
    ids = [s[0] for s in subs]
    idx = {z: i for i, z in enumerate(ids)}
    sxy = np.array([[s[2], s[3]] for s in subs])

    db = sqlite3.connect(GPKG)
    volt = {}
    for layer, tag in (("power_substation_polygon", "G"), ("power_substation_point", "P")):
        for fid, v in db.execute(f"SELECT fid, max_voltage FROM {layer} WHERE max_voltage >= 154000"):
            volt[f"{tag}{fid}"] = float(v)
    db.close()
    sv = np.array([volt[z] for z in ids])
    print("전압별 변전소 수:", {int(v / 1000): int((sv == v).sum()) for v in sorted(set(sv))})

    def load_zone(name):
        d = {}
        with gzip.open(os.path.join(INT, name), "rt", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r)
            for x, y, z in r:
                d[(int(x), int(y))] = z
        return np.array([idx[d[(x, y)]] for x, y in zip(gx, gy)])

    zr = {rn: load_zone(fn) for rn, fn in RULES}
    z1 = zr["①최근접"]
    sgg = cell_admin(gx, gy, cx, cy)
    sido = np.array([c[:2] if c else "" for c in sgg])
    gov_map = build_gov_map()
    gov = np.array([gov_map.get(c, "") for c in sgg])
    adm_ok = sgg != ""
    M = margin_years(ids)
    has_b = ~np.isnan(M[:, 0])
    dist = {rn: np.hypot(cx - sxy[zr[rn], 0], cy - sxy[zr[rn], 1]) for rn in ("①최근접", "③용량제약")}

    print("검산:")
    ref_s = {(r[0], r[1], r[2]): r[3] for r in read_pub("s2_sigma.csv") if len(r) == 5}
    m1 = has_b[z1] & adm_ok
    for zn, zk in (("시도", sido), ("시군구", sgg)):
        check(f"{zn} MW2026 ①", sigma(M[z1[m1], 0], zk[m1], w[m1]), ref_s[("A1_여유2026", "①최근접", zn)])
        check(f"{zn} 거리 ①", sigma(dist["①최근접"][adm_ok], zk[adm_ok], w[adm_ok]), ref_s[("B_공급거리", "①최근접", zn)])

    # 허브 권역의 수요(규칙 ①): 상위 전압 변전소가 직접 맡은 격자 수요의 비중
    wz = np.bincount(z1, w, minlength=len(ids))
    for hn, vmin in HUBS:
        print(f"  {hn} 변전소 {int((sv >= vmin).sum())}곳이 규칙 ①에서 직접 맡은 수요 비중 {100 * wz[sv >= vmin].sum() / wz.sum():.1f}%")

    # 선로망(한 번만 구성) — 변전소를 가장 가까운 노드에 접속
    coords, adj = build_graph()
    n_node = len(coords)
    for i in range(len(ids)):
        d = np.hypot(coords[:, 0] - sxy[i, 0], coords[:, 1] - sxy[i, 1])
        j = int(d.argmin())
        adj[n_node + i].append((j, float(d[j])))
        adj[j].append((n_node + i, float(d[j])))
    graph = (coords, adj, None, None)

    def evaluate(zname, how, lab):
        """lab = 변전소→구역 표지. 규칙 ①②③의 신호로 평가한 행들을 돌려준다."""
        key_all = lab[z1]
        _, inv = np.unique(key_all[adm_ok], return_inverse=True)
        zw = np.bincount(inv, w[adm_ok])
        rows = []
        for rn, _fn in RULES:
            mr = has_b[zr[rn]] & adm_ok
            C = M[zr[rn][mr]]
            S = (C == 0).astype(float)
            key, ww = key_all[mr], w[mr]
            rows.append([zname, how, rn, len(zw),
                         f"{sigma(C[:, 0], key, ww):.3f}", f"{sigma(C[:, 1], key, ww):.3f}",
                         f"{sigma(C[:, 2:].mean(axis=1), key, ww):.3f}", f"{sigma(C.mean(axis=1), key, ww):.3f}",
                         f"{mis_rate(S[:, 0], key, ww):.1f}",
                         f"{sigma(dist[rn][adm_ok], key_all[adm_ok], w[adm_ok]):.3f}" if rn in dist else "",
                         f"{100 * zw.max() / zw.sum():.1f}", f"{100 * np.median(zw) / zw.sum():.2f}"])
        return rows

    out = []
    for zn, zk in (("4광역권(예시)", gov), ("시도", sido), ("시군구", sgg)):
        # 행정 단위는 셀 단위 표지이므로 따로 계산(같은 열 구성)
        _, inv = np.unique(zk[adm_ok], return_inverse=True)
        zw = np.bincount(inv, w[adm_ok])
        for rn, _fn in RULES:
            mr = has_b[zr[rn]] & adm_ok
            C = M[zr[rn][mr]]
            S = (C == 0).astype(float)
            key, ww = zk[mr], w[mr]
            out.append([zn, "", rn, len(zw), f"{sigma(C[:, 0], key, ww):.3f}", f"{sigma(C[:, 1], key, ww):.3f}",
                        f"{sigma(C[:, 2:].mean(axis=1), key, ww):.3f}", f"{sigma(C.mean(axis=1), key, ww):.3f}",
                        f"{mis_rate(S[:, 0], key, ww):.1f}",
                        f"{sigma(dist[rn][adm_ok], zk[adm_ok], w[adm_ok]):.3f}" if rn in dist else "",
                        f"{100 * zw.max() / zw.sum():.1f}", f"{100 * np.median(zw) / zw.sum():.2f}"])

    brick = []
    rng = np.random.default_rng(SEED)
    base_rows = []
    for hn, vmin in HUBS:
        hub_idx = np.flatnonzero(sv >= vmin)
        lab_a = nearest_hub(sxy, hub_idx)
        lab_b, n_miss = network_hub(sxy, hub_idx, graph)
        print(f"{hn}: 허브 {len(hub_idx)}곳 · 선로망으로 닿지 않아 직선거리로 대신한 변전소 {n_miss}곳 · "
              f"두 방법의 허브가 다른 변전소 {int((lab_a != lab_b).sum())}곳")
        out += evaluate(f"{hn} 묶음", "직선거리", lab_a)
        out += evaluate(f"{hn} 묶음", "선로망거리", lab_b)
        for i, z in enumerate(ids):
            brick.append([hn, z, ids[lab_a[i]], ids[lab_b[i]]])
        # 기준선: 같은 수의 임의 중심(직선거리)
        acc = []
        for _ in range(N_RAND):
            pick = np.sort(rng.choice(len(ids), size=len(hub_idx), replace=False))
            r1 = evaluate("임의", "직선거리", nearest_hub(sxy, pick))[0]      # 규칙 ①
            acc.append([float(r1[4]), float(r1[6]), float(r1[8]), float(r1[9])])
        acc = np.array(acc)
        act = [r for r in out if r[0] == f"{hn} 묶음" and r[1] == "직선거리" and r[2] == "①최근접"][0]
        av = [float(act[4]), float(act[6]), float(act[8]), float(act[9])]
        for j, nm in enumerate(("σ_MW2026", "σ_MW2028-32평균", "소수상태_2026_pct", "σ_공급거리")):
            base_rows.append([hn, len(hub_idx), nm, f"{av[j]:.3f}", f"{acc[:, j].mean():.3f}",
                              f"{np.percentile(acc[:, j], 5):.3f}", f"{np.percentile(acc[:, j], 95):.3f}",
                              f"{100 * (acc[:, j] < av[j]).mean():.0f}"])

    head = ["획정", "묶는법", "신호배정규칙", "구역수", "σ_MW2026", "σ_MW2027", "σ_MW2028-32평균", "σ_MW2026-32평균",
            "소수상태_2026_pct", "σ_공급거리(전셀)", "최대구역_수요비중_pct", "중위구역_수요비중_pct"]
    with open(os.path.join(PUB, "s4_zone_hv.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 상위 전압 변전소로 묶은 구역(점검용) — 변전소 권역(규칙 ①)을 가장 가까운 345kV 이상(또는 765kV) 변전소에 붙여 만든 구역. "
                "실제 154kV-345kV 연결 관계는 공개되지 않아 직선거리·선로망 거리로 근사. 여유용량 신호는 신호 보유 셀, 공급 거리와 구역 크기는 전 셀, "
                "가중 = 주택·일반용(ⓑ). 원천: OpenInfraMap/OSM KOR.gpkg(ODbL) + 한전ON 공개자료 파생\n")
        wtr = csv.writer(f)
        wtr.writerow(head)
        wtr.writerows(out)
        f.write(f"# 기준선: 845개소에서 허브와 같은 수를 무작위로 뽑아 직선거리로 묶은 구역 {N_RAND}회(규칙 ①, seed {SEED})\n")
        wtr.writerow(["허브", "구역중심수", "지표", "상위전압묶음", "임의중심_평균", "임의중심_5%", "임의중심_95%", "임의중심보다_큰_비율_pct"])
        wtr.writerows(base_rows)
    with open(os.path.join(INT, "s4_zone_hv_brick.csv"), "w", encoding="utf-8-sig", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["허브", "zone_id", "허브_직선거리", "허브_선로망거리"])
        wtr.writerows(brick)

    print(f"\n{'획정':<16}{'묶는법':<8}{'규칙':<8}{'K':>5}{'MW26':>7}{'MW27':>7}{'MW28-32':>9}{'소수26':>7}{'거리':>7}{'최대%':>7}{'중위%':>7}")
    for r in out:
        print(f"{r[0]:<16}{r[1]:<8}{r[2]:<8}{r[3]:>5}{r[4]:>7}{r[5]:>7}{r[6]:>9}{r[8]:>7}{r[9]:>7}{r[10]:>7}{r[11]:>7}")
    print("\n기준선(임의 중심, 규칙 ①):")
    for r in base_rows:
        print("  ", r)
    print("저장: s4_zone_hv.csv (+internal s4_zone_hv_brick.csv)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
