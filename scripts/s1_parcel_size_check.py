# -*- coding: utf-8 -*-
"""[점검] 수요지도에 쓴 필지의 크기 — '필지 외접사각형 중심' 근사가 100m 격자에서 무해한가.

배경: s1_grid_demand.py는 필지의 위치를 외접사각형(bbox) 중심으로 잡는다(도형 전체를
읽지 않는 근사). 필지가 격자(100m)보다 작으면 중심을 어떻게 잡아도 같은 칸이나 바로
옆 칸에 놓이지만, 큰 필지에서는 실제 건물 위치와 멀어질 수 있다. 보고서의 "필지 크기를
고려할 때 영향이 작은 근사"를 숫자로 확인한다(보고서 검토 2026-11-25).

방법: s1_grid_demand.py와 같은 에너지·지적 자료, 같은 필지 집합(검산: 메타 파일의
격자화 필지 수와 일치). 필지마다 외접사각형의 긴 변(m, EPSG:5186)을 구해 구간별로
필지 수와 연간 전력사용량(보정 전 ⓐ)을 집계한다.

출력: out/public/s1_parcel_size_check.csv (구간 집계 — 공개 등급)
"""
from __future__ import annotations

import csv
import os
import re
import sys
import zipfile

import numpy as np
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s1_grid_demand import (OUT, REPO, SIDOS, dbf_pnus, energy_annual,  # noqa: E402
                            member_name, shp_record_bboxes)

EDGES = [25, 50, 100, 200, 500, 1000]  # 긴 변 구간 경계(m)


def cadastre_dir() -> str:
    """지적 zip의 위치는 레지스트리(config/layers.yml)에서 읽는다(2026-10-26 Z:로 이동)."""
    with open(os.path.join(REPO, "config", "layers.yml"), encoding="utf-8") as f:
        datasets = yaml.safe_load(f)["datasets"]
    return next(x["path"] for x in datasets if x["id"] == "cadastral_parcels_2024")


def main() -> int:
    year = "2024"
    CADASTRE_DIR = cadastre_dir()
    assert os.path.isdir(CADASTRE_DIR), f"지적 폴더 없음: {CADASTRE_DIR}"
    kwh = energy_annual(year)
    side, val = [], []
    for sido in SIDOS:
        zf = zipfile.ZipFile(os.path.join(CADASTRE_DIR, f"AL_D002_{sido}_20241204.zip"))
        infos = {member_name(i): i for i in zf.infolist()}
        for stem in sorted(n[:-4] for n in infos if n.lower().endswith(".dbf")):
            for pnu, bbox in zip(dbf_pnus(zf, infos[stem + ".dbf"]),
                                 shp_record_bboxes(zf, infos[stem + ".shp"])):
                v = kwh.get(pnu)
                if v is None or bbox is None:
                    continue
                side.append(max(bbox[2] - bbox[0], bbox[3] - bbox[1]))
                val.append(v)
        print(f"  {sido}: 누적 필지 {len(side):,}")
    side, val = np.array(side), np.array(val)

    # 검산: 수요지도와 같은 필지 집합이어야 한다
    meta = open(os.path.join(OUT, f"s1_grid_demand_{year}_meta.txt"), encoding="utf-8").read()
    n_ref = int(re.search(r"격자화 필지 ([\d,]+)", meta).group(1).replace(",", ""))
    twh_ref = float(re.search(r"ⓐ raw\s+합계 ([\d.]+) TWh", meta).group(1))
    assert len(side) == n_ref, f"필지 수 불일치: {len(side):,} vs 메타 {n_ref:,}"
    assert abs(val.sum() / 1e9 - twh_ref) < 0.001, f"합계 불일치: {val.sum()/1e9:.3f} vs {twh_ref}"

    idx = np.searchsorted(EDGES, side, side="left")  # 긴 변 ≤ 경계
    names = [f"{a}m 초과~{b}m 이하" for a, b in zip([0] + EDGES[:-1], EDGES)] + [f"{EDGES[-1]}m 초과"]
    rows, cn, cv = [], 0, 0.0
    for i, name in enumerate(names):
        m = idx == i
        cn += int(m.sum()); cv += float(val[m].sum())
        rows.append([name, int(m.sum()), f"{100 * m.mean():.2f}", f"{val[m].sum() / 1e9:.3f}",
                     f"{100 * val[m].sum() / val.sum():.2f}", f"{100 * cn / len(side):.2f}",
                     f"{100 * cv / val.sum():.2f}"])
    order = np.argsort(side)
    cw = np.cumsum(val[order]) / val.sum()
    q = {p: float(np.percentile(side, p)) for p in (50, 90, 99)}
    qw = {p: float(side[order][np.searchsorted(cw, p / 100)]) for p in (50, 90, 99)}
    p = os.path.join(OUT, "s1_parcel_size_check.csv")
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# 수요지도(2024)에 쓴 필지 {len(side):,}개의 외접사각형 긴 변(m, EPSG:5186)과 연간 전력사용량(보정 전 ⓐ {val.sum()/1e9:.3f}TWh). "
                f"긴 변 중앙값 {q[50]:.1f}m·90% {q[90]:.1f}m·99% {q[99]:.1f}m / 사용량 가중 중앙값 {qw[50]:.1f}m·90% {qw[90]:.1f}m·99% {qw[99]:.1f}m\n")
        w = csv.writer(f)
        w.writerow(["외접사각형_긴변", "필지수", "필지비중_pct", "전력사용량_TWh", "사용량비중_pct",
                    "누적_필지비중_pct", "누적_사용량비중_pct"])
        w.writerows(rows)
    for r in rows:
        print(" ", r)
    print(f"긴 변: 중앙값 {q[50]:.1f}m, 90% {q[90]:.1f}m, 99% {q[99]:.1f}m")
    print(f"사용량 가중: 중앙값 {qw[50]:.1f}m, 90% {qw[90]:.1f}m, 99% {qw[99]:.1f}m")
    print("→", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
