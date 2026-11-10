# -*- coding: utf-8 -*-
"""2단계-5: 정부안(4광역권) σ(Z) — 사용자 확정 분할 정의 [2026-11-10].

분할 정의 [사용자 확정 2026-11-10]:
  수도권 북부 = 서울 한강 이북 14개 구 + 인천 전체 +
                경기북부 10개 시·군(연천·포천·동두천·양주·가평·파주·고양·
                의정부·남양주·구리) + 김포·부천·양평
  수도권 남부 = 서울 한강 이남 11개 구 + 나머지 경기
  중부권     = 세종·대전·충북·충남 (청주는 충북에 포함)
  남부권     = 이외 전부 (강원·경상·전라·광주·부산·대구·울산·제주)
  주: 기후부 공청회안(중부권 = 강원·충청)과 달리 강원이 남부권 — 사용자 정의.

코드 매핑 없이 SGIS 자체의 코드↔이름 쌍만 사용: 시도는 bnd_sido의
SIDO_CD·SIDO_NM, 시군구는 bnd_sigungu의 SIGUNGU_CD·SIGUNGU_NM.
동명 구(중구·강서구 등)는 시도 코드와 결합해 판별.

σ: 신호 A1(한전 여유용량 2026)·B(공급 거리) × 배정규칙 ①②③
   (①③ 권역은 저장본, ②는 재계산으로 망 거리 복원; ④는 ±0.01 미만이라 생략).

출력: out/public/s2_sigma_gov.csv (+ 4광역권 수요 비중)
"""
from __future__ import annotations

import csv
import gzip
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s2_zones_nearest import DATA_ROOT, load_substations  # noqa: E402
from s2_zones_network import assign_network  # noqa: E402
from s2_sigma import CELL, cell_admin, load_cells, sigma, zone_margin  # noqa: E402
from s3_osm_groundtruth import read_dbf_names  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INT = os.path.join(REPO, "out", "internal")
OUT = os.path.join(REPO, "out", "public", "s2_sigma_gov.csv")
SIDO_DBF = os.path.join(DATA_ROOT, "행정경계", "bnd_sido_00_2025_2Q",
                        "bnd_sido_00_2025_2Q.dbf")
SGG_DBF = os.path.join(DATA_ROOT, "행정경계", "bnd_sigungu_00_2025_2Q",
                       "bnd_sigungu_00_2025_2Q.dbf")

SEOUL_NORTH = {"종로구", "중구", "용산구", "성동구", "광진구", "동대문구",
               "중랑구", "성북구", "강북구", "도봉구", "노원구", "은평구",
               "서대문구", "마포구"}
SEOUL_SOUTH = {"강서구", "양천구", "구로구", "금천구", "영등포구", "동작구",
               "관악구", "서초구", "강남구", "송파구", "강동구"}
GG_NORTH = ("연천군", "포천시", "동두천시", "양주시", "가평군", "파주시",
            "고양시", "의정부시", "남양주시", "구리시",
            "김포시", "부천시", "양평군")  # 김포·부천·양평 북부 [사용자 확정]
JUNGBU_SIDO = {"세종특별자치시", "대전광역시", "충청북도", "충청남도"}


def build_gov_map():
    """SGIS 시군구코드 → 4광역권 이름."""
    sido_rows = read_dbf_names(SIDO_DBF)
    cd_f = next(k for k in sido_rows[0] if k.endswith("_CD"))
    nm_f = next(k for k in sido_rows[0] if k.endswith("_NM"))
    sido = {r[cd_f]: r[nm_f] for r in sido_rows}
    print("시도 코드 사전:", sido)

    sgg_rows = read_dbf_names(SGG_DBF)
    cd_f = next(k for k in sgg_rows[0] if k.endswith("_CD"))
    nm_f = next(k for k in sgg_rows[0] if k.endswith("_NM"))
    seoul_cd = next(cd for cd, nm in sido.items() if nm.startswith("서울"))
    incheon_cd = next(cd for cd, nm in sido.items() if nm.startswith("인천"))
    gg_cd = next(cd for cd, nm in sido.items() if nm.startswith("경기"))

    gov = {}
    unresolved = []
    for r in sgg_rows:
        cd, nm = r[cd_f], r[nm_f].replace(" ", "")
        sd = cd[:2]
        sd_nm = sido.get(sd, "")
        if sd == seoul_cd:
            if nm in SEOUL_NORTH:
                gov[cd] = "수도권북부"
            elif nm in SEOUL_SOUTH:
                gov[cd] = "수도권남부"
            else:
                unresolved.append((cd, nm))
        elif sd == incheon_cd:
            gov[cd] = "수도권북부"
        elif sd == gg_cd:
            gov[cd] = "수도권북부" if nm.startswith(GG_NORTH) else "수도권남부"
        elif sd_nm in JUNGBU_SIDO:
            gov[cd] = "중부권"
        else:
            gov[cd] = "남부권"
    assert not unresolved, f"서울 미분류 구: {unresolved}"
    from collections import Counter
    print("시군구 분류:", dict(Counter(gov.values())))
    return gov


def main() -> int:
    gx, gy, w = load_cells()
    cx = (gx + 0.5) * CELL
    cy = (gy + 0.5) * CELL
    sgg = cell_admin(gx, gy, cx, cy)
    gov_map = build_gov_map()
    govk = np.array([gov_map.get(c, "") for c in sgg])
    tot = w.sum()
    print("4광역권 수요 비중:",
          {z: f"{100 * w[govk == z].sum() / tot:.1f}%"
           for z in ("수도권북부", "수도권남부", "중부권", "남부권")})

    subs = load_substations()
    zid = {s[0]: i for i, s in enumerate(subs)}
    sxy = np.array([[s[2], s[3]] for s in subs])
    zm = zone_margin()

    def load_zone(name):
        d = {}
        with gzip.open(os.path.join(INT, name), "rt", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r)
            for x, y, z in r:
                d[(int(x), int(y))] = z
        return np.array([d[(x, y)] for x, y in zip(gx, gy)])

    zone1 = load_zone("s2_cell_zone_nearest.csv.gz")
    zone3 = load_zone("s2_cell_zone_cap.csv.gz")
    _subs2, a2, dist2, _nf, _snap = assign_network(cx, cy)
    zone2 = np.array([subs[int(k)][0] for k in a2])

    def planar_dist(zone):
        i = np.array([zid[z] for z in zone])
        return np.hypot(cx - sxy[i, 0], cy - sxy[i, 1])

    rows = []
    for rule, zone, dist in (("①최근접", zone1, planar_dist(zone1)),
                             ("②네트워크", zone2, dist2),
                             ("③용량제약", zone3, planar_dist(zone3))):
        a1 = np.array([zm.get(z, (np.nan,))[0] for z in zone])
        for sig_name, sig, mask in (("A1_여유2026", a1, ~np.isnan(a1)),
                                    ("B_공급거리", dist, ~np.isnan(dist))):
            m = mask & (govk != "")
            s_, cov = sigma(sig, govk, w, m)
            rows.append([sig_name, rule, "정부안4광역권", f"{s_:.3f}", f"{cov:.1f}"])

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 분할 정의: 사용자 확정 2026-11-10 (강원=남부권 — 공청회안과 상이)\n")
        wtr = csv.writer(f)
        wtr.writerow(["signal", "rule", "zoning", "sigma", "coverage_pct"])
        wtr.writerows(rows)
        wtr.writerow([])
        wtr.writerow(["# 4광역권 수요 비중(adj)"])
        for z in ("수도권북부", "수도권남부", "중부권", "남부권"):
            wtr.writerow([z, f"{100 * w[govk == z].sum() / tot:.1f}"])

    print(f"\n{'신호':<14}{'규칙':<10}{'σ':>8}{'수요포함%':>10}")
    for r in rows:
        print(f"{r[0]:<14}{r[1]:<10}{r[3]:>8}{r[4]:>10}")
    print("저장:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
