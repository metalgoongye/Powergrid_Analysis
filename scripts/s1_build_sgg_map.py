# -*- coding: utf-8 -*-
"""1단계-3a: 시군구 명칭(판매통계) ↔ 법정동 시군구코드(에너지DB) 대응표 생성.

에너지 공개본은 (시군구코드, 시도명, 시군구명)을 함께 담고 있으므로 코드-명칭 쌍을
데이터에서 직접 추출하고, 판매통계의 (시도, 시군구) 명칭을 그 위에 대조한다.
일반구를 가진 시(수원시 등)는 1:N 매핑이 된다. 자동 판정 불가 건은 UNMATCHED로
표시만 하고 멈춘다(CLAUDE.md: 사전에 없는 매핑은 사용자 확인).

사용법:  python scripts/s1_build_sgg_map.py [YYYYMM=202412]
산출물:  references/sgg_name_code_map.csv  (확인 후 분석에 사용)
"""
from __future__ import annotations

import csv
import io
import os
import sys
import zipfile
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
ENERGY_DIR = os.path.join(DATA_ROOT, "rdb", "건물에너지_지번별월별_24_25")
SALES = os.path.join(DATA_ROOT, "rdb", "시군구별_월별_계약종별판매전력량_2023_2024",
                     "한전_시군구별_계약종별_월별_판매전력량_2024.csv")
OUT = os.path.join(REPO, "references", "sgg_name_code_map.csv")


def member_name(info: zipfile.ZipInfo) -> str:
    try:
        return info.filename.encode("cp437").decode("cp949")
    except Exception:
        return info.filename


def energy_code_names(yyyymm: str) -> dict[tuple[str, str], set[str]]:
    """(시도명, 시군구명) → {시군구코드}  — 에너지 파일에서 직접 추출."""
    zf = zipfile.ZipFile(os.path.join(ENERGY_DIR, f"MART_KEY_ELCTY_ENERGY_DATA_{yyyymm}.zip"))
    txt = [i for i in zf.infolist() if member_name(i).lower().endswith(".txt")][0]
    pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
    with zf.open(txt) as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):
            p = line.split("|")
            if len(p) < 7:
                continue
            pairs[(p[4].strip(), p[5].strip())].add(p[2].strip())
    return pairs


# 행정구역 개명 별칭 (판매통계 표기 → 에너지DB 표기). 근거: 특별자치도 출범
# (강원 2023-06, 전북 2024-01), 세종은 시군구 자리에 자기 이름이 다시 들어감.
SIDO_ALIAS = {"전라북도": "전북특별자치도", "강원도": "강원특별자치도"}
SGG_ALIAS = {("세종특별자치시", "세종시"): ("세종특별자치시", "세종특별자치시")}


def main() -> int:
    yyyymm = sys.argv[1] if len(sys.argv) > 1 else "202412"
    pairs = energy_code_names(yyyymm)
    # 코드-명칭 1:1 무결성 확인
    multi = {k: v for k, v in pairs.items() if len(v) > 1}
    if multi:
        print(f"[주의] 한 명칭에 코드 여러 개: {multi}")
    code_of = {k: sorted(v)[0] for k, v in pairs.items()}
    print(f"에너지DB ({yyyymm}): 시군구 명칭-코드 쌍 {len(code_of)}개")

    sales_pairs = set()
    with open(SALES, encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if len(r) > 4 and r[0] == "2024" and r[2].strip():
                sales_pairs.add((r[2].strip(), r[3].strip()))
    print(f"판매통계: 시도·시군구 명칭 쌍 {len(sales_pairs)}개")

    # 매칭: ① 완전일치 ② 일반구 포함(판매 '수원시' → 에너지 '수원시장안구'…) ③ 미매칭
    rows = []
    claimed: set[tuple[str, str]] = set()
    n_exact = n_multi = n_un = 0
    for sido_raw, sgg_raw in sorted(sales_pairs):
        sido = SIDO_ALIAS.get(sido_raw, sido_raw)
        sido, sgg = SGG_ALIAS.get((sido, sgg_raw), (sido, sgg_raw))
        if (sido, sgg) in code_of:
            tag = "exact" if (sido, sgg) == (sido_raw, sgg_raw) else f"alias:{sido} {sgg}"
            rows.append([sido_raw, sgg_raw, code_of[(sido, sgg)], tag])
            claimed.add((sido, sgg))
            n_exact += 1
            continue
        # 일반구: 에너지 시군구명이 판매 시군구명으로 시작 (공백 유무 모두 허용)
        subs = [(es, code_of[(esd, es)]) for (esd, es) in code_of
                if esd == sido and es.replace(" ", "").startswith(sgg.replace(" ", ""))
                and (esd, es) != (sido, sgg)]
        if subs:
            for es, code in sorted(subs, key=lambda x: x[1]):
                rows.append([sido_raw, sgg_raw, code, f"district:{es}"])
                claimed.add((sido, es))
            n_multi += 1
        else:
            rows.append([sido_raw, sgg_raw, "", "UNMATCHED"])
            n_un += 1

    leftover = sorted(set(code_of) - claimed)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["판매_시도", "판매_시군구", "에너지_시군구코드", "매칭유형"])
        w.writerows(rows)

    print(f"\n매칭 결과: 완전일치 {n_exact} · 일반구묶음 {n_multi} · 미매칭 {n_un}")
    if n_un:
        print("미매칭(판매통계 측):")
        for r in rows:
            if r[3] == "UNMATCHED":
                print(f"  {r[0]} {r[1]}")
    if leftover:
        print(f"에너지DB에만 있는 명칭 {len(leftover)}건:")
        for sd, sg in leftover:
            print(f"  {sd} {sg} ({code_of[(sd, sg)]})")
    # 동명 시군구 확인용 출력
    name_count = defaultdict(list)
    for (sd, sg) in code_of:
        name_count[sg].append(sd)
    dups = {k: v for k, v in name_count.items() if len(v) > 1}
    print(f"\n동명 시군구 {len(dups)}개 (시도 짝으로 자동 구분됨):")
    for k, v in sorted(dups.items()):
        print(f"  {k}: {', '.join(sorted(v))}")
    print(f"\n초안 저장: {OUT}  — 확인 후 사용")
    return 0


if __name__ == "__main__":
    sys.exit(main())
