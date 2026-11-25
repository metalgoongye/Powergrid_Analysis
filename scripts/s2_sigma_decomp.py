# -*- coding: utf-8 -*-
"""[해석 보조] 신호 보존율 σ를 시도 사이 / 시도 안 시군구 사이 / 시군구 안으로 나눈다.

시군구는 시도에 포함되므로(시도 키 = 시군구 코드 앞 2자리) 총분산은 정확히 세 몫으로
갈린다:  1 = σ(시도) + [σ(시군구) − σ(시도)] + [1 − σ(시군구)].
가운데 몫(시도 요금이면 묻히고 시군구 요금이면 드러나는 차이)을 시도별로 나누어
어느 시도 안의 차이가 큰지 본다. s2_sigma.py와 같은 셀·같은 가중·같은 신호(규칙 ①,
여유용량 2026년). 검산: σ(시도)·σ(시군구)가 s2_sigma.csv와 일치.

출력: out/public/s2_sigma_decomp.csv (시도 단위 집계 — 공개 등급)
"""
from __future__ import annotations

import csv
import gzip
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s2_sigma import OUT as SIGMA_CSV, REPO, ZONE1, cell_admin, load_cells, zone_margin  # noqa: E402
from s2_sigma_gov import SIDO_DBF  # noqa: E402
from s2_zones_network import CELL  # noqa: E402
from s3_osm_groundtruth import read_dbf_names  # noqa: E402

OUT = os.path.join(REPO, "out", "public", "s2_sigma_decomp.csv")


def main() -> int:
    gx, gy, w = load_cells()
    z1 = {}
    with gzip.open(ZONE1, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for x, y, z in r:
            z1[(int(x), int(y))] = z
    zm = zone_margin()
    c = np.array([zm.get(z1[(x, y)], (np.nan,))[0] for x, y in zip(gx, gy)])
    sgg = cell_admin(gx, gy, (gx + 0.5) * CELL, (gy + 0.5) * CELL)
    ok = ~np.isnan(c) & (sgg != "")
    c, ww, sgg = c[ok], w[ok], sgg[ok]
    sido = np.array([s[:2] for s in sgg])

    W = ww.sum()
    m = (c * ww).sum() / W
    tot = ((c - m) ** 2 * ww).sum()
    g = defaultdict(lambda: [0.0, 0.0])           # 시군구 → [가중, 가중합]
    for k, ci, wi in zip(sgg, c, ww):
        g[k][0] += wi; g[k][1] += ci * wi
    s = defaultdict(lambda: [0.0, 0.0])           # 시도
    for k, (wz, sz) in g.items():
        s[k[:2]][0] += wz; s[k[:2]][1] += sz
    bet_sido = sum(wz * (sz / wz - m) ** 2 for wz, sz in s.values()) / tot
    mid = defaultdict(float)                      # 시도 안 시군구 사이
    for k, (wz, sz) in g.items():
        ws, ss = s[k[:2]]
        mid[k[:2]] += wz * (sz / wz - ss / ws) ** 2 / tot
    bet_sgg = bet_sido + sum(mid.values())

    # 검산: s2_sigma.csv의 규칙 ①·A1 값과 일치해야 한다
    ref = {}
    with open(SIGMA_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["signal"] == "A1_여유2026" and r["rule"] == "①최근접":
                ref[r["zoning"]] = r["sigma"]
    assert f"{bet_sido:.3f}" == ref["시도"] and f"{bet_sgg:.3f}" == ref["시군구"], \
        f"기산출 σ와 불일치: 시도 {bet_sido:.3f}/{ref['시도']}, 시군구 {bet_sgg:.3f}/{ref['시군구']}"

    cd_f = next(k for k in read_dbf_names(SIDO_DBF)[0] if k.endswith("_CD"))
    nm_f = next(k for k in read_dbf_names(SIDO_DBF)[0] if k.endswith("_NM"))
    name = {r[cd_f]: r[nm_f] for r in read_dbf_names(SIDO_DBF)}
    nsgg = defaultdict(int)
    for k in g:
        nsgg[k[:2]] += 1
    rows = [[name[k], nsgg[k], f"{100 * s[k][0] / W:.1f}", f"{s[k][1] / s[k][0]:.1f}", f"{v:.4f}",
             f"{100 * v / sum(mid.values()):.1f}"] for k, v in sorted(mid.items(), key=lambda kv: -kv[1])]
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# σ 분해(규칙 ①·여유용량 2026년·주택·일반용 ⓑ 가중, 신호가 있는 수요). 총분산 1 = 시도 사이 {bet_sido:.3f} "
                f"+ 시도 안 시군구 사이 {bet_sgg - bet_sido:.3f} + 시군구 안 {1 - bet_sgg:.3f}. 수요 가중 평균 여유용량 {m:.1f}MW\n")
        wr = csv.writer(f)
        wr.writerow(["시도", "시군구수", "수요비중_pct", "평균여유용량_MW", "시도안_시군구사이_몫(총분산=1)", "가운데몫_안의_비중_pct"])
        wr.writerows(rows)
    print(f"총분산 1 = 시도 사이 {bet_sido:.3f} + 시도 안 시군구 사이 {bet_sgg - bet_sido:.3f} + 시군구 안 {1 - bet_sgg:.3f}")
    print(f"수요 가중 평균 여유용량 {m:.1f}MW, 시군구 {len(g)}개·시도 {len(s)}개")
    for r in rows:
        print(" ", r)
    print("→", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
