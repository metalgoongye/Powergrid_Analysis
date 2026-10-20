# -*- coding: utf-8 -*-
"""위성 정사영상 커버리지 지도 — 시군구 경계 위에 장면 footprint를 포갠다.

- 장면은 config/layers.yml의 raster_scene_set 등록분만 쓴다(미등록 파일 제외).
- footprint는 각 장면 Aux.xml의 PS 밴드 모서리 4점(경위도)을 EPSG:5179로 변환.
  (국토위성 tif에는 지오태그가 없어 Aux가 georeference 전거다.)
- 경계는 SGIS 시군구(EPSG:5179), 대상 3곳: 서천군·보령시·은평구.
- 미피복은 경계 내부 200m 표본점 중 어느 footprint에도 안 들어가는 점(빨강).
- 추가 주문 시 국토정보플랫폼에서 찾기 쉽도록 경위도 눈금(0.1°)을 함께 그린다.

출력: out/public/ortho_coverage_<권역>.png (footprint는 영상 메타라 공개 가능)
"""
import os
import re
import struct
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml
from pyproj import Transformer

sys.stdout.reconfigure(encoding="utf-8")
plt.rcParams["font.family"] = ["Malgun Gothic", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
SHP = os.path.join(DATA_ROOT, "행정경계", "bnd_sigungu_00_2025_2Q", "bnd_sigungu_00_2025_2Q")
OUT = os.path.join(REPO, "out", "public")
TARGETS = ["서천군", "보령시", "은평구", "화성시", "세종시"]  # 화성·세종 추가 2026-10-20

T = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
T_INV = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)


# ---------- 경계 (s2_map_preview와 동일한 직접 파싱) ----------
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
                    rec[name] = b.decode("utf-8").strip()  # SGIS 2025 2Q는 UTF-8
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
            rings = []
            for pi in range(nparts):
                s = parts[pi]
                e = parts[pi + 1] if pi + 1 < nparts else npts
                rings.append(pts[s:e])
            polys.append(rings)
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


# ---------- 장면 footprint (레지스트리 → Aux 모서리) ----------
def load_registered_scenes():
    with open(os.path.join(REPO, "config", "layers.yml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    scenes = {}  # sid -> (quad 4x2 ndarray(5179), 촬영일)
    for ds in cfg["datasets"]:
        if ds["kind"] != "raster_scene_set":
            continue
        folder = os.path.join(DATA_ROOT, ds["path"])
        for sid in ds["scenes"]:
            if sid in scenes:
                continue
            aux = os.path.join(folder, f"{sid}_L2G_Aux.xml")
            t = open(aux, encoding="utf-8", errors="replace").read()
            quad = []
            for tag in ("ImageGeogTL", "ImageGeogTR", "ImageGeogBR", "ImageGeogBL"):
                m = re.search(rf"<{tag}>\s*<Latitude>([\d.+-]+)</Latitude>\s*"
                              rf"<Longitude>([\d.+-]+)</Longitude>", t)
                quad.append(T.transform(float(m.group(2)), float(m.group(1))))
            scenes[sid] = (np.asarray(quad), sid[3:11])  # YYYYMMDD
    return scenes


def graticule(ax, xmin, xmax, ymin, ymax):
    """0.1° 경위도 격자선 + 라벨 (5179 평면 위에)."""
    lon1, lat1 = T_INV.transform(xmin, ymin)
    lon2, lat2 = T_INV.transform(xmax, ymax)
    for lon in np.arange(np.floor(lon1 * 10) / 10, lon2 + 0.1, 0.1):
        lats = np.linspace(lat1 - 0.05, lat2 + 0.05, 50)
        x, y = T.transform(np.full_like(lats, lon), lats)
        ax.plot(x, y, color="gray", lw=0.4, alpha=0.5, zorder=1)
        if xmin <= x[0] <= xmax:
            ax.text(x[0], ymin, f"{lon:.1f}°E", fontsize=7, ha="center",
                    va="top", color="gray")
    for lat in np.arange(np.floor(lat1 * 10) / 10, lat2 + 0.1, 0.1):
        lons = np.linspace(lon1 - 0.05, lon2 + 0.05, 50)
        x, y = T.transform(lons, np.full_like(lons, lat))
        ax.plot(x, y, color="gray", lw=0.4, alpha=0.5, zorder=1)
        if ymin <= y[0] <= ymax:
            ax.text(xmin, y[0], f"{lat:.1f}°N ", fontsize=7, ha="right",
                    va="center", color="gray")


def main():
    os.makedirs(OUT, exist_ok=True)
    recs = read_dbf_names(SHP + ".dbf")
    polys = read_shp_polys(SHP + ".shp")
    name_f = next(k for k in recs[0] if k.endswith("_NM"))
    targets = {r[name_f]: p for r, p in zip(recs, polys) if r[name_f] in TARGETS}
    assert len(targets) == len(TARGETS), f"경계 미발견: {set(TARGETS) - set(targets)}"
    scenes = load_registered_scenes()
    print(f"등록 장면 {len(scenes)}개")

    for nm in TARGETS:
        rings = targets[nm]
        edges = rings_to_edges(rings)
        xs = np.concatenate([edges[0], edges[2]])
        ys = np.concatenate([edges[1], edges[3]])
        pad = 3000
        xmin, xmax = xs.min() - pad, xs.max() + pad
        ymin, ymax = ys.min() - pad, ys.max() + pad

        # 미피복 표본점
        gx = np.arange(xs.min(), xs.max(), 200.0)
        gy = np.arange(ys.min(), ys.max(), 200.0)
        GX, GY = np.meshgrid(gx, gy)
        px, py = GX.ravel(), GY.ravel()
        inside = points_in_edges(px, py, edges)
        px, py = px[inside], py[inside]
        union = np.zeros(len(px), dtype=bool)
        touch = []
        for sid, (quad, date) in scenes.items():
            hit = points_in_edges(px, py, rings_to_edges([quad]))
            union |= hit
            if hit.any():
                touch.append(sid)
        cov = 100 * union.mean()

        fig, ax = plt.subplots(figsize=(11, 11), dpi=150)
        graticule(ax, xmin, xmax, ymin, ymax)
        for ring in rings:
            ax.plot(ring[:, 0], ring[:, 1], color="black", lw=1.2, zorder=4)
        for sid in touch:
            quad, date = scenes[sid]
            recent = sid[3:7] >= "2026"
            color = "darkorange" if recent else "steelblue"
            q = np.vstack([quad, quad[:1]])
            ax.fill(q[:, 0], q[:, 1], color=color, alpha=0.18, zorder=2)
            ax.plot(q[:, 0], q[:, 1], color=color, lw=1.0, zorder=3)
            cx, cy = quad[:, 0].mean(), quad[:, 1].mean()
            label = f"{sid.split('_')[2]}_{sid.split('_')[3]}\n{date[:4]}-{date[4:6]}-{date[6:]}"
            ax.annotate(label, (cx, cy), fontsize=6.5, ha="center", va="center",
                        color="navy" if not recent else "saddlebrown", zorder=5)
        ux, uy = px[~union], py[~union]
        if len(ux):
            ax.scatter(ux, uy, s=1.5, c="red", alpha=0.8, zorder=6, linewidths=0)
        ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax)
        ax.set_aspect("equal")
        ax.set_title(f"{nm} — 국토위성 정사영상 커버리지 {cov:.1f}% "
                     f"(등록 장면 {len(touch)}개, 빨강 = 미피복 200m 표본점)")
        ax.set_xlabel("EPSG:5179 X (m) · 회색 격자 = 경위도 0.1°")
        ax.set_ylabel("EPSG:5179 Y (m)")
        ax.ticklabel_format(style="plain")
        fig.text(0.01, 0.01,
                 "footprint = Aux.xml 모서리(UTM-K 선언) → EPSG:5179. "
                 "파랑 = 2024 촬영, 주황 = 2026-04 촬영(기준년 밖). "
                 "경계 = SGIS 시군구 2025 2Q.", fontsize=7)
        p = os.path.join(OUT, f"ortho_coverage_{nm}.png")
        fig.savefig(p, bbox_inches="tight")
        plt.close(fig)
        print(f"{nm}: 피복 {cov:.1f}%, 장면 {len(touch)}개, 미피복점 {len(ux)} → {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
