# -*- coding: utf-8 -*-
"""1단계-4b: 100m 격자 수요지도를 ArcGIS 확인용 shapefile(폴리곤)로 내보낸다.

입력   out/public/s1_grid_demand_<년>.csv.gz  (gx, gy, kwh_raw, kwh_adj, kwh_excl)
출력   Z:\\2026_WP_out\\internal\\s1_grid_shp\\s1_grid_demand_<년>.shp/.shx/.dbf/.prj/.cpg
       + _meta.txt  (격자 집계 — 공개 등급이나 100MB를 넘어 NAS에 둔다)
형식   셀 하나 = 100m 정사각형 폴리곤 1개, EPSG:5179.
       셀 (gx, gy)의 범위는 x [gx·100, gx·100+100), y [gy·100, gy·100+100).
       GIS 라이브러리 없이 struct·numpy로 직접 쓴다(ESRI Shapefile 기술명세, 1998).
검산   쓴 파일을 다시 읽어 건수·셀 좌표·합계를 CSV와 대조한다. 어긋나면 실패.

사용법:  python scripts/s1_grid_to_shp.py [연도=2024]
"""
from __future__ import annotations

import datetime
import os
import struct
import sys

import numpy as np
from pyproj import CRS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = r"Z:\2026_WP_out\internal\s1_grid_shp"
GRID = 100.0  # m (EPSG:5179)
EPSG = 5179

# dbf 필드: (이름, 형, 폭, 소수 자리)
FIELDS = [("GX", "N", 6, 0), ("GY", "N", 6, 0),
          ("KWH_RAW", "N", 14, 1), ("KWH_ADJ", "N", 14, 1), ("KWH_EXCL", "N", 14, 1)]
RSIZE = 1 + sum(f[2] for f in FIELDS)

# shp 레코드(폴리곤, 고리 1개·점 5개) — 머리 8바이트 + 내용 128바이트
REC = np.dtype([("recno", ">i4"), ("clen", ">i4"), ("stype", "<i4"), ("box", "<f8", 4),
                ("nparts", "<i4"), ("npts", "<i4"), ("part0", "<i4"), ("pts", "<f8", (5, 2))])
IDX = np.dtype([("off", ">i4"), ("clen", ">i4")])
assert REC.itemsize == 136 and IDX.itemsize == 8


def file_header(nbytes: int, bbox: tuple[float, float, float, float], stype: int = 5) -> bytes:
    """shp·shx 공통 100바이트 머리. 파일 길이는 16비트 워드 단위. stype 5 = 폴리곤, 1 = 점."""
    return (struct.pack(">7i", 9994, 0, 0, 0, 0, 0, nbytes // 2)
            + struct.pack("<2i", 1000, stype)
            + struct.pack("<8d", *bbox, 0.0, 0.0, 0.0, 0.0))


def write_shp(stem: str, gx: np.ndarray, gy: np.ndarray) -> None:
    n = len(gx)
    x0, y0 = gx * GRID, gy * GRID
    x1, y1 = x0 + GRID, y0 + GRID
    rec = np.zeros(n, dtype=REC)
    rec["recno"] = np.arange(1, n + 1)
    rec["clen"] = (REC.itemsize - 8) // 2
    rec["stype"] = 5
    rec["box"] = np.column_stack([x0, y0, x1, y1])
    rec["nparts"] = 1
    rec["npts"] = 5
    # 바깥 고리는 시계 방향: 좌하 → 좌상 → 우상 → 우하 → 좌하
    rec["pts"][:, :, 0] = np.column_stack([x0, x0, x1, x1, x0])
    rec["pts"][:, :, 1] = np.column_stack([y0, y1, y1, y0, y0])
    bbox = (x0.min(), y0.min(), x1.max(), y1.max())
    with open(stem + ".shp", "wb") as f:
        f.write(file_header(100 + REC.itemsize * n, bbox))
        rec.tofile(f)

    idx = np.zeros(n, dtype=IDX)
    idx["off"] = (100 + REC.itemsize * np.arange(n)) // 2
    idx["clen"] = (REC.itemsize - 8) // 2
    with open(stem + ".shx", "wb") as f:
        f.write(file_header(100 + IDX.itemsize * n, bbox))
        idx.tofile(f)


def write_dbf(stem: str, gx: np.ndarray, gy: np.ndarray, kwh: np.ndarray) -> None:
    n = len(gx)
    today = datetime.date.today()
    rows = [f" {x:6d}{y:6d}{a:14.1f}{b:14.1f}{c:14.1f}"
            for x, y, (a, b, c) in zip(gx.tolist(), gy.tolist(), kwh.tolist())]
    assert all(len(r) == RSIZE for r in rows), "필드 폭을 넘는 값이 있음"
    with open(stem + ".dbf", "wb") as f:
        f.write(struct.pack("<4BIHH20x", 3, today.year - 1900, today.month, today.day,
                            n, 32 + 32 * len(FIELDS) + 1, RSIZE))
        for name, typ, width, dec in FIELDS:
            f.write(struct.pack("<11sc4xBB14x", name.encode("ascii"), typ.encode("ascii"),
                                width, dec))
        f.write(b"\r")
        f.write("".join(rows).encode("ascii"))
        f.write(b"\x1a")


def verify(stem: str, gx: np.ndarray, gy: np.ndarray, kwh: np.ndarray) -> None:
    """쓴 파일을 다시 읽어 CSV 값과 대조."""
    n = len(gx)
    with open(stem + ".shp", "rb") as f:
        head = f.read(100)
        rec = np.fromfile(f, dtype=REC)
    assert struct.unpack(">i", head[:4])[0] == 9994
    assert struct.unpack(">i", head[24:28])[0] * 2 == os.path.getsize(stem + ".shp")
    assert struct.unpack("<2i", head[28:36]) == (1000, 5)
    assert len(rec) == n and (rec["recno"] == np.arange(1, n + 1)).all()
    pts = rec["pts"]
    assert (pts[:, 0, 0] == gx * GRID).all() and (pts[:, 0, 1] == gy * GRID).all()
    assert (pts[:, 0] == pts[:, 4]).all(), "고리가 닫히지 않음"
    area = 0.5 * ((pts[:, :-1, 0] * pts[:, 1:, 1]) - (pts[:, 1:, 0] * pts[:, :-1, 1])).sum(axis=1)
    assert (area == -GRID * GRID).all(), "셀 면적·방향 오류(시계 방향 10,000㎡여야 함)"

    idx = np.fromfile(stem + ".shx", dtype=IDX, offset=100)
    assert len(idx) == n and (idx["off"] * 2 == 100 + REC.itemsize * np.arange(n)).all()

    with open(stem + ".dbf", "rb") as f:
        hdr = f.read(32)
        nrec, hsize, rsize = struct.unpack("<IHH", hdr[4:12])
        f.seek(hsize)
        body = f.read(nrec * rsize)
        assert f.read() == b"\x1a"
    assert (nrec, rsize) == (n, RSIZE) and len(body) == n * RSIZE
    tab = np.frombuffer(body, dtype=f"S{RSIZE}")
    cols, pos = [], 1
    for _name, _typ, width, _dec in FIELDS:
        cols.append(np.array([r[pos:pos + width] for r in tab]).astype(float))
        pos += width
    assert (cols[0] == gx).all() and (cols[1] == gy).all()
    for j in range(3):
        assert np.array_equal(cols[2 + j], kwh[:, j]), f"dbf 값 불일치: {FIELDS[2 + j][0]}"


def main() -> int:
    year = sys.argv[1] if len(sys.argv) > 1 else "2024"
    src = os.path.join(REPO, "out", "public", f"s1_grid_demand_{year}.csv.gz")
    a = np.loadtxt(src, delimiter=",", skiprows=1)
    gx, gy, kwh = a[:, 0].astype(np.int64), a[:, 1].astype(np.int64), a[:, 2:5]
    n = len(gx)
    assert len(np.unique(gx * 1_000_000 + gy)) == n, "중복 셀"
    print(f"입력 {src}: {n:,}셀")

    os.makedirs(OUT_DIR, exist_ok=True)
    stem = os.path.join(OUT_DIR, f"s1_grid_demand_{year}")
    write_shp(stem, gx, gy)
    write_dbf(stem, gx, gy, kwh)
    with open(stem + ".prj", "w", encoding="ascii") as f:
        f.write(CRS.from_epsg(EPSG).to_wkt("WKT1_ESRI"))
    with open(stem + ".cpg", "w", encoding="ascii") as f:
        f.write("UTF-8")
    verify(stem, gx, gy, kwh)

    tw = kwh.sum(axis=0) / 1e9
    with open(stem + "_meta.txt", "w", encoding="utf-8") as f:
        f.write(
            f"100m 격자 전력수요 지도 — shapefile(폴리곤), EPSG:{EPSG}\n"
            f"기준년 {year} · 생성 스크립트 s1_grid_to_shp.py · 입력 s1_grid_demand_{year}.csv.gz\n"
            f"셀 {n:,}개(수요가 있는 셀만). 셀 범위 = x [GX·100, GX·100+100), y [GY·100, GY·100+100) m\n"
            f"필드(단위 kWh/년)\n"
            f"  KWH_RAW   무보정 합계                      {tw[0]:.3f} TWh\n"
            f"  KWH_ADJ   시군구 판매량 기준 보정 합계      {tw[1]:.3f} TWh\n"
            f"  KWH_EXCL  보정 불가(플래그) 시군구 제외 합계 {tw[2]:.3f} TWh\n"
            f"원천: 건물에너지 공개본(주택용+일반용 성격)·연속지적 2024-12-04 — 산업용 미포함\n"
            f"위치: 필지 bbox 중심을 셀에 배정(근사)\n"
        )
    print(open(stem + "_meta.txt", encoding="utf-8").read())
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        print(f"  {stem + ext}  {os.path.getsize(stem + ext) / 1e6:,.1f} MB")
    print("검산 통과: 건수·셀 좌표·필드 값이 CSV와 일치")
    return 0


if __name__ == "__main__":
    sys.exit(main())
