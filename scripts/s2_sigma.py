# -*- coding: utf-8 -*-
"""2단계-3: σ(Z) 1차 산출 — 신호 2종 × 배정규칙 2종 × 획정 대안 4종.

σ(Z) = (요금-한계비용 괴리 신호의) 구역 간 분산 ÷ 총분산, 수요 가중.
전국 단일 = 0, 변전소 권역(벽돌) 수준 차등 = 신호가 권역 상수면 1.

신호(한계비용 대리) 2종 — 수준·부호와 무관(분산비는 아핀 불변):
  A. 한전 변전소별 전력공급 여유용량(154kV) — 공급 핍박도의 직접 관측.
     A1 = 2026년(MW), A2 = 2026~2032 평균(절단 0 완화의 강건성 점검).
     변전소 매칭은 s3_subst_match(표-대-표) 결과를 그대로 사용 — 매칭 안 된
     권역의 셀은 해당 σ 계산에서 제외하고 포함 수요 비중(coverage)을 보고.
  B. 공급 거리(cost-to-serve 대리) — 셀→배정 변전소 거리. 규칙 ①은 평면,
     규칙 ②는 '선로 접속 + 망 주행'(s2_zones_network.assign_network).

배정규칙: ① 평면 최근접(s2_cell_zone_nearest), ② 네트워크 거리(재계산).
획정 대안 Z: 전국 단일 · 시도 · 시군구 · 변전소 권역.
  시도/시군구는 SGIS 2025 2Q 경계 PIP — 통계청 코드(SIGUNGU_CD, 시도 = 앞
  2자리)를 '구획 키'로만 쓴다(행정표준코드 매핑 없음 — CLAUDE.md).

출력: out/public/s2_sigma.csv (signal, rule, zoning, sigma, coverage_pct)
      out/internal/s2_cell_admin.csv.gz (셀→시군구코드 캐시)
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
from s2_zones_network import CELL, assign_network  # noqa: E402
from s3_osm_groundtruth import SHP, read_dbf_names, read_shp_polys, \
    rings_to_edges  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
SUPPLY = os.path.join(DATA_ROOT, "rdb", "변전소별_접속가능여유용량",
                      "한전ON_변전소별_전력공급_여유용량_20261008.csv")
MATCH = os.path.join(REPO, "out", "internal", "s3_subst_match_detail.csv")
ZONE1 = os.path.join(REPO, "out", "internal", "s2_cell_zone_nearest.csv.gz")
ADMIN = os.path.join(REPO, "out", "internal", "s2_cell_admin.csv.gz")
OUT = os.path.join(REPO, "out", "public", "s2_sigma.csv")


def load_cells():
    gx, gy, w = [], [], []
    with gzip.open(GRID, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            gx.append(int(row[0])); gy.append(int(row[1]))
            w.append(float(row[3]))  # GWh_adj(kWh) — 수요 가중치
    return np.array(gx), np.array(gy), np.array(w)


def pip_big(px, py, edges, pchunk=512, echunk=100000):
    """점-다각형 판정 — 점·선분 양쪽을 블록화(해안 시군구 84만 선분 OOM 대비)."""
    x1, y1, x2, y2 = edges
    out = np.zeros(len(px), dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        for s in range(0, len(px), pchunk):
            X = px[s:s + pchunk, None]
            Y = py[s:s + pchunk, None]
            cnt = np.zeros(X.shape[0], dtype=np.int64)
            for t in range(0, len(x1), echunk):
                a = slice(t, t + echunk)
                cross = (y1[a] > Y) != (y2[a] > Y)
                xint = (x2[a] - x1[a]) * (Y - y1[a]) / (y2[a] - y1[a]) + x1[a]
                cnt += (cross & (X < xint)).sum(axis=1)
            out[s:s + pchunk] = cnt % 2 == 1
    return out


def cell_admin(gx, gy, cx, cy):
    """셀 → SIGUNGU_CD (SGIS PIP, 캐시)."""
    if os.path.exists(ADMIN):
        cd = {}
        with gzip.open(ADMIN, "rt", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r)
            for x, y, c in r:
                cd[(int(x), int(y))] = c
        out = np.array([cd.get((x, y), "") for x, y in zip(gx, gy)])
        print(f"행정 배정(캐시): 미배정 {int((out == '').sum()):,}")
        return out

    recs = read_dbf_names(SHP + ".dbf")
    polys = read_shp_polys(SHP + ".shp")
    cd_f = next(k for k in recs[0] if k.endswith("_CD"))
    out = np.full(len(gx), "", dtype=object)
    todo = np.ones(len(gx), dtype=bool)
    for r, p in zip(recs, polys):
        if p is None:
            continue
        e = rings_to_edges(p)
        x0, x1 = e[0].min(), e[0].max()
        y0, y1 = e[1].min(), e[1].max()
        idx = np.flatnonzero(todo & (cx >= x0) & (cx <= x1) &
                             (cy >= y0) & (cy <= y1))
        if not len(idx):
            continue
        hit = pip_big(cx[idx], cy[idx], e)
        out[idx[hit]] = r[cd_f]
        todo[idx[hit]] = False
    print(f"행정 배정(PIP): 미배정 {int(todo.sum()):,}")
    with gzip.open(ADMIN, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["gx", "gy", "sgg_cd"])
        for x, y, c in zip(gx, gy, out):
            w.writerow([x, y, c])
    return out.astype(str)


def zone_margin():
    """zone_id → (여유 2026, 평균 2026~2032) — 표-대-표 매칭 경유."""
    sup = {}
    with open(SUPPLY, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["전압구분"] != "154kV":
                continue
            ys = [float(r[f"전력공급여유용량_{y}(MW)"]) for y in range(2026, 2033)]
            sup[r["변전소코드"]] = (ys[0], sum(ys) / len(ys))
    out = {}
    with open(MATCH, encoding="utf-8-sig") as f:
        rows = csv.DictReader(ln for ln in f if not ln.startswith("#"))
        for r in rows:
            if r.get("상태") != "운영" or r.get("OSM매칭") != "Y" or not r.get("osm_fid"):
                continue
            tag = "G" if r["osm_table"].endswith("polygon") else "P"
            zid = f"{tag}{r['osm_fid']}"
            if r["변전소코드"] in sup:
                out[zid] = sup[r["변전소코드"]]
    print(f"신호 A: 여유용량 매핑 권역 {len(out)}")
    return out


def sigma(signal, zone_key, w, mask=None):
    """수요 가중 σ = 구역 간 분산 / 총분산. mask=신호 보유 셀."""
    if mask is None:
        mask = np.ones(len(w), dtype=bool)
    c = signal[mask]
    ww = w[mask]
    keys = zone_key[mask]
    W = ww.sum()
    m = (c * ww).sum() / W
    tot = ((c - m) ** 2 * ww).sum()
    if tot == 0:
        return float("nan"), 100.0 * W / w.sum()
    agg = defaultdict(lambda: [0.0, 0.0])
    for k, ci, wi in zip(keys, c, ww):
        a = agg[k]
        a[0] += wi
        a[1] += ci * wi
    bet = sum(wz * (sz / wz - m) ** 2 for wz, sz in agg.values())
    return bet / tot, 100.0 * W / w.sum()


def main() -> int:
    gx, gy, w = load_cells()
    cx = (gx + 0.5) * CELL
    cy = (gy + 0.5) * CELL
    print(f"셀 {len(gx):,}")

    # 배정 ①: 저장본 로드
    z1 = {}
    with gzip.open(ZONE1, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for x, y, z in r:
            z1[(int(x), int(y))] = z
    zone1 = np.array([z1[(x, y)] for x, y in zip(gx, gy)])

    # 배정 ②: 재계산(거리 포함)
    subs, assign2, dist2, _nf, _snap = assign_network(cx, cy)
    zone2 = np.array([subs[int(k)][0] for k in assign2])

    # 규칙① 평면거리(신호 B-①)
    sid = {s[0]: i for i, s in enumerate(subs)}
    sxy = np.array([[s[2], s[3]] for s in subs])
    i1 = np.array([sid[z] for z in zone1])
    dist1 = np.hypot(cx - sxy[i1, 0], cy - sxy[i1, 1])

    # 행정 구획
    sgg = cell_admin(gx, gy, cx, cy)
    sido = np.array([s[:2] if s else "" for s in sgg])
    nation = np.full(len(gx), "KR")

    # 신호 A: 권역 여유용량 → 셀로 전개 (규칙별)
    zm = zone_margin()
    rows = []
    for rule, zone in (("①최근접", zone1), ("②네트워크", zone2)):
        a1 = np.array([zm.get(z, (np.nan, np.nan))[0] for z in zone])
        a2 = np.array([zm.get(z, (np.nan, np.nan))[1] for z in zone])
        has_a = ~np.isnan(a1)
        d = dist1 if rule.startswith("①") else dist2
        for sig_name, sig, mask in (
            ("A1_여유2026", a1, has_a),
            ("A2_여유26-32평균", a2, has_a),
            ("B_공급거리", d, ~np.isnan(d)),
        ):
            for zn_name, zkey in (("전국단일", nation), ("시도", sido),
                                  ("시군구", sgg), ("변전소권역", zone)):
                m = mask & (zkey != "")
                s, cov = sigma(sig, zkey, w, m)
                rows.append([sig_name, rule, zn_name,
                             f"{s:.3f}", f"{cov:.1f}"])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["signal", "rule", "zoning", "sigma", "coverage_pct"])
        wtr.writerows(rows)

    print(f"\n{'신호':<16}{'규칙':<10}{'획정':<8}{'σ':>8}{'수요포함%':>10}")
    for r in rows:
        print(f"{r[0]:<16}{r[1]:<10}{r[2]:<8}{r[3]:>8}{r[4]:>10}")
    print("저장:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
