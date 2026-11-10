# -*- coding: utf-8 -*-
"""한전 판매전력량 통계의 계약종별·권역별 요약 (보고서 2장 배경 수치).

- 계약종별 판매량·평균 판매단가(= 전기요금 합 ÷ 사용량 합) — 2023·2024
- 수도권(서울·인천·경기) 판매 비중 — 전체 및 계약종별

출력: out/public/s1_sales_by_class.csv
"""
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
D = os.path.join(DATA_ROOT, "rdb", "시군구별_월별_계약종별판매전력량_2023_2024")
OUT = os.path.join(REPO, "out", "public", "s1_sales_by_class.csv")
CAPITAL = ("서울", "인천", "경기")


def main() -> int:
    rows = []
    for year in ("2023", "2024"):
        kwh = defaultdict(float)
        won = defaultdict(float)
        cap = defaultdict(float)
        p = os.path.join(D, f"한전_시군구별_계약종별_월별_판매전력량_{year}.csv")
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                k = r["계약종별"]
                v = float(r["사용량(kWh)"] or 0)
                kwh[k] += v
                won[k] += float(r["전기요금(원)"] or 0)
                if r["시도"].startswith(CAPITAL):
                    cap[k] += v
        # 원자료에 '합계' 종별 행이 따로 있다 — 합산에서 빼고 검산에만 쓴다
        stated = kwh.pop("합계", None)
        won.pop("합계", None)
        cap.pop("합계", None)
        tot, tot_won, tot_cap = sum(kwh.values()), sum(won.values()), sum(cap.values())
        if stated is not None:
            assert abs(stated - tot) / tot < 1e-6, f"{year}: 종별 합 {tot} ≠ 합계 행 {stated}"
        print(f"[{year}] 총 판매 {tot / 1e9:.1f}TWh · 평균단가 {tot_won / tot:.1f}원/kWh · "
              f"수도권 비중 {100 * tot_cap / tot:.1f}%")
        for k in sorted(kwh, key=lambda k: -kwh[k]):
            rows.append([year, k, f"{kwh[k] / 1e9:.2f}", f"{100 * kwh[k] / tot:.1f}",
                         f"{won[k] / kwh[k]:.1f}", f"{100 * cap[k] / kwh[k]:.1f}"])
            print(f"  {k:<6} {kwh[k] / 1e9:7.1f}TWh ({100 * kwh[k] / tot:4.1f}%) · "
                  f"{won[k] / kwh[k]:6.1f}원/kWh · 수도권 {100 * cap[k] / kwh[k]:.1f}%")
        rows.append([year, "합계", f"{tot / 1e9:.2f}", "100.0",
                     f"{tot_won / tot:.1f}", f"{100 * tot_cap / tot:.1f}"])
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 한전 시군구별 계약종별 월별 판매전력량 집계. 평균단가 = 전기요금 합 ÷ 사용량 합\n")
        w = csv.writer(f)
        w.writerow(["연도", "계약종별", "판매량_TWh", "비중_pct", "평균단가_원/kWh", "수도권비중_pct"])
        w.writerows(rows)
    print("저장:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
