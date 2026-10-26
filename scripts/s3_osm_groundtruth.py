# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 파일럿 권역의 OSM 전력설비 추출 — 탐지 대조 기준표.

KOR.gpkg(ODbL)에서 파일럿 3개 시군구(서천군·보령시·은평구) 안의
철탑(power_tower)·변전소(power_substation_*)를 뽑아 대조 기준으로 저장한다.

- 좌표가 포함되므로 산출물은 out/internal/ 에만 쓴다 (공개 금지, CLAUDE.md).
- stdout에는 집계(건수)만 출력한다.
- 파생물이므로 ODbL 고지를 산출물 헤더에 기록한다.

출력: out/internal/s3_osm_groundtruth.csv (class, sigungu, x_5179, y_5179, osm_id)
"""
import csv
import os
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
OUT = os.path.join(REPO, "out", "internal")
TARGETS = ["서천군", "보령시", "은평구", "화성시", "세종시"]  # 화성·세종 추가 2026-10-20
T = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)

# (클래스, 테이블, 지오메트리 종류) — 폴리곤은 중심점으로 환산
LAYERS = [("tower", "power_tower", "point"),
          ("substation", "power_substation_point", "point"),
          ("substation", "power_substation_polygon", "polygon")]


# ---- 경계 (ortho_coverage_map.py와 동일 파서) ----
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


def points_in_edges(px, py, edges, chunk=2000):
    x1, y1, x2, y2 = edges
    out = np.zeros(len(px), dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        for s in range(0, len(px), chunk):
            X = px[s:s + chunk, None]
            Y = py[s:s + chunk, None]
            cross = (y1 > Y) != (y2 > Y)
            xint = (x2 - x1) * (Y - y1) / (y2 - y1) + x1
            out[s:s + chunk] = (cross & (X < xint)).sum(axis=1) % 2 == 1
    return out


# ---- gpkg 지오메트리 (WKB) ----
def gpkg_xy(blob):
    """GPKG 바이너리 헤더를 건너뛰고 점 좌표 또는 폴리곤 외곽 중심을 반환."""
    flags = blob[3]
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 7]
    off = 8 + env_len
    little = blob[off]
    bo = "<" if little else ">"
    gtype = struct.unpack_from(bo + "I", blob, off + 1)[0] & 0xFF
    if gtype == 1:  # Point
        x, y = struct.unpack_from(bo + "2d", blob, off + 5)
        return x, y
    if gtype == 3:  # Polygon — 외곽 고리 평균
        nrings = struct.unpack_from(bo + "I", blob, off + 5)[0]
        npts = struct.unpack_from(bo + "I", blob, off + 9)[0]
        pts = struct.unpack_from(bo + f"{2 * npts}d", blob, off + 13)
        xs = pts[0::2]; ys = pts[1::2]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    if gtype == 6:  # MultiPolygon — 첫 폴리곤 외곽 평균
        # [npolys][wkb polygon ...] — 첫 폴리곤 본문으로 이동
        off2 = off + 9  # 멀티 헤더(바이트오더1+타입4+개수4)
        little2 = blob[off2]
        bo2 = "<" if little2 else ">"
        npts = struct.unpack_from(bo2 + "I", blob, off2 + 9)[0]
        pts = struct.unpack_from(bo2 + f"{2 * npts}d", blob, off2 + 13)
        xs = pts[0::2]; ys = pts[1::2]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    recs = read_dbf_names(SHP + ".dbf")
    polys = read_shp_polys(SHP + ".shp")
    name_f = next(k for k in recs[0] if k.endswith("_NM"))
    targets = {r[name_f]: rings_to_edges(p)
               for r, p in zip(recs, polys) if r[name_f] in TARGETS}
    assert len(targets) == len(TARGETS)

    db = sqlite3.connect(GPKG)
    rows_out = []
    counts = {}
    for cls, table, _kind in LAYERS:
        cur = db.execute(f'SELECT fid, geometry FROM "{table}"')
        lons, lats, fids = [], [], []
        for fid, geom in cur:
            xy = gpkg_xy(geom)
            if xy:
                lons.append(xy[0]); lats.append(xy[1]); fids.append(fid)
        if not fids:
            continue
        X, Y = T.transform(np.array(lons), np.array(lats))
        for nm, edges in targets.items():
            # 경계 bbox 선필터 후 정밀 PIP
            x1, y1, x2, y2 = edges
            bb = ((X >= min(x1.min(), x2.min())) & (X <= max(x1.max(), x2.max())) &
                  (Y >= min(y1.min(), y2.min())) & (Y <= max(y1.max(), y2.max())))
            idx = np.flatnonzero(bb)
            if not len(idx):
                counts[(nm, cls, table)] = counts.get((nm, cls, table), 0)
                continue
            hit = points_in_edges(X[idx], Y[idx], edges)
            for i in idx[hit]:
                rows_out.append({"class": cls, "table": table, "sigungu": nm,
                                 "x_5179": round(float(X[i]), 1),
                                 "y_5179": round(float(Y[i]), 1),
                                 "osm_fid": fids[i]})
            counts[(nm, cls, table)] = counts.get((nm, cls, table), 0) + int(hit.sum())
    db.close()

    out = os.path.join(OUT, "s3_osm_groundtruth.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        f.write("# OSM(KOR.gpkg, © OpenStreetMap contributors, ODbL) 파생 — 비공개(out/internal)\n")
        w = csv.DictWriter(f, fieldnames=["class", "table", "sigungu",
                                          "x_5179", "y_5179", "osm_fid"])
        w.writeheader()
        w.writerows(rows_out)

    print("권역별 OSM 설비 (대조 기준):")
    for nm in TARGETS:
        tower = sum(v for (n, c, _t), v in counts.items() if n == nm and c == "tower")
        sub = sum(v for (n, c, _t), v in counts.items() if n == nm and c == "substation")
        print(f"  {nm}: 철탑 {tower} · 변전소 {sub}")
    print(f"총 {len(rows_out)}건 → {out} (비공개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
