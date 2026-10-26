# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 항공 25cm 도엽 georeference 복원 — 도엽번호 체계 기반.

항공 정사영상(B060) GeoTIFF에는 좌표 앵커(지오태그·월드파일·메타 모서리)가
없다. 검증된 규약(ops_log §6: 공식 후보 4종 × (도엽,읍면동) 32쌍 교차검정,
원산도 해안선 육안 확인)으로 각 도엽의 아핀을 복원한다.

규약
- 도엽번호 8자리: 1°셀[위도2+경도끝1] → 1:50k 4×4(NW 행우선, 01~16)
  → 1:5k 10×10(NW 행우선, 001~100). 경도 끝자리 4~9는 120°대, 0~2는 130°대.
- 래스터 = 도곽(지리적 사각형)의 EPSG:5186 bbox + 사방 ~200px(50m) 여백,
  bbox 중심 정렬. 화소 0.25m, 북향(회전 없음).
- 여백이 [150, 250]px 밖이면 구조 가정 위반으로 FLAG(분석 제외 후보).

출력: out/public/s3_sheet_georef.csv
  (sheet, year, rel_path, width, height, margin_x_px, margin_y_px,
   x0, y0, px, flag) — x0,y0 = 화소(0,0) 좌상단 모서리의 EPSG:5186 좌표.
도엽 좌표는 공개 격자 체계이므로 out/public (설비 좌표 아님).
"""
import csv
import os
import re
import struct
import sys

import numpy as np
from pyproj import Transformer

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AERIAL = r"Z:\powergrid_spatial_data\위성정사영상\위성정사영상_분석대상지역\261026_25cm"
OUT = os.path.join(REPO, "out", "public", "s3_sheet_georef.csv")
PX = 0.25
T = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
PAT = re.compile(r"\(B060\)정사영상_(\d{4})_(\d{8})\.tif$")


def sheet_rect(code: str):
    """도엽번호 → 도곽 지리 사각형 (lon_min, lat_min, lon_max, lat_max)."""
    lat2 = int(code[:2])
    d = int(code[2])
    lon0 = 120 + d if d >= 4 else 130 + d
    i50 = int(code[3:5]) - 1
    lat_top = lat2 + 1 - (i50 // 4) * 0.25
    lon_lft = lon0 + (i50 % 4) * 0.25
    n5 = int(code[5:8]) - 1
    lat_top -= (n5 // 10) * 0.025
    lon_lft += (n5 % 10) * 0.025
    return lon_lft, lat_top - 0.025, lon_lft + 0.025, lat_top


def rect_bbox_5186(rect):
    """도곽 사각형의 EPSG:5186 bbox (모서리 곡률 대비 변당 10점 조밀화)."""
    lon1, lat1, lon2, lat2 = rect
    t = np.linspace(0, 1, 11)
    lons = np.concatenate([lon1 + (lon2 - lon1) * t, np.full(11, lon2),
                           lon2 - (lon2 - lon1) * t, np.full(11, lon1)])
    lats = np.concatenate([np.full(11, lat1), lat1 + (lat2 - lat1) * t,
                           np.full(11, lat2), lat2 - (lat2 - lat1) * t])
    X, Y = T.transform(lons, lats)
    return X.min(), Y.min(), X.max(), Y.max()


def tiff_dims(path: str):
    """표준 TIFF의 ImageWidth(256)·ImageLength(257)만 헤더에서 읽는다 (GDAL 불요)."""
    with open(path, "rb") as f:
        hdr = f.read(8)
        bo = "<" if hdr[:2] == b"II" else ">"
        magic = struct.unpack(bo + "H", hdr[2:4])[0]
        if magic != 42:
            raise ValueError(f"표준 TIFF 아님 (magic={magic})")
        ifd_off = struct.unpack(bo + "I", hdr[4:8])[0]
        f.seek(ifd_off)
        n = struct.unpack(bo + "H", f.read(2))[0]
        w = h = None
        for _ in range(n):
            tag, typ, _cnt = struct.unpack(bo + "HHI", f.read(8))
            val_raw = f.read(4)
            if tag in (256, 257):
                val = struct.unpack(bo + ("H" if typ == 3 else "I"),
                                    val_raw[:2 if typ == 3 else 4])[0]
                if tag == 256:
                    w = val
                else:
                    h = val
            if w is not None and h is not None:
                break
        if w is None or h is None:
            raise ValueError("크기 태그(256/257) 없음")
    return w, h


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    rows, flags, errors = [], [], []
    for root, _d, files in os.walk(AERIAL):
        for fn in sorted(files):
            m = PAT.search(fn)
            if not m:
                continue
            p = os.path.join(root, fn)
            if os.path.getsize(p) == 0:
                continue
            year, code = m.group(1), m.group(2)
            try:
                w, h = tiff_dims(p)
            except ValueError as exc:
                errors.append((fn, str(exc)))
                continue
            x1, y1, x2, y2 = rect_bbox_5186(sheet_rect(code))
            mx = (w * PX - (x2 - x1)) / 2 / PX   # 좌우 여백(px)
            my = (h * PX - (y2 - y1)) / 2 / PX   # 상하 여백(px)
            flag = "" if (150 <= mx <= 250 and 150 <= my <= 250) else "MARGIN"
            if flag:
                flags.append((fn, round(mx, 1), round(my, 1)))
            rows.append({
                "sheet": code, "year": year,
                "rel_path": os.path.relpath(p, AERIAL),
                "width": w, "height": h,
                "margin_x_px": round(mx, 2), "margin_y_px": round(my, 2),
                "x0": round(x1 - mx * PX, 3), "y0": round(y2 + my * PX, 3),
                "px": PX, "flag": flag,
            })
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    mxs = np.array([r["margin_x_px"] for r in rows])
    mys = np.array([r["margin_y_px"] for r in rows])
    print(f"도엽 {len(rows)}건 georef 복원 → {OUT}")
    print(f"여백(px) 좌우 중앙값 {np.median(mxs):.1f} (범위 {mxs.min():.1f}~{mxs.max():.1f}) · "
          f"상하 중앙값 {np.median(mys):.1f} (범위 {mys.min():.1f}~{mys.max():.1f})")
    print(f"구조 가정 위반 FLAG {len(flags)}건 · 헤더 오류 {len(errors)}건")
    for fn, a, b in flags[:10]:
        print(f"  FLAG {fn}: 여백 {a}/{b}px")
    for fn, msg in errors[:10]:
        print(f"  오류 {fn}: {msg}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
