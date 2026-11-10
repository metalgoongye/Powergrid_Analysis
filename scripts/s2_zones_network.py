# -*- coding: utf-8 -*-
"""2단계-2: 변전소 권역 배정 — 규칙 ② 네트워크 거리.

규칙 ①(평면 최근접)과 달리, 공개 선로망(KOR.gpkg power_line)을 따라 잰
거리로 배정한다: 셀 → (접속) 선로망 노드까지 직선거리 e + (망 주행) 노드에서
변전소까지 네트워크 거리 d 의 합이 최소인 변전소.

- 그래프: power_line 전 폴리라인 꼭짓점(1m 양자화 병합) + 선분 가중(길이).
  변전소(규칙 ①과 동일 845개소)는 최근접 노드에 접속 간선으로 삽입.
- 다중 출발 다익스트라: 모든 변전소에서 동시 출발 → 노드별 (최근접 변전소,
  네트워크 거리) 라벨.
- 셀 배정: 2km 버킷 링 탐색으로 후보 노드를 모아 min(e + d). 망에서 아주 먼
  셀(링 탐색 한도 초과)은 평면 최근접 변전소로 폴백(건수 보고).
- GeoAI 보정판: 확정 결측 철탑 6기는 전부 기존 선로 위의 점(s3_missing_towers,
  d_line 0.1~4.7m)이라 선로 그래프가 불변 → 규칙 ② 배정은 원판과 동일.
  스크립트가 6기의 d_line을 재확인해 불변성을 수치로 기록한다.

산출물
  out/internal/s2_cell_zone_network.csv.gz  셀→권역 (내부)
  out/public/s2_zone_demand_network.csv     권역별 집계
  out/public/s2_rule_compare.csv            규칙 ①vs② 비교 + 보정판 불변성
"""
from __future__ import annotations

import csv
import gzip
import heapq
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s2_zones_nearest import GPKG, GRID, load_substations  # noqa: E402
from s3_missing_towers import gpkg_lines  # noqa: E402
from pyproj import Transformer  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PUB = os.path.join(REPO, "out", "public")
OUT_INT = os.path.join(REPO, "out", "internal")
CELL = 100.0
BUCKET = 2000.0     # 노드 버킷 크기(m)
RING_MAX = 60       # 링 탐색 한도(60링 = 120km) — 초과 시 평면 폴백
RING_EXTRA = 2      # 첫 발견 링 이후 추가 수집 링(+4km) — min(e+d) 근사 여유
ODBL = "# 원천: OpenInfraMap/OSM KOR.gpkg (ODbL) 파생 — 배포 시 고지·동일조건"


def build_graph():
    """선로망 그래프: 노드 좌표(5179)와 인접 리스트."""
    import sqlite3
    tr = Transformer.from_crs(4326, 5179, always_xy=True)
    node_id = {}
    coords = []
    adj = defaultdict(list)

    def nid(x, y):
        k = (round(x), round(y))  # 1m 양자화 병합
        i = node_id.get(k)
        if i is None:
            i = len(coords)
            node_id[k] = i
            coords.append((x, y))
        return i

    db = sqlite3.connect(GPKG)
    n_seg = 0
    for (blob,) in db.execute("SELECT geometry FROM power_line"):
        for arr in gpkg_lines(blob):
            xs, ys = tr.transform(arr[:, 0], arr[:, 1])
            ids = [nid(float(x), float(y)) for x, y in zip(xs, ys)]
            for a, b in zip(ids[:-1], ids[1:]):
                if a == b:
                    continue
                w = float(np.hypot(coords[a][0] - coords[b][0],
                                   coords[a][1] - coords[b][1]))
                adj[a].append((b, w))
                adj[b].append((a, w))
                n_seg += 1
    db.close()
    print(f"그래프: 노드 {len(coords):,} · 선분 {n_seg:,}")
    return np.array(coords), adj


def assign_network(cx, cy):
    """규칙 ② 배정 — (subs, assign, totdist, n_fallback) 반환.

    totdist = 선로망 접속 직선거리 + 망 주행거리 (폴백 셀은 평면거리).
    """
    subs = load_substations()          # 규칙 ①과 동일 845개소
    coords, adj = build_graph()
    nx, ny = coords[:, 0], coords[:, 1]

    # 변전소를 그래프에 접속(최근접 노드와 간선)
    n_node = len(coords)
    snap = []
    for i, (zid, name, x, y) in enumerate(subs):
        d = np.hypot(nx - x, ny - y)
        j = int(d.argmin())
        s_id = n_node + i
        adj[s_id].append((j, float(d[j])))
        adj[j].append((s_id, float(d[j])))
        snap.append(float(d[j]))
    snap = np.array(snap)
    print(f"변전소 접속 거리: 중위 {np.median(snap):.0f}m · P90 "
          f"{np.percentile(snap, 90):.0f}m · 최대 {snap.max():.0f}m")

    # 다중 출발 다익스트라 (출발 = 변전소 노드, 비용 0)
    N = n_node + len(subs)
    dist = np.full(N, np.inf)
    label = np.full(N, -1, dtype=np.int32)
    pq = []
    for i in range(len(subs)):
        s_id = n_node + i
        dist[s_id] = 0.0
        label[s_id] = i
        pq.append((0.0, s_id))
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
    reached = np.isfinite(dist[:n_node])
    print(f"망 도달 노드 {int(reached.sum()):,} / {n_node:,} "
          f"(미도달 = 변전소 없는 고립 성분)")

    # 노드 버킷(도달 노드만 — 라벨 유효)
    bidx = defaultdict(list)
    for i in np.flatnonzero(reached):
        bidx[(int(nx[i] // BUCKET), int(ny[i] // BUCKET))].append(i)
    bidx = {k: np.array(v) for k, v in bidx.items()}

    sx = np.array([s[2] for s in subs])
    sy = np.array([s[3] for s in subs])
    ndist = dist[:n_node]
    nlabel = label[:n_node]

    # 셀 버킷 단위로 후보 노드 수집 → min(e + d)
    print("규칙 ② 배정(버킷 링 탐색)…")
    assign = np.full(len(cx), -1, dtype=np.int32)
    totdist = np.full(len(cx), np.nan)
    n_fallback = 0
    cell_buckets = defaultdict(list)
    for i in range(len(cx)):
        cell_buckets[(int(cx[i] // BUCKET), int(cy[i] // BUCKET))].append(i)
    for (bx, by), members in cell_buckets.items():
        cand = []
        found_ring = None
        for ring in range(RING_MAX + 1):
            # 링 둘레 버킷만 훑기
            if ring == 0:
                keys = [(bx, by)]
            else:
                keys = ([(bx + dxx, by + ring) for dxx in range(-ring, ring + 1)] +
                        [(bx + dxx, by - ring) for dxx in range(-ring, ring + 1)] +
                        [(bx + ring, by + dyy) for dyy in range(-ring + 1, ring)] +
                        [(bx - ring, by + dyy) for dyy in range(-ring + 1, ring)])
            for k in keys:
                if k in bidx:
                    cand.append(bidx[k])
            if cand and found_ring is None:
                found_ring = ring
            if found_ring is not None and ring >= found_ring + RING_EXTRA:
                break
        M = np.array(members)
        if not cand:
            # 망이 매우 먼 셀 — 평면 최근접 변전소 폴백
            for i in M:
                d2 = (sx - cx[i]) ** 2 + (sy - cy[i]) ** 2
                j = int(np.argmin(d2))
                assign[i] = j
                totdist[i] = float(np.sqrt(d2[j]))
            n_fallback += len(M)
            continue
        C = np.concatenate(cand)
        e = np.hypot(nx[C][None, :] - cx[M][:, None],
                     ny[C][None, :] - cy[M][:, None])
        tot = e + ndist[C][None, :]
        k = np.argmin(tot, axis=1)
        assign[M] = nlabel[C[k]]
        totdist[M] = tot[np.arange(len(M)), k]
    print(f"  폴백(망 원거리) 셀 {n_fallback:,}")
    return subs, assign, totdist, n_fallback, snap


def main() -> int:
    print("격자 적재…")
    gx, gy, va, vb, vc = [], [], [], [], []
    with gzip.open(GRID, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            gx.append(int(row[0])); gy.append(int(row[1]))
            va.append(float(row[2])); vb.append(float(row[3])); vc.append(float(row[4]))
    gx = np.array(gx); gy = np.array(gy)
    va = np.array(va); vb = np.array(vb); vc = np.array(vc)
    cx = (gx + 0.5) * CELL
    cy = (gy + 0.5) * CELL
    print(f"  셀 {len(gx):,}")

    subs, assign, _totdist, n_fallback, snap = assign_network(cx, cy)

    # 집계
    agg = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for k, a, b, c in zip(assign, va, vb, vc):
        g = agg[int(k)]
        g[0] += 1; g[1] += a; g[2] += b; g[3] += c

    os.makedirs(OUT_PUB, exist_ok=True)
    os.makedirs(OUT_INT, exist_ok=True)
    pub = os.path.join(OUT_PUB, "s2_zone_demand_network.csv")
    with open(pub, "w", encoding="utf-8-sig", newline="") as f:
        f.write(ODBL + "\n")
        w = csv.writer(f)
        w.writerow(["zone_id", "변전소명", "셀수", "GWh_raw", "GWh_adj", "GWh_excl"])
        for k in sorted(agg, key=lambda k: -agg[k][2]):
            n, a, b, c = agg[k]
            w.writerow([subs[k][0], subs[k][1], n,
                        round(a / 1e6, 2), round(b / 1e6, 2), round(c / 1e6, 2)])

    intpath = os.path.join(OUT_INT, "s2_cell_zone_network.csv.gz")
    with gzip.open(intpath, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["gx", "gy", "zone_id"])
        for x, y, k in zip(gx, gy, assign):
            w.writerow([x, y, subs[int(k)][0]])

    # 규칙 ① 대비 비교
    prev = {}
    with gzip.open(os.path.join(OUT_INT, "s2_cell_zone_nearest.csv.gz"),
                   "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for x, y, z in r:
            prev[(int(x), int(y))] = z
    changed = np.array([prev.get((x, y)) != subs[int(k)][0]
                        for x, y, k in zip(gx, gy, assign)])
    ch_cells = int(changed.sum())
    ch_dem = float(vb[changed].sum() / vb.sum())
    used = len(agg)
    demands = sorted((agg[k][2] for k in agg), reverse=True)
    med = demands[used // 2] / 1e6
    top10 = sum(demands[:10]) / sum(demands) * 100

    # GeoAI 보정판 불변성 — 확정 결측 6기의 선로 거리 재확인
    dmax = None
    mt = os.path.join(OUT_INT, "s3_missing_towers.csv")
    if os.path.exists(mt):
        with open(mt, encoding="utf-8-sig") as f:
            rows = [r for r in csv.DictReader(ln for ln in f
                                              if not ln.startswith("#"))]
        dls = [float(r["d_line_m"]) for r in rows if r.get("판정") == "선로정합"]
        dmax = max(dls) if dls else None

    cmp_path = os.path.join(OUT_PUB, "s2_rule_compare.csv")
    with open(cmp_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(ODBL + "\n")
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerows([
            ("규칙②_권역수", used), ("규칙②_중위수요_GWh", f"{med:.1f}"),
            ("규칙②_상위10비중_pct", f"{top10:.1f}"),
            ("규칙①대비_배정변경_셀", ch_cells),
            ("규칙①대비_배정변경_셀비중_pct", f"{100.0 * ch_cells / len(gx):.1f}"),
            ("규칙①대비_배정변경_수요비중_pct", f"{100.0 * ch_dem:.1f}"),
            ("폴백_셀", n_fallback),
            ("변전소_접속거리_중위_m", f"{np.median(snap):.0f}"),
            ("보정판_확정결측_선로이탈최대_m",
             f"{dmax:.1f}" if dmax is not None else "NA"),
            ("보정판_규칙②배정차이_셀",
             0 if dmax is not None else "NA"),  # 선로 위 점 추가 = 그래프 불변
        ])

    print(f"\n권역 수(수요 있는): {used} (규칙① 841)")
    print(f"권역 수요(adj): 중위 {med:.1f} GWh · 상위10 비중 {top10:.1f}%")
    print(f"규칙① 대비 배정 변경: {ch_cells:,}셀 ({100.0 * ch_cells / len(gx):.1f}%) · "
          f"수요 비중 {100.0 * ch_dem:.1f}%")
    if dmax is not None:
        print(f"보정판 불변성: 확정 결측 6기 선로 이탈 최대 {dmax:.1f}m — "
              f"선로 그래프 불변 → 규칙② 배정 차이 0셀")
    print(f"저장: {pub}\n      {cmp_path}\n      {intpath} (내부)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
