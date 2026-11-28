# -*- coding: utf-8 -*-
"""2단계 점검: 권역의 중심을 154kV 변전소로만 한정하면 결과가 달라지는가.

보고서는 OSM의 154kV '이상' 변전소 845개소를 수요 배정의 중심으로 썼다. 그 안에는 345kV 이상
118개소(+직류 변환소 4개소)가 들어 있다. 상위 전압 변전소가 배전 공급을 하지 않는다면 그곳에
수요를 배정하는 것은 맞지 않으므로, 중심을 바꿔 규칙 ①로 다시 배정하고 주요 결과를 견준다.

  A. 기준(845개소)            — 보고서와 같음(검산)
  B. 154kV만(723개소)
  C. 154kV + 한전 여유용량 목록(154kV)과 이름이 맞은 상위 전압 변전소

출력: out/public/s2_volt_check.csv (집계)
"""
from __future__ import annotations

import csv
import gzip
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s2_zones_nearest import GPKG, load_substations  # noqa: E402
from s2_zones_network import CELL  # noqa: E402
from s2_sigma import cell_admin, load_cells  # noqa: E402
from s2_sigma_gov import build_gov_map  # noqa: E402
from s4_zone_alt import INT, PUB, check, margin_years, mis_rate, read_pub, sigma  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


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
    M = margin_years(ids)
    has_b = ~np.isnan(M[:, 0])
    hv = sv >= 345000
    other = (sv > 154000) & ~hv                      # 180·250kV(직류 변환소)
    print(f"845개소: 154kV {int((sv == 154000).sum())} · 345kV 이상 {int(hv.sum())} · 그 밖(180·250kV) {int(other.sum())}")
    print(f"한전 여유용량 목록(154kV)과 이름이 맞은 곳: 154kV {int(has_b[sv == 154000].sum())} · "
          f"345kV 이상 {int(has_b[hv].sum())} · 그 밖 {int(has_b[other].sum())}")

    sgg = cell_admin(gx, gy, cx, cy)
    sido = np.array([c[:2] if c else "" for c in sgg])
    gov_map = build_gov_map()
    gov = np.array([gov_map.get(c, "") for c in sgg])
    one = np.array(["전국" if c else "" for c in sgg])
    adm_ok = sgg != ""

    # 기준 배정(보고서의 규칙 ①)
    d = {}
    with gzip.open(os.path.join(INT, "s2_cell_zone_nearest.csv.gz"), "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for x, y, z in r:
            d[(int(x), int(y))] = z
    z_base = np.array([idx[d[(x, y)]] for x, y in zip(gx, gy)])

    def assign(keep):
        ki = np.flatnonzero(keep)
        out = np.empty(len(cx), dtype=np.int64)
        for i in range(0, len(cx), 20000):
            dx = cx[i:i + 20000, None] - sxy[ki, 0][None, :]
            dy = cy[i:i + 20000, None] - sxy[ki, 1][None, :]
            out[i:i + 20000] = ki[np.argmin(dx * dx + dy * dy, axis=1)]
        return out

    cases = (("A_기준(154kV 이상 전체)", np.ones(len(ids), bool)),
             ("B_154kV만", sv == 154000),
             ("C_154kV+목록에 있는 상위 전압", (sv == 154000) | (hv & has_b)))
    ref_s = {(r[0], r[1], r[2]): r[3] for n in ("s2_sigma.csv", "s2_sigma_gov.csv") for r in read_pub(n) if len(r) == 5}
    rows = []
    for name, keep in cases:
        z = assign(keep)
        if name.startswith("A_"):
            assert (z == z_base).all(), "기준 배정이 보고서의 규칙 ① 배정과 다름"
        m = has_b[z] & adm_ok
        C = M[z[m], 0]
        S = (C == 0).astype(float)
        ww = w[m]
        dist = np.hypot(cx - sxy[z, 0], cy - sxy[z, 1])
        zw = np.bincount(z, w, minlength=len(ids))
        row = [name, int(keep.sum()), int((zw > 0).sum()), f"{np.median(zw[zw > 0]) / 1e6:.0f}",
               f"{100 * w[z != z_base].sum() / w.sum():.1f}", f"{100 * w[m].sum() / w[adm_ok].sum():.1f}",
               f"{100 * ww[S == 1].sum() / ww.sum():.1f}"]
        for zn, zk in (("정부안4광역권", gov), ("시도", sido), ("시군구", sgg)):
            sg = sigma(C, zk[m], ww)
            if name.startswith("A_"):
                check(f"{zn} MW2026", sg, ref_s[("A1_여유2026", "①최근접", zn)])
            row.append(f"{sg:.3f}")
        for zn, zk in (("정부안4광역권", gov), ("시도", sido), ("시군구", sgg)):
            sg = sigma(dist[adm_ok], zk[adm_ok], w[adm_ok])
            if name.startswith("A_"):
                check(f"{zn} 거리", sg, ref_s[("B_공급거리", "①최근접", zn)])
            row.append(f"{sg:.3f}")
        for zk in (one, gov, sido, sgg):
            row.append(f"{mis_rate(S, zk[m], ww):.1f}")
        rows.append(row)

    head = ["경우", "중심_변전소수", "수요있는_권역수", "권역수요_중앙값_GWh", "기준과_다른_변전소로_간_수요_pct",
            "여유용량신호_포괄_pct", "포화권역_수요비중_pct", "σ여유_4광역권", "σ여유_시도", "σ여유_시군구",
            "σ거리_4광역권", "σ거리_시도", "σ거리_시군구", "소수상태_전국", "소수상태_4광역권", "소수상태_시도", "소수상태_시군구"]
    with open(os.path.join(PUB, "s2_volt_check.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 권역 중심을 154kV 변전소로만 한정했을 때의 점검 — 규칙 ①(가장 가까운 변전소), 여유용량 2026년, 가중 = 주택·일반용(ⓑ). "
                "원천: OpenInfraMap/OSM KOR.gpkg(ODbL) + 한전ON 공개자료 파생\n")
        wtr = csv.writer(f)
        wtr.writerow(head)
        wtr.writerows(rows)
    for h, *vals in zip(head, *rows):
        print(f"  {h:<28}", *[f"{v:>26}" for v in vals])
    print("저장: s2_volt_check.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
