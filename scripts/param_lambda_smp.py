# -*- coding: utf-8 -*-
"""혼잡료 λ 1차 추정 — EPSIS 시간별 SMP(육지 vs 제주, 2024).

재료: EPSIS 전력거래 > 계통한계가격 > 시간별SMP, 구분 육지/제주, 2024-01-01~
12-31 CSV (2026-10-13 직접 다운로드, rdb/SMP_시간별_2024/).

개념: 모형의 λ는 구역 간 한계비용(가격) 격차. 한국에서 실재하는 유일한
지역 분리 가격이 육지-제주 SMP 격차이므로 이를 λ의 1차 실증 대응물로 쓴다.
격차가 0인 시간은 계통 제약이 묶이지 않은 시간(λ=0), 격차가 벌어진 시간이
혼잡료가 발생한 시간이다. 2024-06부터 제주 시범사업 DA/RT 가격 체제 —
상·하반기 분리 통계도 산출한다.

검증(실패 시 멈춤): 육지 366일(2024 윤년)×24시간, 제주 누락 날짜는 보고.
출력: out/public/param_lambda_smp_2024.csv + 콘솔. 단위: 원/kWh.
"""
import csv
import io
import os
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
DIR = os.path.join(DATA_ROOT, "rdb", "SMP_시간별_2024")
OUT = os.path.join(os.path.dirname(__file__), "..", "out", "public",
                   "param_lambda_smp_2024.csv")
EPS = 0.005  # 격차 0 판정 허용(원/kWh)


def read_csv(path):
    raw = open(path, "rb").read()
    txt = raw.decode("utf-8-sig") if raw[:3] == b"\xef\xbb\xbf" else raw.decode("cp949")
    out = {}
    for r in csv.reader(io.StringIO(txt)):
        if r and r[0].startswith("2024"):
            out[r[0]] = [float(x) for x in r[1:25]]
    return out


def stats(pairs):
    """pairs: [(제주, 육지)] 시간 단위 → 격차 통계 dict"""
    d = [j - l for j, l in pairs]
    n = len(d)
    pos = [x for x in d if x > EPS]
    neg = [x for x in d if x < -EPS]
    bind = len(pos) + len(neg)
    return {
        "시간수": n,
        "평균격차": sum(d) / n,
        "절대평균격차": sum(abs(x) for x in d) / n,
        "구속시간비중_pct": 100 * bind / n,
        "구속시평균격차": (sum(abs(x) for x in d if abs(x) > EPS) / bind) if bind else 0.0,
        "제주고가비중_pct": 100 * len(pos) / n,
        "제주저가비중_pct": 100 * len(neg) / n,
    }


def main():
    land = read_csv(os.path.join(DIR, "epsis_smp_hourly_2024_육지.csv"))
    jeju = read_csv(os.path.join(DIR, "epsis_smp_hourly_2024_제주.csv"))

    assert len(land) == 366, f"육지 {len(land)}일 != 366 (2024 윤년)"
    missing = sorted(set(land) - set(jeju))
    print(f"(1) 커버: 육지 366일, 제주 {len(jeju)}일"
          + (f" — 제주 누락: {', '.join(missing)}" if missing else ""))
    assert len(missing) <= 2, "제주 결측 과다 — 다운로드 재확인"

    common = sorted(set(land) & set(jeju))
    allp = [(j, l) for d in common for j, l in zip(jeju[d], land[d])]
    h1 = [(j, l) for d in common if d < "2024/06/01"
          for j, l in zip(jeju[d], land[d])]
    h2 = [(j, l) for d in common if d >= "2024/06/01"
          for j, l in zip(jeju[d], land[d])]

    rows = []
    for name, pairs in (("연간", allp), ("상반기(변동비시장)", h1),
                        ("하반기(시범사업 DA/RT)", h2)):
        s = stats(pairs)
        rows.append({"구간": name, **{k: round(v, 2) for k, v in s.items()}})
        print(f"(2) [{name}] 평균 {s['평균격차']:+.2f} · 절대평균 {s['절대평균격차']:.2f} · "
              f"구속 {s['구속시간비중_pct']:.1f}% · 구속시 평균 {s['구속시평균격차']:.2f}원/kWh")

    print("\n(3) 월별 평균 격차(제주−육지):")
    mon = defaultdict(list)
    for d in common:
        mon[d[5:7]] += [j - l for j, l in zip(jeju[d], land[d])]
    for m in sorted(mon):
        v = sum(mon[m]) / len(mon[m])
        rows.append({"구간": f"{m}월", "시간수": len(mon[m]), "평균격차": round(v, 2),
                     "절대평균격차": "", "구속시간비중_pct": "", "구속시평균격차": "",
                     "제주고가비중_pct": "", "제주저가비중_pct": ""})
        print(f"    {m}월: {v:+7.2f}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n저장: {os.path.relpath(OUT)}")
    print("주의: SMP는 도매가격 — 모형 λ(소매 요금 격차)로 쓸 때는 정산단가·"
          "요금 전가 구조를 거쳐 해석. 제주 시범사업 가격(별표 33 DA/RT)과의"
          " 정합은 하반기 구간으로 점검.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
