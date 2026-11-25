# -*- coding: utf-8 -*-
"""[점검] 한전ON 목록의 '시군구'는 변전소의 소재지인가 — 불일치 100건의 성격.

배경: s3_subst_match.py는 이름으로 맞춘 변전소의 OSM 위치(시군구)와 한전ON 목록의 시군구를
견주어 일치 516·동일시 8·불일치 100·판정불가 4를 얻었다. 보고서는 불일치를 "대부분 이웃
시군구이며 한전 자료의 시군구가 공급 지역을 뜻하기 때문으로 추정"이라고 적었으나 계산한
적이 없다(보고서 검토 2026-11-25). 세 가지를 센다.
  ① 한전ON 목록 안에서 같은 변전소의 154kV 행과 22.9kV 행의 시군구·읍면동이 같은가
     (소재지라면 한 변전소에 하나여야 한다)
  ② 불일치 건에서 OSM 위치의 시군구와 한전 시군구가 경계를 맞대는가, OSM 위치에서 한전
     시군구까지 얼마나 떨어져 있는가
  ③ 불일치 건의 OSM 시군구가 22.9kV 행의 시군구와는 맞는가

한전 시군구 → 경계 도형은 시도 이름이 SGIS와 글자 그대로 같고 시군구가 s3_subst_match의
소프트 비교로 맞는 경우만 쓴다(임의 매핑 없음). 좌표는 내부 파일에서 읽되 출력은 건수뿐.

출력: out/public/s3_subst_sgg_check.csv
"""
from __future__ import annotations

import csv
import os
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s2_sigma_gov import SIDO_DBF  # noqa: E402
from s3_osm_groundtruth import SHP, read_dbf_names, read_shp_polys, rings_to_edges  # noqa: E402
from s3_subst_match import SUPPLY, point_in_edges, sgg_soft_eq  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DETAIL = os.path.join(REPO, "out", "internal", "s3_subst_match_detail.csv")
OUT = os.path.join(REPO, "out", "public", "s3_subst_sgg_check.csv")
BINS = [1, 3, 5, 10, 20]  # km


def dist_to_edges(x, y, e):
    x1, y1, x2, y2 = e
    dx, dy = x2 - x1, y2 - y1
    L = dx * dx + dy * dy
    t = np.clip(((x - x1) * dx + (y - y1) * dy) / np.where(L == 0, 1, L), 0, 1)
    return float(np.hypot(x - (x1 + t * dx), y - (y1 + t * dy)).min())


def main() -> int:
    # ① 한전ON 목록 안의 두 전압 행
    a, b = {}, {}
    with open(SUPPLY, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            (a if r["전압구분"] == "154kV" else b)[r["변전소코드"]] = r
    both = [k for k in a if k in b and a[k]["시군구"] and b[k]["시군구"]]
    same_sgg = sum((a[k]["시도"], a[k]["시군구"]) == (b[k]["시도"], b[k]["시군구"]) for k in both)
    same_emd = sum((a[k]["시도"], a[k]["시군구"], a[k]["읍면동"]) == (b[k]["시도"], b[k]["시군구"], b[k]["읍면동"]) for k in both)

    # 시군구 경계
    sido_nm = {r[next(k for k in r if k.endswith("_CD"))]: r[next(k for k in r if k.endswith("_NM"))]
               for r in read_dbf_names(SIDO_DBF)}
    recs = read_dbf_names(SHP + ".dbf")
    cd_f = next(k for k in recs[0] if k.endswith("_CD"))
    nm_f = next(k for k in recs[0] if k.endswith("_NM"))
    sgg = []
    for r, p in zip(recs, read_shp_polys(SHP + ".shp")):
        if p is None:
            continue
        e = rings_to_edges(p)
        sgg.append(dict(nm=r[nm_f], sido=sido_nm[r[cd_f][:2]], e=e,
                        box=(e[0].min(), e[0].max(), e[1].min(), e[1].max()), vset=None))

    def vset(s):
        if s["vset"] is None:
            s["vset"] = set(zip(np.round(s["e"][0]).astype(np.int64).tolist(), np.round(s["e"][1]).astype(np.int64).tolist()))
        return s["vset"]

    with open(DETAIL, encoding="utf-8-sig") as f:
        det = [r for r in csv.DictReader(ln for ln in f if not ln.startswith("#")) if r["시군구판정"]]
    verdict = Counter(r["시군구판정"] for r in det)

    adj = Counter()
    dbin = Counter()
    alt = Counter()
    multi = 0
    for r in det:
        if r["시군구판정"] != "불일치":
            continue
        x, y = float(r["x_5179"]), float(r["y_5179"])
        multi += int(r["동명피처수"]) > 1
        # ③ 22.9kV 행의 시군구와는 맞는가
        rb = b.get(r["변전소코드"])
        alt[sgg_soft_eq(rb["시군구"], r["sgis_시군구"]) if rb and rb["시군구"] else "22.9kV 행 주소 없음"] += 1
        # ② 한전 시군구의 도형
        same_sido = [s for s in sgg if s["sido"] == r["시도"]]
        cand = [s for s in same_sido if sgg_soft_eq(r["시군구"], s["nm"]) == "일치"] or \
               [s for s in same_sido if sgg_soft_eq(r["시군구"], s["nm"]) == "동일시"]
        here = next((s for s in sgg if s["box"][0] <= x <= s["box"][1] and s["box"][2] <= y <= s["box"][3]
                     and point_in_edges(x, y, s["e"])), None)
        if not cand or here is None:
            adj["대조 불가(이름으로 경계를 찾지 못함)"] += 1
            continue
        adj["경계를 맞댐" if any(vset(here) & vset(c) for c in cand) else "맞대지 않음"] += 1
        d = min(dist_to_edges(x, y, c["e"]) for c in cand) / 1000
        i = int(np.searchsorted(BINS, d, side="left"))
        dbin[i] += 1

    n_mis = verdict["불일치"]
    names = [f"{lo}km 초과~{hi}km 이하" for lo, hi in zip([0] + BINS[:-1], BINS)] + [f"{BINS[-1]}km 초과"]
    rows = [["목록 안 비교", "같은 변전소의 154kV·22.9kV 행 — 시군구 같음", same_sgg, len(both)],
            ["목록 안 비교", "같은 변전소의 154kV·22.9kV 행 — 읍면동까지 같음", same_emd, len(both)]]
    rows += [["OSM 위치 대 한전 시군구(154kV 행)", k, v, sum(verdict.values())] for k, v in verdict.most_common()]
    rows += [["불일치의 이웃 여부", k, v, n_mis] for k, v in adj.most_common()]
    rows += [["불일치: OSM 위치에서 한전 시군구까지의 거리", names[i], dbin[i], sum(dbin.values())] for i in range(len(names))]
    rows += [["불일치: 22.9kV 행의 시군구와 견주면", str(k), v, n_mis] for k, v in alt.most_common()]
    rows += [["불일치: 같은 이름의 OSM 변전소가 둘 이상", "동명 피처 2개 이상", multi, n_mis]]
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 한전ON 변전소별 전력공급 여유용량(2026-10-08)의 시군구와 OSM 변전소 위치의 대조 점검. 건수만 기록(좌표 없음)\n")
        w = csv.writer(f)
        w.writerow(["구분", "항목", "건수", "모수"])
        w.writerows(rows)
    for r in rows:
        print(f"  {r[0]} | {r[1]}: {r[2]} / {r[3]} ({100 * r[2] / r[3]:.1f}%)")
    print("→", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
