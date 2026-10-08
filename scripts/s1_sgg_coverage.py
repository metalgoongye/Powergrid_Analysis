# -*- coding: utf-8 -*-
"""1단계-3b: 시군구(자치단체) 단위 정합 검증 — 기준년 2024.

에너지 공개본의 연간 사용량을 시군구코드로 집계하고, 확정 사전
(references/sgg_name_code_map.csv)으로 자치단체 단위(판매통계 해상도)에 묶어
판매통계 '주택용+일반용' 연간 합과 대조한다. R²(수준·로그), 커버리지 분포,
이탈 시군구를 보고한다.

사용법:  python scripts/s1_sgg_coverage.py [연도=2024]
산출물:  out/public/s1_sgg_coverage_<년>.csv
"""
from __future__ import annotations

import csv
import io
import math
import os
import sys
import zipfile
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
ENERGY_DIR = os.path.join(DATA_ROOT, "rdb", "건물에너지_지번별월별_24_25")
SALES_DIR = os.path.join(DATA_ROOT, "rdb", "시군구별_월별_계약종별판매전력량_2023_2024")
MAP = os.path.join(REPO, "references", "sgg_name_code_map.csv")
OUT = os.path.join(REPO, "out", "public")


def member_name(info: zipfile.ZipInfo) -> str:
    try:
        return info.filename.encode("cp437").decode("cp949")
    except Exception:
        return info.filename


def energy_by_code(year: str) -> dict[str, float]:
    agg: dict[str, float] = defaultdict(float)
    for mm in range(1, 13):
        zf = zipfile.ZipFile(os.path.join(
            ENERGY_DIR, f"MART_KEY_ELCTY_ENERGY_DATA_{year}{mm:02d}.zip"))
        txt = [i for i in zf.infolist() if member_name(i).lower().endswith(".txt")][0]
        with zf.open(txt) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):
                p = line.split("|")
                if len(p) < 17:
                    continue
                try:
                    agg[p[2]] += float(p[16])
                except ValueError:
                    pass
        print(f"  에너지 {year}-{mm:02d} 집계 완료")
    return agg


# 한전 통계가 같은 해 안에서 신·구 시도명을 혼용(전북 개명 2024-01)하므로
# 단위 키를 정규화해 병합한다. s1_build_sgg_map.py의 별칭과 동일해야 한다.
SIDO_ALIAS = {"전라북도": "전북특별자치도", "강원도": "강원특별자치도"}
SGG_ALIAS = {("세종특별자치시", "세종시"): ("세종특별자치시", "세종특별자치시")}


def norm_unit(sido: str, sgg: str) -> tuple[str, str]:
    sido = SIDO_ALIAS.get(sido, sido)
    return SGG_ALIAS.get((sido, sgg), (sido, sgg))


def main() -> int:
    year = sys.argv[1] if len(sys.argv) > 1 else "2024"

    # 사전: 정규화 단위 → {코드…} (신·구 표기 행이 같은 단위로 합쳐짐)
    unit_codes: dict[tuple[str, str], set[str]] = defaultdict(set)
    with open(MAP, encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        for row in r:
            if row["에너지_시군구코드"]:
                unit_codes[norm_unit(row["판매_시도"], row["판매_시군구"])].add(
                    row["에너지_시군구코드"])
    print(f"사전: 자치단체 단위 {len(unit_codes)}개 (정규화 병합 후)")

    # 판매통계: 분모 2종 — den1 = 주택+일반, den2 = 주택+일반+교육+심야
    DEN1 = ("주택용", "일반용")
    DEN2 = ("주택용", "일반용", "교육용", "심야")
    sales1: dict[tuple[str, str], float] = defaultdict(float)
    sales2: dict[tuple[str, str], float] = defaultdict(float)
    with open(os.path.join(SALES_DIR, f"한전_시군구별_계약종별_월별_판매전력량_{year}.csv"),
              encoding="utf-8-sig") as f:
        for r2 in csv.reader(f):
            if len(r2) > 6 and r2[0] == year and r2[4] in DEN2:
                try:
                    v = float(r2[6])
                except ValueError:
                    continue
                u = norm_unit(r2[2].strip(), r2[3].strip())
                sales2[u] += v
                if r2[4] in DEN1:
                    sales1[u] += v
    sales = sales1

    print("에너지 공개본 연간 집계 중…")
    ecode = energy_by_code(year)

    rows = []
    xs, ys = [], []
    for unit, codes in sorted(unit_codes.items()):
        e = sum(ecode.get(c, 0.0) for c in codes)
        s = sales.get(unit, 0.0)
        s2 = sales2.get(unit, 0.0)
        if s <= 0:
            continue
        cov = 100 * e / s
        cov2 = 100 * e / s2 if s2 > 0 else float("nan")
        rows.append([unit[0], unit[1], ";".join(sorted(codes)), round(e), round(s),
                     round(cov, 2), round(cov2, 2)])
        xs.append(s)
        ys.append(e)

    # R² (수준, 로그)
    def r2(x, y):
        n = len(x)
        mx, my = sum(x) / n, sum(y) / n
        sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
        sxx = sum((a - mx) ** 2 for a in x)
        syy = sum((b - my) ** 2 for b in y)
        return (sxy * sxy) / (sxx * syy) if sxx * syy > 0 else float("nan")

    r2_level = r2(xs, ys)
    r2_log = r2([math.log(v) for v in xs], [math.log(max(v, 1.0)) for v in ys])
    def dist(idx):
        cs = sorted(r[idx] for r in rows if not math.isnan(r[idx]))
        k = len(cs)
        return cs[k // 2], cs[k // 10], cs[9 * k // 10]

    med, p10, p90 = dist(5)
    med2, p10_2, p90_2 = dist(6)

    rows.sort(key=lambda r: r[5])
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, f"s1_sgg_coverage_{year}.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시도", "자치단체", "에너지코드", "공개본_kWh", "판매_주택일반_kWh",
                    "커버리지_주택일반(%)", "커버리지_교육심야포함(%)"])
        w.writerows(rows)

    n = len(rows)
    print(f"\n자치단체 {n}개 비교 (기준년 {year})")
    print(f"R² (수준)  : {r2_level:.4f}")
    print(f"R² (로그)  : {r2_log:.4f}")
    print(f"커버리지(주택+일반)     : 중위 {med:.1f}% · P10 {p10:.1f}% · P90 {p90:.1f}%")
    print(f"커버리지(+교육·심야 포함): 중위 {med2:.1f}% · P10 {p10_2:.1f}% · P90 {p90_2:.1f}%")
    print("\n커버리지 하위 10:")
    for r3 in rows[:10]:
        print(f"  {r3[0]} {r3[1]:<8} {r3[5]:6.1f}%")
    print("커버리지 상위 5:")
    for r3 in rows[-5:]:
        print(f"  {r3[0]} {r3[1]:<8} {r3[5]:6.1f}%")
    print(f"\n저장: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
