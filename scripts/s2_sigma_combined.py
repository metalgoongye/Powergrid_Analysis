# -*- coding: utf-8 -*-
"""2단계-6: 산업용 합산 수요 가중의 σ(Z)·오신호율 감도 — 건물계 vs 합산 두 열.

기산출 σ(s2_sigma*.py)·오신호율(s4_compare_zonings.py)은 건물계 수요(주택+일반)
가중이다. 산업용(286TWh, 합산의 57%, 수도권 32%)을 더한 합산 가중으로 같은
지표를 다시 내 두 열로 보고한다.

- 격자 = 건물계 셀 ∪ 산업용 셀. 가중 w_b(건물계 adj)·w_c(= w_b + 산업용).
- 권역 배정: 규칙 ①(평면 최근접)·②(네트워크 거리) — 합집합 격자에서 재계산.
- 행정 배정: 기존 캐시 + 신규 셀만 PIP(합집합 캐시 별도 저장).
- 신호: A1 = 한전 여유용량 2026, B = 공급 거리(규칙별).
- 획정: 정부안 4광역권·시도·시군구·변전소 권역.
- 오신호율: 규칙 ①·A1, 포화/여유 이분(정의는 s4_compare_zonings와 동일).

출력: out/public/s2_sigma_combined.csv
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
from s2_zones_network import assign_network  # noqa: E402
from s2_sigma import ADMIN, CELL, GRID, SHP, pip_big, sigma, zone_margin  # noqa: E402
from s2_sigma_gov import build_gov_map  # noqa: E402
from s3_osm_groundtruth import read_dbf_names, read_shp_polys, rings_to_edges  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUB = os.path.join(REPO, "out", "public")
IND = os.path.join(PUB, "s1_grid_industrial_2024.csv.gz")
ADMIN_U = os.path.join(REPO, "out", "internal", "s2_cell_admin_union.csv.gz")
OUT = os.path.join(PUB, "s2_sigma_combined.csv")


def load_union():
    wb, wi = defaultdict(float), defaultdict(float)
    with gzip.open(GRID, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            wb[(int(row[0]), int(row[1]))] += float(row[3])
    with gzip.open(IND, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            wi[(int(row[0]), int(row[1]))] += float(row[2])
    keys = sorted(set(wb) | set(wi))
    gx = np.array([k[0] for k in keys])
    gy = np.array([k[1] for k in keys])
    w_b = np.array([wb.get(k, 0.0) for k in keys])
    w_i = np.array([wi.get(k, 0.0) for k in keys])
    return keys, gx, gy, w_b, w_i


def admin_union(keys, cx, cy):
    """기존 캐시 + 신규 셀 PIP → 시군구코드 배열(합집합 캐시 저장)."""
    cd = {}
    src = ADMIN_U if os.path.exists(ADMIN_U) else ADMIN
    with gzip.open(src, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for x, y, c in r:
            cd[(int(x), int(y))] = c
    out = np.array([cd.get(k, None) for k in keys], dtype=object)
    todo = np.array([v is None for v in out])
    print(f"행정 배정: 캐시 {int((~todo).sum()):,} · 신규 PIP {int(todo.sum()):,}")
    if todo.any():
        out[todo] = ""
        recs = read_dbf_names(SHP + ".dbf")
        polys = read_shp_polys(SHP + ".shp")
        cd_f = next(k for k in recs[0] if k.endswith("_CD"))
        rem = todo.copy()
        for r, p in zip(recs, polys):
            if p is None:
                continue
            e = rings_to_edges(p)
            idx = np.flatnonzero(rem & (cx >= e[0].min()) & (cx <= e[0].max()) &
                                 (cy >= e[1].min()) & (cy <= e[1].max()))
            if not len(idx):
                continue
            hit = pip_big(cx[idx], cy[idx], e)
            out[idx[hit]] = r[cd_f]
            rem[idx[hit]] = False
        with gzip.open(ADMIN_U, "wt", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["gx", "gy", "sgg_cd"])
            for k, c in zip(keys, out):
                w.writerow([k[0], k[1], c])
    out = out.astype(str)
    print(f"  미배정 {int((out == '').sum()):,}")
    return out


def mis_rate(sat, zkey, w, has):
    m = has & (zkey != "") & (w > 0)
    agg = defaultdict(lambda: [0.0, 0.0])
    for k, si, wi in zip(zkey[m], sat[m], w[m]):
        a = agg[k]
        a[0] += wi
        a[1] += si * wi
    rho = {k: s / wt for k, (wt, s) in agg.items()}
    zmaj = np.array([rho.get(k, 0.0) >= 0.5 for k in zkey])
    mism = m & (sat != zmaj)
    return 100.0 * w[mism].sum() / w[m].sum()


def main() -> int:
    keys, gx, gy, w_b, w_i = load_union()
    w_c = w_b + w_i
    cx = (gx + 0.5) * CELL
    cy = (gy + 0.5) * CELL
    print(f"합집합 셀 {len(keys):,} · 건물계 {w_b.sum() / 1e9:.1f} · "
          f"산업용 {w_i.sum() / 1e9:.1f} · 합산 {w_c.sum() / 1e9:.1f} TWh")

    subs = load_substations()
    sx = np.array([s[2] for s in subs])
    sy = np.array([s[3] for s in subs])
    zid = np.array([s[0] for s in subs])

    # 규칙 ①
    a1 = np.empty(len(keys), dtype=np.int32)
    for i in range(0, len(keys), 20000):
        d2 = (cx[i:i + 20000, None] - sx[None, :]) ** 2 + \
             (cy[i:i + 20000, None] - sy[None, :]) ** 2
        a1[i:i + 20000] = np.argmin(d2, axis=1)
    zone1 = zid[a1]
    dist1 = np.hypot(cx - sx[a1], cy - sy[a1])
    # 규칙 ②
    _s, a2, dist2, _nf, _snap = assign_network(cx, cy)
    zone2 = zid[a2]

    sgg = admin_union(keys, cx, cy)
    sido = np.array([c[:2] if c else "" for c in sgg])
    gov_map = build_gov_map()
    govk = np.array([gov_map.get(c, "") for c in sgg])

    zm = zone_margin()
    rows = []
    for rule, zone, dist in (("①최근접", zone1, dist1), ("②네트워크", zone2, dist2)):
        a1v = np.array([zm.get(z, (np.nan,))[0] for z in zone])
        has = ~np.isnan(a1v)
        for sig_name, sig, mask in (("A1_여유2026", a1v, has),
                                    ("B_공급거리", dist, np.ones(len(keys), bool))):
            for zn, zk in (("정부안4광역권", govk), ("시도", sido),
                           ("시군구", sgg), ("변전소권역", zone)):
                m = mask & (zk != "")
                sb, _ = sigma(sig, zk, w_b, m & (w_b > 0))
                sc, _ = sigma(sig, zk, w_c, m & (w_c > 0))
                rows.append(["σ", sig_name, rule, zn, f"{sb:.3f}", f"{sc:.3f}"])
        if rule.startswith("①"):
            sat = a1v == 0
            nation = np.full(len(keys), "KR")
            for zn, zk in (("전국단일", nation), ("정부안4광역권", govk),
                           ("시도", sido), ("시군구", sgg), ("변전소권역", zone)):
                rows.append(["오신호율_pct", "A1_포화/여유", rule, zn,
                             f"{mis_rate(sat, zk, w_b, has & (sgg != '')):.1f}",
                             f"{mis_rate(sat, zk, w_c, has & (sgg != '')):.1f}"])
            hb = has & (w_b > 0)
            hc = has & (w_c > 0)
            rows.append(["포화수요비중_pct", "A1", rule, "-",
                         f"{100 * w_b[hb & sat].sum() / w_b[hb].sum():.1f}",
                         f"{100 * w_c[hc & sat].sum() / w_c[hc].sum():.1f}"])
            rows.append(["신호커버리지_pct", "A1", rule, "-",
                         f"{100 * w_b[has].sum() / w_b.sum():.1f}",
                         f"{100 * w_c[has].sum() / w_c.sum():.1f}"])
            cap = np.isin(govk, ["수도권북부", "수도권남부"])
            ok = govk != ""
            rows.append(["수도권수요비중_pct", "-", "-", "-",
                         f"{100 * w_b[cap].sum() / w_b[ok].sum():.1f}",
                         f"{100 * w_c[cap].sum() / w_c[ok].sum():.1f}"])

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 건물계 = 주택+일반(adj), 합산 = 건물계 + 산업용(공장 연면적 배분). "
                "격자 = 두 레이어 합집합에서 권역·행정 재배정\n")
        w = csv.writer(f)
        w.writerow(["지표", "신호", "규칙", "획정", "건물계", "합산"])
        w.writerows(rows)

    print(f"\n{'지표':<14}{'신호':<14}{'규칙':<9}{'획정':<10}{'건물계':>8}{'합산':>8}")
    for r in rows:
        print(f"{r[0]:<14}{r[1]:<14}{r[2]:<9}{r[3]:<10}{r[4]:>8}{r[5]:>8}")
    print("저장:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
