# -*- coding: utf-8 -*-
"""귀속률 s 추정 — KOSIS 지역소득(INH_1C91) 시도별 지역외순수취본원소득.

모형(modeling_note v2.2)의 s = 구역2(비수도권)에서 발생한 이윤 중 구역2
주민에게 귀속되는 몫. 이 스크립트는 그 1차 추정으로, 본원소득 전체 기준의
귀속률(= s의 상한)과 구역별 순유출 총액을 계산한다.

계산 단계
  (1) 정합 검증: 공표 지역외순수취본원소득 == GRI - GRDP (시도×연도 전수)
  (2) 구역 합산: 구역1 = 수도권(서울·인천·경기), 구역2 = 나머지 14개 시도
  (3) 본안: 국외 무보정 — 구역2 순유출 L2 = -Σ지역외순수취, s_상한 = GRI2/GRDP2
  (4) 감도: 국외순수취(전국 행)를 GRDP 비례로 제거 → 순수 구역 간 흐름
  (5) s 완성식(영업잉여 입수 후): s = 1 - L2 / 영업잉여2
      본원소득 기준 s_상한이 상한인 이유 - 유출 L2가 전부 이윤이라 해도
      분모(영업잉여) < GRDP 이므로 1 - L2/OS2 < 1 - L2/GRDP2.

출력: out/public/param_s_attribution.csv + 콘솔 전 과정.
"""
import csv
import os
import sys

from openpyxl import load_workbook

sys.stdout.reconfigure(encoding="utf-8")

DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
XLSX = os.path.join(DATA_ROOT, "rdb", "GRDP_GRNI_2023_2024",
                    "GRDP_GRI_지역외순수취본원소득_시도__20261008144507.xlsx")
OUT = os.path.join(os.path.dirname(__file__), "..", "out", "public",
                   "param_s_attribution.csv")

ZONE1 = {"서울특별시", "인천광역시", "경기도"}  # 구역1 = 수도권
# 감도 변형: 구역2에서 비수도권 광역시·세종 제외(도 지역만) — 구역 정의 민감도
METRO2 = {"부산광역시", "대구광역시", "광주광역시", "대전광역시",
          "울산광역시", "세종특별자치시"}
YEARS = ("2023", "2024p")
TOL = 1.0  # 백만원 — 공표치 vs 재계산 허용오차


def load():
    wb = load_workbook(XLSX, read_only=True, data_only=True)
    rows = list(wb["데이터"].iter_rows(values_only=True))
    hdr = [str(c) for c in rows[1]]
    assert hdr[2:] == ["명목GRDP", "실질GRDP", "명목GRDP", "실질GRDP",
                       "명목GRI", "명목GRI",
                       "명목 지역외순수취본원소득", "명목 지역외순수취본원소득"], hdr
    data = {}  # 시도 -> {연도: (grdp, gri, nr)}
    for r in rows[2:]:
        if not r or not r[0]:
            continue
        sido = str(r[0]).strip()
        data[sido] = {
            "2023":  (float(r[2]), float(r[6]), float(r[8])),
            "2024p": (float(r[4]), float(r[7]), float(r[9])),
        }
    return data


def main():
    data = load()
    sidos = [s for s in data if s != "전국"]
    assert len(sidos) == 17, f"시도 수 {len(sidos)} != 17"

    # (1) 정합 검증 — 실패 시 멈춤
    print("=" * 72)
    print("(1) 정합 검증: 공표 지역외순수취본원소득 == GRI - GRDP")
    bad = 0
    for sido, by in data.items():
        for y, (grdp, gri, nr) in by.items():
            if abs((gri - grdp) - nr) > TOL:
                print(f"  불일치: {sido} {y}: GRI-GRDP={gri-grdp:,.0f} vs 공표={nr:,.0f}")
                bad += 1
    if bad:
        print(f"검증 실패 {bad}건 — 중단")
        return 1
    n_checked = len(data) * len(YEARS)
    print(f"  전수 일치 ({n_checked}건, 허용오차 {TOL}백만원) — 통과")

    # 구역 합이 전국과 맞는지 (전국 행 = 국외순수취본원소득)
    results = []
    for y in YEARS:
        print("\n" + "=" * 72)
        print(f"[{y}] (단위: 조원 = 1e6 백만원)")
        g1 = sum(data[s][y][0] for s in sidos if s in ZONE1)
        g2 = sum(data[s][y][0] for s in sidos if s not in ZONE1)
        nr1 = sum(data[s][y][2] for s in sidos if s in ZONE1)
        nr2 = sum(data[s][y][2] for s in sidos if s not in ZONE1)
        nat = data["전국"][y]
        assert abs((nr1 + nr2) - nat[2]) < TOL * 20, "구역 합 != 전국(국외순수취)"
        assert abs((g1 + g2) - nat[0]) < TOL * 20, "GRDP 구역 합 != 전국"

        print(f"(2) 구역 합산: 구역1(수도권 3시도) GRDP {g1/1e6:.1f}조 · "
              f"구역2(비수도권 14시도) GRDP {g2/1e6:.1f}조")
        print(f"    지역외순수취: 구역1 {nr1/1e6:+.1f}조, 구역2 {nr2/1e6:+.1f}조, "
              f"합 {(nr1+nr2)/1e6:+.1f}조 = 전국(국외순수취) {nat[2]/1e6:+.1f}조 [검산 OK]")

        # (3) 본안 — 국외 무보정
        leak2 = -nr2
        s_ub = 1 + nr2 / g2          # = GRI2/GRDP2
        print(f"(3) 본안(국외 무보정): 구역2 순유출 L2 = {leak2/1e6:.1f}조")
        print(f"    본원소득 기준 귀속률(= s 상한) = GRI2/GRDP2 "
              f"= 1 + ({nr2/1e6:+.1f})/{g2/1e6:.1f} = {s_ub:.4f}")

        # (4) 감도 — 국외순수취를 GRDP 비례로 제거
        gnat = nat[0]
        nr2_adj = nr2 - nat[2] * (g2 / gnat)
        nr1_adj = nr1 - nat[2] * (g1 / gnat)
        assert abs(nr1_adj + nr2_adj) < TOL * 20, "보정 후 구역 합 != 0"
        leak2_adj = -nr2_adj
        s_ub_adj = 1 + nr2_adj / g2
        print(f"(4) 감도(국외 비례배분 제거): 구역2 순유출' = {leak2_adj/1e6:.1f}조 "
              f"(구역1 순수취' {nr1_adj/1e6:+.1f}조와 상쇄 [검산 OK])")
        print(f"    귀속률 상한' = {s_ub_adj:.4f}")

        # (4b) 구역 정의 감도 — 구역2를 도 지역만으로 (비수도권 광역시·세종 제외)
        do_only = [s for s in sidos if s not in ZONE1 and s not in METRO2]
        g2d = sum(data[s][y][0] for s in do_only)
        nr2d = sum(data[s][y][2] for s in do_only)
        s_ub_do = 1 + nr2d / g2d
        print(f"(4b) 구역 정의 감도(도 지역 {len(do_only)}개만): GRDP {g2d/1e6:.1f}조, "
              f"순유출 {-nr2d/1e6:.1f}조 → 귀속률 상한 = {s_ub_do:.4f}")
        nr_metro = sum(data[s][y][2] for s in METRO2)
        print(f"     (제외한 비수도권 광역시·세종의 순수취 합 {nr_metro/1e6:+.1f}조 — "
              f"구역2 내부 흡수분)")

        results.append({
            "연도": y, "GRDP_구역1_조원": round(g1/1e6, 1),
            "GRDP_구역2_조원": round(g2/1e6, 1),
            "순유출_구역2_본안_조원": round(leak2/1e6, 1),
            "순유출_구역2_국외보정_조원": round(leak2_adj/1e6, 1),
            "s상한_본안": round(s_ub, 4),
            "s상한_국외보정": round(s_ub_adj, 4),
            "s상한_도지역만": round(s_ub_do, 4),
        })

    print("\n" + "=" * 72)
    print("(5) s 완성식 — 분배계정(영업잉여) 입수 후:")
    print("    s = 1 - L2 / 영업잉여2   (유출 전량 이윤 가정 시 하한,")
    print("    임금·재산소득 유출 분리 시 그만큼 상향). 위 s상한과 함께 구간 보고.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print(f"\n저장: {os.path.relpath(OUT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
