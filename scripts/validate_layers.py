"""레지스트리(config/layers.yml, config/sources.yml의 local 소스) vs 실제 파일 정합성 검증.

원칙
- 표준 라이브러리 + PyYAML만 쓴다. GDAL 스택 없이도 어느 기관 PC에서든 돌아야 한다.
- 실패(FAIL)가 하나라도 있으면 종료 코드 1. 이후 단계는 산출물을 만들면 안 된다.
- 기준값(expected_count 등)을 이 스크립트에서 고치지 않는다. 갱신은 사용자 확인 후
  레지스트리에서 한다. (CLAUDE.md)

사용법
    python scripts/validate_layers.py            # 검증
    python scripts/validate_layers.py --strict   # WARN도 실패로 처리
"""
from __future__ import annotations

import csv
import os
import sqlite3
import struct
import sys

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 cp949 대비

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")

results: list[tuple[str, str, str]] = []  # (level, dataset_id, message)


def report(level: str, dataset_id: str, message: str) -> None:
    results.append((level, dataset_id, message))
    print(f"[{level:4}] {dataset_id}: {message}")


def dbf_record_count(dbf_path: str) -> int:
    with open(dbf_path, "rb") as f:
        header = f.read(8)
    return struct.unpack("<I", header[4:8])[0]


def check_gpkg(ds: dict) -> None:
    path = os.path.join(DATA_ROOT, ds["path"])
    if not os.path.isfile(path):
        report("FAIL", ds["id"], f"파일 없음: {path}")
        return
    db = sqlite3.connect(path)
    try:
        contents = {
            name: srs
            for name, srs in db.execute(
                "SELECT table_name, srs_id FROM gpkg_contents WHERE data_type='features'"
            )
        }
        for layer, spec in ds["layers"].items():
            if layer not in contents:
                report("FAIL", ds["id"], f"레이어 없음: {layer}")
                continue
            if contents[layer] != ds["crs_epsg"]:
                report("FAIL", ds["id"], f"{layer}: srs {contents[layer]} != 등록값 {ds['crs_epsg']}")
            n = db.execute(f'SELECT COUNT(*) FROM "{layer}"').fetchone()[0]
            if n != spec["expected_count"]:
                report("FAIL", ds["id"], f"{layer}: 피처 {n}건 != 기준값 {spec['expected_count']}건")
            else:
                report("OK", ds["id"], f"{layer}: {n}건")
    finally:
        db.close()


def check_shapefile(ds: dict) -> None:
    shp = os.path.join(DATA_ROOT, ds["path"])
    base, _ = os.path.splitext(shp)
    missing = [ext for ext in (".shp", ".shx", ".dbf", ".prj") if not os.path.isfile(base + ext)]
    if missing:
        report("FAIL", ds["id"], f"구성 파일 없음: {', '.join(missing)} ({base})")
        return
    n = dbf_record_count(base + ".dbf")
    if n != ds["expected_count"]:
        report("FAIL", ds["id"], f"레코드 {n}건 != 기준값 {ds['expected_count']}건")
    else:
        report("OK", ds["id"], f"{n}건")
    # .prj는 WKT 텍스트라 EPSG 번호가 없다. 5179 등록 건은 명칭으로만 확인한다.
    prj_text = open(base + ".prj", encoding="ascii", errors="replace").read()
    if ds["crs_epsg"] == 5179 and "Korea_2000" not in prj_text.replace(" ", "_"):
        report("WARN", ds["id"], f".prj에서 Korea_2000 명칭을 찾지 못함 — 좌표계 수동 확인 필요")


def check_local_csv(src: dict) -> None:
    path = os.path.join(DATA_ROOT, src["path"])
    if not os.path.isfile(path):
        report("FAIL", src["id"], f"파일 없음: {path}")
        return
    with open(path, encoding=src["encoding"], newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        n = sum(1 for _ in reader)
    if header != src["columns"]:
        report("FAIL", src["id"], f"컬럼 불일치: {header} != {src['columns']}")
    if n != src["expected_rows"]:
        report("FAIL", src["id"], f"데이터 {n}행 != 기준값 {src['expected_rows']}행")
    else:
        report("OK", src["id"], f"{n}행")


def main() -> int:
    strict = "--strict" in sys.argv
    print(f"DATA_ROOT = {DATA_ROOT}\n")

    with open(os.path.join(REPO, "config", "layers.yml"), encoding="utf-8") as f:
        layers_cfg = yaml.safe_load(f)
    for ds in layers_cfg["datasets"]:
        if ds["kind"] == "gpkg":
            check_gpkg(ds)
        elif ds["kind"] == "shapefile":
            check_shapefile(ds)
        else:
            report("WARN", ds["id"], f"검사 미구현 kind: {ds['kind']}")

    with open(os.path.join(REPO, "config", "sources.yml"), encoding="utf-8") as f:
        sources_cfg = yaml.safe_load(f)
    for src in sources_cfg.get("sources", []):
        if src["kind"] == "local_csv":
            check_local_csv(src)

    fails = [r for r in results if r[0] == "FAIL"]
    warns = [r for r in results if r[0] == "WARN"]
    oks = [r for r in results if r[0] == "OK"]
    print(f"\n결과: OK {len(oks)} · WARN {len(warns)} · FAIL {len(fails)}")
    if fails or (strict and warns):
        print("검증 실패 — 산출물을 만들지 마십시오. 기준값 변경은 사용자 확인 후 레지스트리에서.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
