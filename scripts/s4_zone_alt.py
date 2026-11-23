# -*- coding: utf-8 -*-
"""4단계-4: 전력망 여건 기반 획정 대안 — 포화·여유 상태로 묶은 변전소 권역 구역.

설계
  재료 = 변전소 권역(규칙 ① 배정, 여유용량 신호가 있는 권역)
  기준 = 2026년 전력공급 여유용량이 0(포화)인지 여부(0/1)
  방법 = 인접 제약(변전소 kNN, k=8) Ward 병합으로 K개 구역
         (s4_welfare_cluster.py의 시제품과 같은 방법, 묶는 기준만 다름)
  전국 구역화 = 신호가 없는 권역은 가장 가까운 신호 보유 권역의 구역에 붙임
평가 — 구역(지리)은 고정하고 신호만 바꾼다. 행정 단위 획정과 같은 셀에서 계산.
  묶을 때 쓴 해       : 2026년 MW·포화 여부(표본 내)
  묶을 때 쓰지 않은 해 : 2027년, 2028~2032년(MW·포화 여부)
  다른 신호           : 공급 거리(전 셀, 규칙 ①·③ 평면거리)
  다른 배정 규칙      : ②③으로 잰 여유용량 신호
  참고                : 여유용량의 크기(MW)로 묶은 구역(종전 시제품)
  민감도              : 인접 기준 kNN 6·10

검산(불일치 시 중단): 행정 단위의 σ·소수 상태 = s2_sigma*.csv·s4_zoning_compare.csv,
MW 기준 묶음의 σ = s4_cluster_sigma.csv.

출력: out/public/s4_zone_alt.csv        σ·소수 상태(집계)
      out/public/s4_zone_alt_zones.csv  구역별 구성(수요 비중·포화 비중·시도 구성)
      out/internal/s4_zone_alt_brick.csv 권역→구역(내부)
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
from s2_sigma import MATCH, SUPPLY, cell_admin, load_cells, zone_margin  # noqa: E402
from s2_sigma_gov import SIDO_DBF, build_gov_map  # noqa: E402
from s3_osm_groundtruth import read_dbf_names  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUB = os.path.join(REPO, "out", "public")
INT = os.path.join(REPO, "out", "internal")
YEARS = list(range(2026, 2033))
KS = (2, 4, 8, 17)
KNN_MAIN, KNN_SENS = 8, (6, 10)
RULES = (("①최근접", "s2_cell_zone_nearest.csv.gz"), ("②네트워크", "s2_cell_zone_network.csv.gz"),
         ("③용량제약", "s2_cell_zone_cap.csv.gz"))


def read_pub(name):
    with open(os.path.join(PUB, name), encoding="utf-8-sig") as f:
        return list(csv.reader(ln for ln in f if ln.strip() and not ln.startswith("#")))


def check(label, got, ref, fmt="{:.3f}"):
    ok = fmt.format(got) == ref
    print(f"  검산 {label}: 계산 {fmt.format(got)} · 기산출 {ref} {'OK' if ok else '불일치'}")
    assert ok, f"검산 불일치 — {label}"


def sigma(c, key, w):
    """수요 가중 σ = 구역 간 분산 / 총분산."""
    mu = (c * w).sum() / w.sum()
    tot = ((c - mu) ** 2 * w).sum()
    _, inv = np.unique(key, return_inverse=True)
    wz = np.bincount(inv, w)
    sz = np.bincount(inv, c * w)
    ok = wz > 0
    return float((wz[ok] * (sz[ok] / wz[ok] - mu) ** 2).sum() / tot)


def mis_rate(sat, key, w):
    """구역의 수요 가중 다수 상태(포화/여유)와 반대 상태인 수요 비중(%)."""
    _, inv = np.unique(key, return_inverse=True)
    wz = np.bincount(inv, w)
    sz = np.bincount(inv, sat * w)
    maj = (sz / np.maximum(wz, 1e-300) >= 0.5)[inv]
    return float(100.0 * w[sat.astype(bool) != maj].sum() / w.sum())


def ward(wt, feat, xy, knn, ks):
    """인접 제약 Ward 병합(s4_welfare_cluster.py와 같은 절차) → {K: 단위→구역 번호}."""
    n = len(wt)
    d2 = ((xy[:, None, :] - xy[None, :, :]) ** 2).sum(axis=2)
    np.fill_diagonal(d2, np.inf)
    nbr = np.argsort(d2, axis=1)[:, :knn]
    adj = defaultdict(set)
    for i in range(n):
        for j in nbr[i]:
            adj[i].add(int(j)); adj[int(j)].add(i)
    cw = {i: float(wt[i]) for i in range(n)}
    cm = {i: float(feat[i]) for i in range(n)}
    lab = np.arange(n)
    out, k = {}, n
    while k > min(ks):
        best, pair = None, None
        for a in adj:
            for b in adj[a]:
                if a < b:
                    cost = cw[a] * cw[b] / (cw[a] + cw[b]) * (cm[a] - cm[b]) ** 2
                    if best is None or cost < best:
                        best, pair = cost, (a, b)
        if pair is None:
            break
        a, b = pair
        cm[a] = (cw[a] * cm[a] + cw[b] * cm[b]) / (cw[a] + cw[b])
        cw[a] += cw[b]
        lab[lab == b] = a
        for x in adj.pop(b):
            adj[x].discard(b)
            if x != a:
                adj[x].add(a); adj[a].add(x)
        adj[a].discard(a)
        del cw[b], cm[b]
        k -= 1
        if k in ks:
            out[k] = lab.copy()
    return out, adj


def margin_years(ids):
    """권역×연도 여유용량(MW) — 매칭은 s2_sigma.zone_margin과 같은 규칙."""
    sup = {}
    with open(SUPPLY, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["전압구분"] == "154kV":
                sup[r["변전소코드"]] = [float(r[f"전력공급여유용량_{y}(MW)"]) for y in YEARS]
    idx = {z: i for i, z in enumerate(ids)}
    M = np.full((len(ids), len(YEARS)), np.nan)
    with open(MATCH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(ln for ln in f if not ln.startswith("#")):
            if r.get("상태") != "운영" or r.get("OSM매칭") != "Y" or not r.get("osm_fid"):
                continue
            zid = ("G" if r["osm_table"].endswith("polygon") else "P") + r["osm_fid"]
            if r["변전소코드"] in sup and zid in idx:
                M[idx[zid]] = sup[r["변전소코드"]]
    return M


def main() -> int:
    gx, gy, w = load_cells()
    cx, cy = (gx + 0.5) * CELL, (gy + 0.5) * CELL
    subs = load_substations()
    ids = [s[0] for s in subs]
    idx = {z: i for i, z in enumerate(ids)}
    sxy = np.array([[s[2], s[3]] for s in subs])

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
    two = np.array(["수도권" if g.startswith("수도권") else g and "비수도권" for g in gov])
    sido_nm = {}
    rows = read_dbf_names(SIDO_DBF)
    cd_f = next(k for k in rows[0] if k.endswith("_CD"))
    nm_f = next(k for k in rows[0] if k.endswith("_NM"))
    for r in rows:
        sido_nm[r[cd_f]] = r[nm_f]

    M = margin_years(ids)
    zm = zone_margin()
    for z, (a1, _a2) in zm.items():
        if z in idx:
            assert M[idx[z], 0] == a1, "연도별 여유용량과 zone_margin 불일치"
    has_b = ~np.isnan(M[:, 0])

    # ── 재료: 신호 보유·수요 보유 권역(규칙 ①)
    ok1 = has_b[z1] & (w > 0)
    ub = np.unique(z1[ok1])
    pos = np.full(len(ids), -1)
    pos[ub] = np.arange(len(ub))
    wt = np.bincount(pos[z1[ok1]], w[ok1], minlength=len(ub))
    mw26 = M[ub, 0]
    sat26 = (mw26 == 0).astype(float)
    print(f"재료 권역 {len(ub)}개 · 포화 권역 {int(sat26.sum())}개 · "
          f"포화 수요 비중 {100 * wt[sat26 == 1].sum() / wt.sum():.1f}%")

    # 신호 없는 권역 → 가장 가까운 신호 보유 권역
    d2 = ((sxy[:, None, :] - sxy[ub][None, :, :]) ** 2).sum(axis=2)
    near = np.argmin(d2, axis=1)
    near[ub] = np.arange(len(ub))
    unit = near[z1]                                   # 셀 → 재료 권역 색인(전 셀)

    builds = {}
    for knn in (KNN_MAIN,) + KNN_SENS:
        builds[("포화여부", knn)], adj = ward(wt, sat26, sxy[ub], knn, KS)
    builds[("MW크기", KNN_MAIN)], _ = ward(wt, mw26, sxy[ub], KNN_MAIN, KS)

    # 상태가 같은 이웃끼리 이은 덩어리 수(k=8)
    nbr8 = np.argsort(((sxy[ub][:, None, :] - sxy[ub][None, :, :]) ** 2).sum(axis=2)
                      + np.diag(np.full(len(ub), np.inf)), axis=1)[:, :KNN_MAIN]
    par = list(range(len(ub)))

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a
    for i in range(len(ub)):
        for j in nbr8[i]:
            if sat26[i] == sat26[int(j)]:
                par[find(i)] = find(int(j))
    comp = np.array([find(i) for i in range(len(ub))])
    cw = np.bincount(comp, wt, minlength=len(ub))
    n_sat = len({c for c in comp[sat26 == 1]})
    n_sur = len({c for c in comp[sat26 == 0]})
    print(f"상태가 같은 이웃끼리 이은 덩어리: 포화 {n_sat}개 · 여유 {n_sur}개 · "
          f"가장 큰 덩어리 수요 {100 * cw.max() / wt.sum():.1f}%")

    # ── 검산
    print("검산:")
    ref_s = {(r[0], r[1], r[2]): r[3] for n in ("s2_sigma.csv", "s2_sigma_gov.csv")
             for r in read_pub(n) if len(r) == 5}
    ref34 = {(r[1], r[0], r[2]): r[3] for r in read_pub("s2_sigma34.csv")[1:]}
    ref_s.update(ref34)
    admin = (("정부안4광역권", gov), ("시도", sido), ("시군구", sgg))
    adm_ok = sgg != ""
    for rn, _fn in RULES:
        mr = has_b[zr[rn]] & adm_ok
        for zn, zk in admin:
            check(f"{zn} MW2026 {rn}", sigma(M[zr[rn][mr], 0], zk[mr], w[mr]),
                  ref_s[("A1_여유2026", rn, zn)])
    dist = {rn: np.hypot(cx - sxy[zr[rn], 0], cy - sxy[zr[rn], 1]) for rn in ("①최근접", "③용량제약")}
    for rn in dist:
        for zn, zk in admin:
            check(f"{zn} 거리 {rn}", sigma(dist[rn][adm_ok], zk[adm_ok], w[adm_ok]),
                  ref_s[("B_공급거리", rn, zn)])
    m1 = has_b[z1] & adm_ok
    ref_m = {r[0]: r[6] for r in read_pub("s4_zoning_compare.csv")[1:]}
    for zn, zk in admin:
        check(f"{zn} 소수 상태", mis_rate((M[z1[m1], 0] == 0).astype(float), zk[m1], w[m1]),
              ref_m[zn], "{:.1f}")
    ref_cl = {int(r[0]): r[1] for r in read_pub("s4_cluster_sigma.csv")[1:]}
    mc = has_b[z1]
    for k in (4, 8, 17):
        check(f"MW 기준 묶음 K={k}",
              sigma(M[z1[mc], 0], builds[("MW크기", KNN_MAIN)][k][unit[mc]], w[mc]), ref_cl[k])

    # ── 신호의 연도별 성질(규칙 ①, 신호 보유 셀): 상태는 유지되는가, 크기는 유지되는가
    C1 = M[z1[m1]]
    W1 = w[m1]
    S1 = C1 == 0
    X = C1 - (C1 * W1[:, None]).sum(axis=0) / W1.sum()
    cov = (X * W1[:, None]).T @ X / W1.sum()
    corr = cov[0] / np.sqrt(cov[0, 0] * np.diag(cov))
    srows = []
    for j, y in enumerate(YEARS):
        srows.append([y, f"{100 * W1[S1[:, j]].sum() / W1.sum():.1f}",
                      f"{(C1[:, j] * W1).sum() / W1.sum():.1f}",
                      f"{100 * W1[S1[:, 0] & S1[:, j]].sum() / W1[S1[:, 0]].sum():.1f}",
                      f"{100 * W1[~S1[:, 0] & S1[:, j]].sum() / W1[~S1[:, 0]].sum():.1f}",
                      f"{corr[j]:.2f}"])
    with open(os.path.join(PUB, "s4_zone_alt_signal.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 한전 변전소별 여유용량의 연도별 성질 — 규칙 ①, 신호 보유 셀, 주택·일반용(ⓑ) 수요 가중\n")
        wtr = csv.writer(f)
        wtr.writerow(["연도", "포화수요비중_pct", "평균여유_MW", "2026포화수요중_그해포화_pct",
                      "2026여유수요중_그해포화_pct", "2026년MW와의_상관"])
        wtr.writerows(srows)
    print("\n신호의 연도별 성질:")
    for r in srows:
        print("  ", r)

    # ── 평가
    zonings = [("2구역(수도권·비수도권)", "", "", two), ("4광역권(예시)", "", "", gov),
               ("시도", "", "", sido), ("시군구", "", "", sgg)]
    for (bname, knn), res in builds.items():
        for k in KS:
            zonings.append(("권역묶음", bname, knn, res[k][unit]))
    head = ["획정", "묶는기준", "인접k", "신호배정규칙", "구역수",
            "σ_MW2026", "σ_MW2027"] + [f"σ_MW{y}" for y in YEARS[2:]] + \
           ["σ_MW2028-32평균", "σ_MW2026-32평균", "σ_포화2026", "σ_포화2027",
            "σ_포화2028-32", "소수상태_2026_pct", "소수상태_2028-32평균_pct",
            "σ_공급거리(전셀)", "최대구역_수요비중_pct(전셀)"]
    out = []
    for rn, _fn in RULES:
        mr = has_b[zr[rn]] & adm_ok
        C = M[zr[rn][mr]]
        S = (C == 0).astype(float)
        ww = w[mr]
        for zn, bname, knn, zk in zonings:
            if rn != "①최근접" and knn not in ("", KNN_MAIN):
                continue
            key = zk[mr]
            row = [zn, bname, knn, rn, len(set(zk[adm_ok]))]
            row += [f"{sigma(C[:, j], key, ww):.3f}" for j in range(7)]
            row += [f"{sigma(C[:, 2:].mean(axis=1), key, ww):.3f}",
                    f"{sigma(C.mean(axis=1), key, ww):.3f}",
                    f"{sigma(S[:, 0], key, ww):.3f}", f"{sigma(S[:, 1], key, ww):.3f}",
                    f"{sigma(S[:, 2:].mean(axis=1), key, ww):.3f}",
                    f"{mis_rate(S[:, 0], key, ww):.1f}",
                    f"{np.mean([mis_rate(S[:, j], key, ww) for j in range(2, 7)]):.1f}"]
            if rn in dist:
                row.append(f"{sigma(dist[rn][adm_ok], zk[adm_ok], w[adm_ok]):.3f}")
            else:
                row.append("")
            _, inv = np.unique(zk[adm_ok], return_inverse=True)
            row.append(f"{100 * np.bincount(inv, w[adm_ok]).max() / w[adm_ok].sum():.1f}")
            out.append(row)

    with open(os.path.join(PUB, "s4_zone_alt.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 권역묶음 = 이웃한 변전소 권역을 2026년 포화 여부(여유용량 0)가 같은 것끼리 묶은 구역"
                "(묶는기준 'MW크기'는 종전 시제품, 참고용). 구역은 규칙 ① 권역으로 고정하고 "
                "신호만 배정 규칙·연도별로 바꿔 계산. 2026년 값은 묶을 때 쓴 해(표본 내), "
                "2027년 이후와 공급 거리는 묶을 때 쓰지 않은 신호. 여유용량 신호는 신호 보유 셀, "
                "공급 거리·최대구역 비중은 전 셀 기준. 가중 = 주택·일반용(ⓑ). "
                "원천: OpenInfraMap/OSM KOR.gpkg(ODbL) + 한전ON 공개자료 파생\n")
        wtr = csv.writer(f)
        wtr.writerow(head)
        wtr.writerows(out)

    # ── 같은 구역 수끼리의 비교(권역 묶음 ÷ 행정 획정) — 여유용량 신호
    col = {h: i for i, h in enumerate(head)}
    sigs = ["σ_MW2026", "σ_MW2027"] + [f"σ_MW{y}" for y in YEARS[2:]] + ["σ_MW2028-32평균"]
    adm_row = {(r[0], r[3]): r for r in out if r[1] == ""}
    rrows = []
    for r in out:
        if r[1] != "포화여부" or r[4] not in (4, 17):
            continue
        a = adm_row[("4광역권(예시)" if r[4] == 4 else "시도", r[3])]
        for s in sigs:
            rrows.append([f"묶음{r[4]} 대 {a[0]}", r[2], r[3], s, r[col[s]], a[col[s]],
                          f"{float(r[col[s]]) / float(a[col[s]]):.2f}"])
    with open(os.path.join(PUB, "s4_zone_alt_ratio.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 같은 구역 수끼리 비교 — 포화 여부로 묶은 권역 묶음의 σ ÷ 행정 획정의 σ"
                "(소수 셋째 자리로 반올림한 σ로 계산)\n")
        wtr = csv.writer(f)
        wtr.writerow(["비교", "인접k", "신호배정규칙", "신호", "σ_권역묶음", "σ_행정획정", "배율"])
        wtr.writerows(rrows)
    print("\n같은 구역 수끼리 비교(배율 = 권역 묶음 ÷ 행정 획정):")
    for cmp_name in dict.fromkeys(r[0] for r in rrows):
        for label, pick in (("k=8·규칙①②③", lambda r: r[1] == KNN_MAIN),
                            ("k=6·10·규칙①", lambda r: r[1] != KNN_MAIN)):
            sel = [r for r in rrows if r[0] == cmp_name and pick(r)]
            wins = sum(float(r[4]) > float(r[5]) for r in sel)
            print(f"  {cmp_name} [{label}]: 묶음이 높은 경우 {wins}/{len(sel)}")
            for s in ("σ_MW2026", "σ_MW2027", "σ_MW2028-32평균"):
                v = [float(r[6]) for r in sel if r[3] == s]
                print(f"      {s}: 배율 {min(v):.2f}~{max(v):.2f}")

    # ── 구역별 구성(포화 기준·k=8, K=4·8)
    zrows = []
    for k in (4, 8):
        key_all = builds[("포화여부", KNN_MAIN)][k][unit]
        key = key_all[m1]
        order = sorted(set(key), key=lambda c: -W1[key == c].sum())
        for rank, c in enumerate(order, 1):
            mm = key == c
            sh = defaultdict(float)
            for s, wi in zip(sido[m1][mm], W1[mm]):
                sh[sido_nm.get(s, s)] += wi
            top = sorted(sh.items(), key=lambda kv: -kv[1])[:5]
            zrows.append([k, rank,
                          f"{100 * w[adm_ok & (key_all == c)].sum() / w[adm_ok].sum():.1f}",
                          f"{100 * W1[mm].sum() / W1.sum():.1f}",
                          int((builds[('포화여부', KNN_MAIN)][k] == c).sum()),
                          f"{100 * W1[mm & (C1[:, 0] == 0)].sum() / W1[mm].sum():.1f}",
                          f"{100 * ((C1[mm, 2:] == 0).mean(axis=1) * W1[mm]).sum() / W1[mm].sum():.1f}",
                          f"{(C1[mm, 0] * W1[mm]).sum() / W1[mm].sum():.1f}",
                          " · ".join(f"{n} {100 * v / W1[mm].sum():.0f}%" for n, v in top)])
    with open(os.path.join(PUB, "s4_zone_alt_zones.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 포화 여부로 묶은 구역(k=8)의 구성. 포화·여유용량·시도 구성은 신호 보유 수요 기준\n")
        wtr = csv.writer(f)
        wtr.writerow(["K", "구역(수요순)", "수요비중_전셀_pct", "수요비중_신호보유_pct", "신호보유_권역수",
                      "포화수요비중_2026_pct", "포화수요비중_2028-32_pct", "평균여유_2026_MW",
                      "시도구성_상위5(신호보유 수요)"])
        wtr.writerows(zrows)

    with open(os.path.join(INT, "s4_zone_alt_brick.csv"), "w", encoding="utf-8-sig", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["K", "zone", "zone_id", "신호보유"])
        for k in KS:
            lab = builds[("포화여부", KNN_MAIN)][k]
            for i, z in enumerate(ids):
                wtr.writerow([k, int(lab[near[i]]), z, int(has_b[i] and pos[i] >= 0)])

    # ── 화면 요약(규칙 ①)
    print(f"\n{'획정':<20}{'기준':<8}{'k':>3}{'K':>5}{'MW26':>7}{'MW27':>7}{'MW28-32':>9}"
          f"{'포화26':>7}{'포화28-32':>9}{'소수26':>7}{'소수28-32':>9}{'거리':>7}{'최대%':>7}")
    for r in out:
        if r[3] == "①최근접":
            print(f"{r[0]:<20}{r[1]:<8}{str(r[2]):>3}{r[4]:>5}{r[5]:>7}{r[6]:>7}{r[12]:>9}"
                  f"{r[14]:>7}{r[16]:>9}{r[17]:>7}{r[18]:>9}{r[19]:>7}{r[20]:>7}")
    print("\n구역 구성:")
    for r in zrows:
        print("  ", r)
    print("저장: s4_zone_alt.csv, s4_zone_alt_zones.csv (+internal s4_zone_alt_brick.csv)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
