# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 한전 변전소 목록 표-대-표 대조 — OSM 변전소 완전성 검증.

2기둥 검증의 둘째 기둥: 철탑·선로는 GeoAI 탐지로, 변전소는 한전 공식 목록과의
표-대-표 대조로 OSM 완전성을 확인한다(레이어 간 외삽 금지 원칙, methods.md §9-c).

정본 목록 = 한전ON 전력공급 여유용량(154kV, 2026-10-08, sources.yml
kepco_substation_supply_margin). "(건설중)" 표기는 OSM 부재가 당연하므로
운영분만 수록률 분모로 삼는다. 재생e 연계 목록 전용 코드(S/Y·H/P·건설중 혼재)는
역방향(OSM 잉여) 분류 보조로만 쓴다.

대조 방법: 변전소명 정규화(괄호·공백·접두 '154kV'·접미 '변전소' 제거) 후
이름 일치. 수록률은 이름 존재 기준(철도 제외 전 변전소 피처)으로 보고,
전압 태그 완비(154kV 이상) 매칭을 별도 집계한다. 매칭 건은 OSM 중심점의
시군구(PIP, SGIS 2025 2Q)와 한전 시군구를 공백 제거·접미/동일시 소프트
비교(불일치는 목록 보고만, 임의 매핑 없음 — CLAUDE.md). 한전 목록 시군구는
공급권 표기일 수 있어 위치 검증이 아닌 정합성 참고로만 쓴다.
주의: 한전ON 공개분에는 제주 시도가 없다 — 수록률은 육지 계통 기준.

출력:
  out/public/s3_subst_match_summary.csv   (metric,value 집계 — 좌표 없음)
  out/public/s3_subst_match_by_sido.csv   (시도별 수록률 — 좌표 없음)
  out/internal/s3_subst_match_detail.csv  (변전소별 판정 + OSM 좌표 — 비공개)
stdout에는 집계만 출력한다. OSM 파생이므로 ODbL 고지를 산출물 헤더에 기록한다.
"""
import csv
import os
import re
import sqlite3
import struct
import sys

import numpy as np
from pyproj import Transformer

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
GPKG = os.path.join(DATA_ROOT, "powergrid_spatial_data", "OSM_powergrid", "KOR.gpkg")
SHP = os.path.join(DATA_ROOT, "행정경계", "bnd_sigungu_00_2025_2Q", "bnd_sigungu_00_2025_2Q")
SUPPLY = os.path.join(DATA_ROOT, "rdb", "변전소별_접속가능여유용량",
                      "한전ON_변전소별_전력공급_여유용량_20261008.csv")
RE_ONLY = os.path.join(DATA_ROOT, "rdb", "변전소별_접속가능여유용량",
                       "한전ON_변전소별_재생e연계_여유용량_20261008.csv")
OUT_PUB = os.path.join(REPO, "out", "public")
OUT_INT = os.path.join(REPO, "out", "internal")
T = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
ODBL = "# 원천: OpenInfraMap/OSM KOR.gpkg (ODbL) + 한전ON 공개자료 — 파생물 배포 시 ODbL 고지·동일조건"

# 철도(전철) 변전소는 한전 계통이 아니므로 후보에서 제외
EXCLUDE_TYPES = {"traction", "distribution;traction"}


def norm_name(s):
    """변전소명 정규화 — 괄호·공백·접두 전압표기·접미 '변전소' 제거."""
    s = re.sub(r"\(.*?\)", "", s or "")
    s = re.sub(r"^\s*154\s*[kK][vV]\s*", "", s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"^(한국전력공사|한국전력|한전)", "", s)
    s = re.sub(r"변전소$", "", s)
    return s


# ---- 경계 파서 (s3_osm_groundtruth.py와 동일) ----
def read_dbf_names(path):
    with open(path, "rb") as f:
        hdr = f.read(32)
        n_rec = struct.unpack("<I", hdr[4:8])[0]
        hdr_len, rec_len = struct.unpack("<2H", hdr[8:12])
        fields = []
        while True:
            fd = f.read(32)
            if fd[0] == 0x0D:
                break
            fields.append((fd[:11].split(b"\0")[0].decode("ascii"), fd[16]))
        f.seek(hdr_len)
        recs = []
        for _ in range(n_rec):
            raw = f.read(rec_len)[1:]
            rec, off = {}, 0
            for name, flen in fields:
                b = raw[off:off + flen]
                try:
                    rec[name] = b.decode("utf-8").strip()
                except UnicodeDecodeError:
                    rec[name] = b.decode("cp949", "replace").strip()
                off += flen
            recs.append(rec)
    return recs


def read_shp_polys(path):
    polys = []
    with open(path, "rb") as f:
        f.read(100)
        while True:
            rh = f.read(8)
            if len(rh) < 8:
                break
            clen = struct.unpack(">i", rh[4:8])[0] * 2
            body = f.read(clen)
            if struct.unpack("<i", body[:4])[0] != 5:
                polys.append(None)
                continue
            nparts, npts = struct.unpack("<2i", body[36:44])
            parts = struct.unpack(f"<{nparts}i", body[44:44 + 4 * nparts])
            base = 44 + 4 * nparts
            pts = np.frombuffer(body[base:base + 16 * npts], dtype="<f8").reshape(-1, 2)
            polys.append([pts[parts[i]:(parts[i + 1] if i + 1 < nparts else npts)]
                          for i in range(nparts)])
    return polys


def rings_to_edges(rings):
    xs1, ys1, xs2, ys2 = [], [], [], []
    for ring in rings:
        a = np.asarray(ring, dtype=float)
        b = np.roll(a, -1, axis=0)
        xs1.append(a[:, 0]); ys1.append(a[:, 1])
        xs2.append(b[:, 0]); ys2.append(b[:, 1])
    return (np.concatenate(xs1), np.concatenate(ys1),
            np.concatenate(xs2), np.concatenate(ys2))


def point_in_edges(px, py, edges):
    x1, y1, x2, y2 = edges
    with np.errstate(divide="ignore", invalid="ignore"):
        cross = (y1 > py) != (y2 > py)
        xint = (x2 - x1) * (py - y1) / (y2 - y1) + x1
        return bool((cross & (px < xint)).sum() % 2 == 1)


# ---- gpkg 지오메트리 (s3_osm_groundtruth.py와 동일) ----
def gpkg_pts(blob):
    flags = blob[3]
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 7]
    off = 8 + env_len
    little = blob[off]
    bo = "<" if little else ">"
    gtype = struct.unpack_from(bo + "I", blob, off + 1)[0] & 0xFF
    if gtype == 1:
        x, y = struct.unpack_from(bo + "2d", blob, off + 5)
        return [(x, y)]
    if gtype == 3:
        npts = struct.unpack_from(bo + "I", blob, off + 9)[0]
        pts = struct.unpack_from(bo + f"{2 * npts}d", blob, off + 13)
        return list(zip(pts[0::2], pts[1::2]))
    if gtype == 6:
        off2 = off + 9
        little2 = blob[off2]
        bo2 = "<" if little2 else ">"
        npts = struct.unpack_from(bo2 + "I", blob, off2 + 9)[0]
        pts = struct.unpack_from(bo2 + f"{2 * npts}d", blob, off2 + 13)
        return list(zip(pts[0::2], pts[1::2]))
    return None


def sgg_soft_eq(kepco_sgg, sgis_nm):
    """시군구 소프트 비교 → '일치'/'동일시'/'불일치'/None.

    공백 제거 후 접미 일치면 '일치'(구 단위까지), 아니면 앞의 '…시' 토막이
    같을 때 '동일시'(같은 시의 다른 구 — 공급권 표기 차이로 본다).
    """
    a = re.sub(r"\s+", "", kepco_sgg or "")
    b = re.sub(r"\s+", "", sgis_nm or "")
    if not a or not b:
        return None
    if a.endswith(b) or b.endswith(a):
        return "일치"
    ma, mb = re.match(r"^(.+?시)", a), re.match(r"^(.+?시)", b)
    if ma and mb and ma.group(1) == mb.group(1):
        return "동일시"
    return "불일치"


def main():
    os.makedirs(OUT_PUB, exist_ok=True)
    os.makedirs(OUT_INT, exist_ok=True)

    # 1. 한전 목록 (운영/건설중 분리)
    kepco = []  # dict(code,name,norm,status,sido,sgg)
    with open(SUPPLY, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["전압구분"] != "154kV":
                continue
            status = "건설중" if "건설중" in r["변전소명"] else "운영"
            kepco.append(dict(code=r["변전소코드"], name=r["변전소명"],
                              norm=norm_name(r["변전소명"]), status=status,
                              sido=r["시도"] or "미상", sgg=r["시군구"]))
    oper = [k for k in kepco if k["status"] == "운영"]
    cons = [k for k in kepco if k["status"] == "건설중"]

    # 재생e 전용 코드 (역방향 분류 보조)
    re_only_norms = set()
    with open(RE_ONLY, encoding="utf-8-sig") as f:
        supply_codes = {k["code"] for k in kepco}
        for r in csv.DictReader(f):
            if r["변전소코드"] not in supply_codes:
                re_only_norms.add(norm_name(r["변전소명"]))

    # 2. OSM 피처 전체(철도 제외) — hv154 플래그로 전압 태그 완비 여부 구분
    db = sqlite3.connect(GPKG)
    osm = []  # dict(table,fid,name,norm,stype,mv,x,y,hv154)
    n_excl_traction = 0
    for table in ("power_substation_polygon", "power_substation_point"):
        cur = db.execute(f'SELECT fid, name, substation_type, max_voltage, geometry '
                         f'FROM "{table}"')
        for fid, name, stype, mv, geom in cur:
            if (stype or "") in EXCLUDE_TYPES:
                n_excl_traction += 1
                continue
            pts = gpkg_pts(geom)
            if not pts:
                continue
            lon = sum(p[0] for p in pts) / len(pts)
            lat = sum(p[1] for p in pts) / len(pts)
            x, y = T.transform(lon, lat)
            osm.append(dict(table=table, fid=fid, name=name or "",
                            norm=norm_name(name) if name else "",
                            stype=stype or "", mv=mv, x=x, y=y,
                            hv154=(mv or 0) >= 154000))
    db.close()

    # 3. OSM 중심점 → 시군구 (PIP, bbox 선필터)
    recs = read_dbf_names(SHP + ".dbf")
    polys = read_shp_polys(SHP + ".shp")
    nm_f = next(k for k in recs[0] if k.endswith("_NM"))
    bounds = []
    for r, p in zip(recs, polys):
        if p is None:
            continue
        e = rings_to_edges(p)
        bounds.append((r[nm_f], e,
                       e[0].min(), e[0].max(), e[1].min(), e[1].max()))
    for o in osm:
        o["sgis_sgg"] = ""
        for nm, e, x0, x1, y0, y1 in bounds:
            if x0 <= o["x"] <= x1 and y0 <= o["y"] <= y1 and \
                    point_in_edges(o["x"], o["y"], e):
                o["sgis_sgg"] = nm
                break

    # 4. 이름 매칭 (운영분이 분모) — 전체 피처 기준 + 전압 태그 완비(154kV+) 구분
    groups_any, groups_154 = {}, {}
    for o in osm:
        if o["norm"]:
            groups_any.setdefault(o["norm"], []).append(o)
            if o["hv154"]:
                groups_154.setdefault(o["norm"], []).append(o)

    detail = []
    n_match = n_match_154 = n_tagless = 0
    sgg_cnt = {"일치": 0, "동일시": 0, "불일치": 0, None: 0}
    by_sido = {}
    for k in oper:
        cands = groups_154.get(k["norm"]) or groups_any.get(k["norm"]) or []
        pick, sgg_ok = None, None
        if cands:
            # 동명 다중 피처면 시군구 일치 피처 우선
            pick = next((c for c in cands
                         if sgg_soft_eq(k["sgg"], c["sgis_sgg"]) == "일치"),
                        cands[0])
            sgg_ok = sgg_soft_eq(k["sgg"], pick["sgis_sgg"])
            n_match += 1
            if pick["hv154"]:
                n_match_154 += 1
            else:
                n_tagless += 1
            sgg_cnt[sgg_ok] += 1
        s = by_sido.setdefault(k["sido"], [0, 0])
        s[0] += 1
        s[1] += bool(cands)
        detail.append([k["code"], k["name"], k["status"], k["sido"], k["sgg"],
                       "Y" if cands else "N",
                       "" if not cands else ("154kV+" if pick["hv154"] else "태그불비"),
                       pick["table"] if pick else "", pick["fid"] if pick else "",
                       f"{pick['x']:.1f}" if pick else "",
                       f"{pick['y']:.1f}" if pick else "",
                       pick["sgis_sgg"] if pick else "",
                       sgg_ok or ("판정불가" if cands else ""), len(cands)])

    # 건설중도 참고로 매칭만 기록
    n_cons_match = 0
    for k in cons:
        cands = groups_any.get(k["norm"], [])
        n_cons_match += bool(cands)
        detail.append([k["code"], k["name"], k["status"], k["sido"], k["sgg"],
                       "Y" if cands else "N", "", "", "", "", "", "", "",
                       len(cands)])

    # 5. 역방향 — 한전 운영분과 매칭 안 된 OSM 154kV+ 그룹 분류
    #    (한전ON 공개분에 제주가 없으므로 제주 소재는 별도 분류)
    JEJU = {"제주시", "서귀포시"}
    oper_norms = {k["norm"] for k in oper}
    cons_norms = {k["norm"] for k in cons}
    rev = {"건설중목록": 0, "재생e전용목록": 0, "제주": 0, "345kV이상": 0,
           "변환소": 0, "개폐소": 0, "기타": 0}
    rev_rows = []
    for nm, feats in groups_154.items():
        if nm in oper_norms:
            continue
        f0 = feats[0]
        if nm in cons_norms:
            cat = "건설중목록"
        elif nm in re_only_norms:
            cat = "재생e전용목록"
        elif f0["sgis_sgg"] in JEJU:
            cat = "제주"
        elif (f0["mv"] or 0) >= 345000:
            cat = "345kV이상"
        elif "변환소" in f0["name"]:
            cat = "변환소"
        elif "개폐소" in f0["name"]:
            cat = "개폐소"
        else:
            cat = "기타"
        rev[cat] += 1
        rev_rows.append([f0["name"], cat, f0["stype"], f0["mv"], len(feats)])
    n_noname = sum(1 for o in osm if not o["norm"] and o["hv154"])

    # 6. 출력
    cov = 100.0 * n_match / len(oper)
    cov154 = 100.0 * n_match_154 / len(oper)
    summary = [
        ("한전154kV_전체", len(kepco)), ("한전154kV_운영", len(oper)),
        ("한전154kV_건설중", len(cons)),
        ("비고", "한전ON 공개분은 제주 미포함 — 육지 계통 기준"),
        ("OSM피처_철도제외", len(osm)),
        ("OSM그룹_이름기준", len(groups_any)),
        ("OSM그룹_154kV이상", len(groups_154)),
        ("OSM피처_154kV이상_이름없음", n_noname),
        ("OSM제외_철도", n_excl_traction),
        ("이름매칭_운영", n_match), ("수록률_운영_pct", f"{cov:.1f}"),
        ("이름매칭_그중_전압태그완비", n_match_154),
        ("수록률_전압태그완비_pct", f"{cov154:.1f}"),
        ("이름매칭_그중_태그불비", n_tagless),
        ("시군구_일치", sgg_cnt["일치"]), ("시군구_동일시", sgg_cnt["동일시"]),
        ("시군구_불일치", sgg_cnt["불일치"]),
        ("시군구_판정불가", sgg_cnt[None]),
        ("건설중_OSM선존재", n_cons_match),
        ("OSM잉여그룹_건설중목록", rev["건설중목록"]),
        ("OSM잉여그룹_재생e전용목록", rev["재생e전용목록"]),
        ("OSM잉여그룹_제주", rev["제주"]), ("OSM잉여그룹_345kV이상", rev["345kV이상"]),
        ("OSM잉여그룹_변환소", rev["변환소"]), ("OSM잉여그룹_개폐소", rev["개폐소"]),
        ("OSM잉여그룹_기타", rev["기타"]),
    ]
    p = os.path.join(OUT_PUB, "s3_subst_match_summary.csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        f.write(ODBL + "\n")
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerows(summary)

    p2 = os.path.join(OUT_PUB, "s3_subst_match_by_sido.csv")
    with open(p2, "w", newline="", encoding="utf-8-sig") as f:
        f.write(ODBL + "\n")
        w = csv.writer(f)
        w.writerow(["시도", "한전_운영", "OSM_매칭", "수록률_pct"])
        for sido in sorted(by_sido):
            n, m = by_sido[sido]
            w.writerow([sido, n, m, f"{100.0 * m / n:.1f}"])

    p3 = os.path.join(OUT_INT, "s3_subst_match_detail.csv")
    with open(p3, "w", newline="", encoding="utf-8-sig") as f:
        f.write(ODBL + "\n")
        w = csv.writer(f)
        w.writerow(["변전소코드", "변전소명", "상태", "시도", "시군구",
                    "OSM매칭", "매칭근거", "osm_table", "osm_fid",
                    "x_5179", "y_5179", "sgis_시군구", "시군구판정", "동명피처수"])
        w.writerows(detail)
        w.writerow([])
        w.writerow(["# 역방향: 한전 운영분 미매칭 OSM 그룹"])
        w.writerow(["osm_name", "분류", "substation_type", "max_voltage", "피처수"])
        w.writerows(rev_rows)

    for k, v in summary:
        print(f"{k}: {v}")
    print("저장:", p, p2, p3, sep="\n  ")


if __name__ == "__main__":
    main()
