# -*- coding: utf-8 -*-
"""[3단계 GeoAI] 국토위성 장면 georeference 추정 — Aux 모서리 × 여백 스캔.

국토위성 L2G tif에는 지오태그가 없고, Aux.xml의 ImageGeog{TL,TR,BR,BL}
(경위도)만 있다. 검증 결과 tif는 북향(UTM-K) 격자에 자료 사각형이 기울어
앉은 형태(검은 여백)라서, 여백을 스캔해 자료 사각형의 네 극점 픽셀을 찾고
Aux 모서리(EPSG:5179 변환)와 짝지어 6모수 아핀을 최소제곱으로 푼다.

- 극점↔모서리 대응: 최북 픽셀 = max-Y 모서리, 최남 = min-Y, 최서 = min-X,
  최동 = max-X (자료 사각형이 볼록이므로 성립).
- RMS 잔차를 함께 기록한다. 잔차가 크면(기본 50m 초과) FAIL로 멈춘다 —
  이 값이 OSM 대조 반경 설계의 하한이다.
- 출력: out/public/s3_scene_georef.csv (장면별 아핀 6모수 + 잔차)
  tfw는 쓰지 않는다(회전항 포함 아핀은 매니페스트가 정본, 타일러가 직접 읽음).

사용: python scripts/s3_scene_georef.py            # 등록 전 장면
      python scripts/s3_scene_georef.py <장면ID>   # 한 장면만
"""
import csv
import os
import re
import struct
import sys

import numpy as np
import yaml
from pyproj import Transformer

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
OUT = os.path.join(REPO, "out", "public")
RMS_LIMIT = 50.0  # m — 초과 시 FAIL
T = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)


def aux_corners(aux_path):
    """R_PS 밴드 블록의 TL/TR/BR/BL (EPSG:5179)."""
    t = open(aux_path, encoding="utf-8", errors="replace").read()
    m = re.search(r"<ImageFileName>[^<]*R_PS[^<]*</ImageFileName>"
                  r".*?<ImagingCoordinates>(.*?)</ImagingCoordinates>", t, re.S)
    blk = m.group(1)
    out = {}
    for tag in ("ImageGeogTL", "ImageGeogTR", "ImageGeogBR", "ImageGeogBL"):
        mm = re.search(rf"<{tag}>\s*<Latitude>([\d.+-]+)</Latitude>\s*"
                       rf"<Longitude>([\d.+-]+)</Longitude>", blk)
        out[tag] = T.transform(float(mm.group(2)), float(mm.group(1)))
    return out


class StripTiff:
    """무압축·행 스트립 16비트 단일밴드 TIFF의 행 단위 임의 접근."""

    def __init__(self, path):
        self.f = open(path, "rb")
        hdr = self.f.read(8)
        assert hdr[:4] == b"II*\0", f"지원하지 않는 TIFF: {path}"
        ifd = struct.unpack("<I", hdr[4:8])[0]
        self.f.seek(ifd)
        n = struct.unpack("<H", self.f.read(2))[0]
        tags = {}
        for _ in range(n):
            e = self.f.read(12)
            tid, ttype, cnt = struct.unpack("<HHI", e[:8])
            tags[tid] = (ttype, cnt, struct.unpack("<I", e[8:12])[0])
        assert tags[259][2] == 1, "압축 TIFF — 미지원"
        assert tags[278][2] == 1, "행 스트립이 아님 — 미지원"
        self.w, self.h = tags[256][2], tags[257][2]
        self.f.seek(tags[273][2])
        self.offsets = np.frombuffer(self.f.read(4 * tags[273][1]), dtype="<u4")

    def row(self, r):
        self.f.seek(int(self.offsets[r]))
        return np.frombuffer(self.f.read(self.w * 2), dtype="<u2")

    def close(self):
        self.f.close()


RUN_N, RUN_W = 50, 60  # 자료 인정 기준: 50픽셀이 60px 폭 안에 연속


def robust_span(a):
    """행에서 '진짜 자료' 구간의 (시작, 끝). 흩어진 밝은 점(노이즈·구름 조각)은
    무시 — RUN_N픽셀이 RUN_W폭 안에 몰려 있어야 자료로 인정한다."""
    nz = np.flatnonzero(a)
    if len(nz) < RUN_N:
        return None
    d = nz[RUN_N - 1:] - nz[:len(nz) - RUN_N + 1]
    ok = np.flatnonzero(d <= RUN_W)
    if not len(ok):
        return None
    return int(nz[ok[0]]), int(nz[ok[-1] + RUN_N - 1])


def extreme_pixels(tif):
    """자료 사각형의 네 극점 픽셀 (col, row): 최북(top)·최남(bot)·최서·최동."""
    def first_run(rng):
        for r in rng:
            sp = robust_span(tif.row(r))
            if sp:
                return r, sp
        raise RuntimeError("자료 픽셀 없음")

    r_top, sp = first_run(range(tif.h))
    top = ((sp[0] + sp[1]) / 2, r_top)
    r_bot, sp = first_run(range(tif.h - 1, -1, -1))
    bot = ((sp[0] + sp[1]) / 2, r_bot)

    # 좌·우 극점: 성긴 스캔(모든 행의 1/40)으로 최소 시작열·최대 끝열 행을 찾고
    # 그 주변 ±60행을 정밀 스캔
    step = max(1, (r_bot - r_top) // 40 * 40 // 40)
    coarse = range(r_top, r_bot + 1, max(1, (r_bot - r_top) // 1200))
    starts, ends = {}, {}
    for r in coarse:
        sp = robust_span(tif.row(r))
        if sp:
            starts[r] = sp[0]
            ends[r] = sp[1]
    r_l = min(starts, key=starts.get)
    r_r = max(ends, key=ends.get)

    def refine(r0, want_min):
        best_r, best_c = r0, starts.get(r0) if want_min else ends.get(r0)
        for r in range(max(r_top, r0 - 60), min(r_bot, r0 + 60) + 1):
            sp = robust_span(tif.row(r))
            if not sp:
                continue
            c = sp[0] if want_min else sp[1]
            if (want_min and c < best_c) or (not want_min and c > best_c):
                best_r, best_c = r, c
        return (best_c, best_r)

    left = refine(r_l, True)
    right = refine(r_r, False)
    return {"top": top, "bot": bot, "left": left, "right": right}


def fit_affine(px_pts, m_pts):
    """(col,row) -> (X,Y) 6모수 아핀 최소제곱. 반환: (a,b,c,d,e,f), RMS[m]
    X = a*col + b*row + c ; Y = d*col + e*row + f"""
    A = np.array([[c, r, 1] for c, r in px_pts])
    X = np.array([p[0] for p in m_pts])
    Y = np.array([p[1] for p in m_pts])
    cx, *_ = np.linalg.lstsq(A, X, rcond=None)
    cy, *_ = np.linalg.lstsq(A, Y, rcond=None)
    res = np.hypot(A @ cx - X, A @ cy - Y)
    return (*cx, *cy), float(np.sqrt((res ** 2).mean())), float(res.max())


def scenes_from_registry():
    with open(os.path.join(REPO, "config", "layers.yml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for ds in cfg["datasets"]:
        if ds["kind"] == "raster_scene_set":
            folder = os.path.join(DATA_ROOT, ds["path"])
            for sid in ds["scenes"]:
                yield sid, folder


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    os.makedirs(OUT, exist_ok=True)
    rows, fails = [], []
    for sid, folder in scenes_from_registry():
        if only and sid != only:
            continue
        aux = os.path.join(folder, f"{sid}_L2G_Aux.xml")
        q = aux_corners(aux)
        tif = StripTiff(os.path.join(folder, f"{sid}_L2G_R_PS.tif"))
        ex = extreme_pixels(tif)
        # 대응: 최북=최대Y 모서리, 최남=최소Y, 최서=최소X, 최동=최대X
        by_y = sorted(q, key=lambda k: q[k][1])
        by_x = sorted(q, key=lambda k: q[k][0])
        pairs = [(ex["top"], q[by_y[-1]]), (ex["bot"], q[by_y[0]]),
                 (ex["left"], q[by_x[0]]), (ex["right"], q[by_x[-1]])]
        coef, rms, mx = fit_affine([p for p, _ in pairs], [m for _, m in pairs])
        gsd = float(np.hypot(coef[0], coef[3]))
        rot = float(np.degrees(np.arctan2(coef[3], coef[0])))
        status = "OK" if rms <= RMS_LIMIT else "FAIL"
        if status == "FAIL":
            fails.append(sid)
        rows.append({"scene": sid, "width": tif.w, "height": tif.h,
                     "a": coef[0], "b": coef[1], "c": coef[2],
                     "d": coef[3], "e": coef[4], "f": coef[5],
                     "gsd_m": round(gsd, 4), "rot_deg": round(rot, 4),
                     "rms_m": round(rms, 2), "max_m": round(mx, 2),
                     "crs": "EPSG:5179", "status": status})
        print(f"[{status:4}] {sid}: RMS {rms:6.2f} m · 최대 {mx:6.2f} m · "
              f"GSD {gsd:.4f} · 회전 {rot:+.3f}°")
        tif.close()
    out = os.path.join(OUT, "s3_scene_georef.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)}장면 → {out}")
    if fails:
        print(f"FAIL {len(fails)}장: {fails} — RMS {RMS_LIMIT}m 초과, 타일링 중단 대상")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
