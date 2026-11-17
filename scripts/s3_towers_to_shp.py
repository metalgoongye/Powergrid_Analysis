# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 철탑 점 산출물을 ArcGIS 확인용 shapefile(점)로 내보낸다.

입력   out/internal/detections25_dedup.csv    GeoAI 탐지 철탑(항공 25cm, 점수 0.10 이상 전부 —
                                              평가 권역 경계 밖 탐지 포함)
       out/internal/s3_towers_corrected.csv   보정판 = OSM 철탑 + GeoAI로 찾은 OSM 결측 철탑
출력   Z:\\2026_WP_out\\internal\\s3_towers_shp\\ 아래
       s3_geoai_detections25.shp · s3_towers_corrected.shp (+ .shx·.dbf·.prj·.cpg)
       s3_towers_shp_meta.txt
       설비 좌표 — 비공개. 보정판은 OSM(ODbL) 파생. GIS 확인용 산출물은 NAS에 둔다.
형식   점, EPSG:5179. 값은 CSV 그대로(형식만 변환).
검산   쓴 파일을 다시 읽어 건수·좌표·속성을 CSV와 대조한다. 어긋나면 실패.

선로(연결선)는 GeoAI 산출물이 아니다 — OSM 원본 KOR.gpkg의 power_line 레이어를 쓴다.

사용법:  python scripts/s3_towers_to_shp.py
"""
from __future__ import annotations

import csv
import datetime
import os
import struct
import sys

import numpy as np
from pyproj import CRS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s1_grid_to_shp import EPSG, file_header

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INT = os.path.join(REPO, "out", "internal")
OUT_DIR = r"Z:\2026_WP_out\internal\s3_towers_shp"
ODBL ="원천: OpenInfraMap/OSM KOR.gpkg (© OpenStreetMap contributors, ODbL) 파생 — 배포 시 고지·동일조건"

# (입력 csv, 출력 이름, 설명, [(dbf 필드, 형, 폭(바이트), 소수 자리, csv 열)])
LAYERS = [
    ("detections25_dedup.csv", "s3_geoai_detections25",
     "GeoAI 탐지 철탑(항공 25cm). SCORE = 탐지 점수(보고서 운용 문턱 0.20)",
     [("REGION", "C", 12, 0, "region"), ("SHEET", "C", 8, 0, "sheet"),
      ("YEAR", "N", 4, 0, "year"), ("SCORE", "N", 6, 4, "score")]),
    ("s3_towers_corrected.csv", "s3_towers_corrected",
     "보정판 철탑. SOURCE = osm(OSM 수록) / geoai(GeoAI로 찾은 OSM 결측). " + ODBL,
     [("SOURCE", "C", 5, 0, "source"), ("SIGUNGU", "C", 12, 0, "sigungu")]),
]

REC = np.dtype([("recno", ">i4"), ("clen", ">i4"), ("stype", "<i4"), ("x", "<f8"), ("y", "<f8")])
IDX = np.dtype([("off", ">i4"), ("clen", ">i4")])
assert REC.itemsize == 28


def cell(value: str, typ: str, width: int, dec: int) -> bytes:
    if typ == "C":
        b = value.encode("utf-8").ljust(width)
    else:
        b = f"{float(value):{width}.{dec}f}".encode("ascii")
    assert len(b) == width, f"필드 폭 초과: {value!r}"
    return b


def write_table(stem: str, fields: list, values: list) -> tuple[int, int]:
    """속성표(.dbf)와 .prj·.cpg를 쓴다. fields = (이름, 형, 폭, 소수 자리), values = 행별 문자열 목록.
    반환: (dbf 머리 길이, 레코드 길이)."""
    rsize = 1 + sum(fd[2] for fd in fields)
    hsize = 32 + 32 * len(fields) + 1
    today = datetime.date.today()
    body = b"".join(b" " + b"".join(cell(v, typ, w, d) for v, (_nm, typ, w, d) in zip(row, fields))
                    for row in values)
    with open(stem + ".dbf", "wb") as f:
        f.write(struct.pack("<4BIHH20x", 3, today.year - 1900, today.month, today.day,
                            len(values), hsize, rsize))
        for nm, typ, w, d in fields:
            f.write(struct.pack("<11sc4xBB14x", nm.encode("ascii"), typ.encode("ascii"), w, d))
        f.write(b"\r" + body + b"\x1a")
    with open(stem + ".prj", "w", encoding="ascii") as f:
        f.write(CRS.from_epsg(EPSG).to_wkt("WKT1_ESRI"))
    with open(stem + ".cpg", "w", encoding="ascii") as f:
        f.write("UTF-8")
    return hsize, rsize


def export(src: str, name: str, fields: list) -> int:
    with open(os.path.join(INT, src), encoding="utf-8-sig") as f:
        rows = list(csv.DictReader([ln for ln in f if not ln.startswith("#")]))
    n = len(rows)
    x = np.array([float(r["x_5179"]) for r in rows])
    y = np.array([float(r["y_5179"]) for r in rows])
    bbox = (x.min(), y.min(), x.max(), y.max())
    stem = os.path.join(OUT_DIR, name)

    rec = np.zeros(n, dtype=REC)
    rec["recno"] = np.arange(1, n + 1)
    rec["clen"] = (REC.itemsize - 8) // 2
    rec["stype"] = 1
    rec["x"], rec["y"] = x, y
    with open(stem + ".shp", "wb") as f:
        f.write(file_header(100 + REC.itemsize * n, bbox, stype=1))
        rec.tofile(f)
    idx = np.zeros(n, dtype=IDX)
    idx["off"] = (100 + REC.itemsize * np.arange(n)) // 2
    idx["clen"] = (REC.itemsize - 8) // 2
    with open(stem + ".shx", "wb") as f:
        f.write(file_header(100 + IDX.itemsize * n, bbox, stype=1))
        idx.tofile(f)

    hsize, rsize = write_table(stem, [fd[:4] for fd in fields],
                               [[r[fd[4]] for fd in fields] for r in rows])

    # 검산: 다시 읽어 CSV와 대조
    back = np.fromfile(stem + ".shp", dtype=REC, offset=100)
    assert os.path.getsize(stem + ".shp") == 100 + REC.itemsize * n
    assert len(back) == n and (back["x"] == x).all() and (back["y"] == y).all()
    assert (back["stype"] == 1).all() and (back["recno"] == np.arange(1, n + 1)).all()
    assert os.path.getsize(stem + ".shx") == 100 + IDX.itemsize * n
    with open(stem + ".dbf", "rb") as f:
        f.seek(hsize)
        raw = f.read()
    assert len(raw) == n * rsize + 1
    for i, r in enumerate(rows):
        pos = i * rsize + 1
        for _nm, typ, w, d, col in fields:
            got = raw[pos:pos + w].decode("utf-8").strip()
            assert (got == r[col]) if typ == "C" else (float(got) == round(float(r[col]), d)), \
                f"속성 불일치 {name} {i} {col}"
            pos += w
    print(f"  {stem}.shp  {n:,}점")
    return n


def main() -> int:
    notes = [f"철탑 점 shapefile — EPSG:{EPSG}, 설비 좌표 포함(비공개)",
             "생성 스크립트 s3_towers_to_shp.py · 값은 입력 CSV 그대로"]
    os.makedirs(OUT_DIR, exist_ok=True)
    for src, name, desc, fields in LAYERS:
        n = export(src, name, fields)
        notes.append(f"{name}.shp ({n:,}점, 입력 {src}): {desc}")
    with open(os.path.join(OUT_DIR, "s3_towers_shp_meta.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(notes) + "\n")
    print("검산 통과: 건수·좌표·속성이 CSV와 일치")
    return 0


if __name__ == "__main__":
    sys.exit(main())
