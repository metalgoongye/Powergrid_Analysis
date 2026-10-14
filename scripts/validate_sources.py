# -*- coding: utf-8 -*-
"""표형(비공간) 소스 레지스트리 검증 — config/sources.yml vs 실제 파일.

검사 1: 등록된 소스의 파일이 실제로 존재하는가 (files 목록 포함) → 실패 시 exit 1
검사 2: DATA_ROOT/rdb/ 의 최상위 항목 중 레지스트리에 없는 것(미등록) → 경고,
        --strict 에서는 실패. "받아놓고 등록 안 한 자료"를 자동 발견한다.

사용: python scripts/validate_sources.py [--strict]
"""
import os
import sys

import yaml

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
YML = os.path.join(ROOT, "config", "sources.yml")

# rdb 바로 아래에 있어도 미등록 경고를 내지 않는 항목 (사유 명기)
IGNORE = {
    # (현재 없음)
}


def main():
    strict = "--strict" in sys.argv
    with open(YML, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    errors, warns = [], []
    registered_prefixes = []

    for src in cfg.get("sources", []):
        sid, path = src.get("id"), src.get("path")
        if not path:
            continue
        full = os.path.join(DATA_ROOT, path)
        registered_prefixes.append(path.replace("\\", "/"))
        if not os.path.exists(full):
            errors.append(f"[{sid}] 경로 없음: {path}")
            continue
        for fname in src.get("files", []) or []:
            if not os.path.exists(os.path.join(full, str(fname))):
                errors.append(f"[{sid}] files 누락: {fname}")
        exp = src.get("expected_file_count")
        if exp is not None:
            actual = len([f for f in os.listdir(full)
                          if os.path.isfile(os.path.join(full, f))])
            if actual != exp:
                errors.append(f"[{sid}] 파일 수 {actual} != 기대 {exp}")
        n = len(src.get("files", []) or [])
        print(f"OK  [{sid}] {path}" + (f" (files {n}개 확인)" if n else ""))

    # 미등록 스캔: rdb/ 최상위
    rdb = os.path.join(DATA_ROOT, "rdb")
    for entry in sorted(os.listdir(rdb)):
        rel = f"rdb/{entry}"
        if entry in IGNORE:
            continue
        covered = any(p == rel or p.startswith(rel + "/") or rel.startswith(p)
                      for p in registered_prefixes)
        if not covered:
            warns.append(f"미등록: {rel} — sources.yml에 등록하거나 IGNORE에 사유와 함께 추가")

    for w in warns:
        print("WARN", w)
    for e in errors:
        print("FAIL", e)
    print(f"\n요약: 소스 {len(registered_prefixes)}건 / 미등록 경고 {len(warns)}건 / 오류 {len(errors)}건")
    return 1 if errors or (strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
