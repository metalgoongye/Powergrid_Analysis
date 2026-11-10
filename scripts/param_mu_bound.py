# -*- coding: utf-8 -*-
"""[모수 추정] μ 하한 — 팩토리온 스톡·유량의 스톡 항등식 역산 (2024).

μ = 비수도권 기업 증가분(신규등록) 중 '신규 진입(신설)' 비중. 이전(移轉)
구분 통계가 전국 단위로 없으므로(key-evidence §4-1) 구간으로 접근한다.

  스톡 항등식:  S_t = S_{t-1} + N_t − E_t   →   E_t = N_t − ΔS_t
    S = 등록공장 스톡, N = 기간 내 신규등록, E = 퇴출(폐쇄+전출+등록취소)
  수도권(서울·인천·경기) 퇴출 E₁ ≥ 수도권→비수도권 전출 이므로
    비수도권 신규등록 N₂ 중 이전 유입 ≤ min(E₁, N₂)
    ⇒ μ_하한 = 1 − min(E₁, N₂) / N₂           (상한 μ=1 은 설계 확정)
  참고 추정(가정 명시): 수도권의 '초과 퇴출'만 전출로 보는 경우
    T̂ = max(0, E₁ − e₂·S̄₁),  e₂ = E₂ / S̄₂ (비수도권 퇴출률을 기저 폐쇄율로)
    μ_참고 = 1 − T̂ / N₂

자료: 스톡 = 반기 xlsx '지역별 등록현황'(2023.12·2024.06·2024.12),
      유량 = 반기 보고서 PDF '지역별 신규등록 세부내역'(반기 내 누계).
      ※ xlsx '신규등록건' 시트는 기준월 1개월분이라 사용 불가.
PDF 숫자는 구분자 없이 붙어 추출되므로 행 끝의 '합계+비율%'를
|100·합계/전국합 − 비율| < 0.06 제약으로 분리하고, 시도 합 = 전국 합을
검증한다(불일치 시 중단 — 검사를 완화하지 않는다).

출력: out/public/param_mu_bound.csv
"""
from __future__ import annotations

import csv
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
import openpyxl  # noqa: E402
import pypdf  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
D = os.path.join(DATA_ROOT, "rdb", "공장등록통계")
OUT = os.path.join(REPO, "out", "public", "param_mu_bound.csv")

STOCK_FILES = {"2023H2": "(2023.12월말기준)_공장등록통계.xlsx",
               "2024H1": "(2024.06월말기준)_공장등록통계.xlsx",
               "2024H2": "(2024.12월말기준)_공장등록통계.xlsx"}
FLOW_PDFS = {"2024H1": "2024년_상반기_공장등록현황보고서.pdf",
             "2024H2": "2024년_하반기_공장등록현황보고서.pdf"}
CAPITAL = ("서울", "인천", "경기")
SIDO = ["서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
        "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
        "충청북도", "충청남도", "전라북도", "전북특별자치도", "전라남도",
        "경상북도", "경상남도", "제주특별자치도"]


def is_cap(name):
    return name.startswith(CAPITAL)


def load_stock(fn):
    """'지역별 등록현황' → (수도권 스톡, 비수도권 스톡, 합계 검증)."""
    wb = openpyxl.load_workbook(os.path.join(D, fn), data_only=True)
    ws = wb["지역별 등록현황"]
    cap = non = tot = 0.0
    stated = None
    for name, val in ws.iter_rows(min_row=4, max_col=2, values_only=True):
        if name is None or val is None:
            continue
        name = str(name).strip()
        if name == "합계":
            stated = float(val)
            continue
        tot += float(val)
        if is_cap(name):
            cap += float(val)
        else:
            non += float(val)
    assert stated is not None and abs(tot - stated) < 0.5, \
        f"{fn}: 시도 합 {tot} ≠ 합계 {stated}"
    return cap, non, stated


def load_flow(fn):
    """PDF '지역별 신규등록 세부내역' → {시도: 반기 신규등록 합계}."""
    r = pypdf.PdfReader(os.path.join(D, fn))
    text = None
    for p in r.pages:
        t = p.extract_text() or ""
        if "신규등록 세부내역" in t.replace(" ", "").replace("\n", "") or \
                "지역별 신규등록 세부내역" in t:
            text = t
            break
    assert text, f"{fn}: 신규등록 세부내역 쪽을 못 찾음"
    flat = text.replace("\n", "")
    m = re.search(r"신규공장\s*등록\s*:\s*([\d,]+)\s*개사", flat)
    assert m, f"{fn}: 전국 신규등록 합계 표기를 못 찾음"
    total = int(m.group(1).replace(",", ""))

    out = {}
    for name in SIDO:
        i = flat.find(name)
        if i < 0:
            continue
        seg = flat[i + len(name):]
        j = seg.find("%")
        if j < 0:
            continue
        tail = re.sub(r"[^\d.]", "", seg[:j].replace(",", ""))
        # tail 끝 = '<합계><비율 d+.d>' — 제약으로 분리
        mm = re.search(r"(\d+)\.(\d)$", tail)
        if not mm:
            continue
        digits, dec = mm.group(1), mm.group(2)
        found = None
        for k in (1, 2):  # 비율 정수부 자릿수 1~2
            if len(digits) <= k:
                continue
            ratio = float(f"{digits[-k:]}.{dec}")
            pre = digits[:-k]
            # 합계는 pre의 접미 — 길이를 늘려가며 제약 만족 탐색
            for L in range(1, min(len(pre), 5) + 1):
                T = int(pre[-L:])
                if T > 0 and abs(100.0 * T / total - ratio) < 0.06:
                    found = T
            if found:
                break
        assert found, f"{fn}: {name} 행 분리 실패 ({seg[:j][:40]})"
        out[name] = found
    s = sum(out.values())
    assert s == total, f"{fn}: 시도 합 {s} ≠ 전국 {total} — 추출 검증 실패"
    return out, total


def main() -> int:
    stock = {k: load_stock(fn) for k, fn in STOCK_FILES.items()}
    for k, (c, n, t) in stock.items():
        print(f"스톡 {k}: 수도권 {c:,.0f} · 비수도권 {n:,.0f} · 계 {t:,.0f}")

    flows = {}
    for k, fn in FLOW_PDFS.items():
        f, total = load_flow(fn)
        n1 = sum(v for nm, v in f.items() if is_cap(nm))
        flows[k] = (n1, total - n1, total)
        print(f"유량 {k}: 수도권 신규등록 {n1:,} · 비수도권 {total - n1:,} · 계 {total:,}")

    rows = []

    def calc(label, s0, s1, n1, n2):
        dS1, dS2 = s1[0] - s0[0], s1[1] - s0[1]
        e1, e2 = n1 - dS1, n2 - dS2
        mu_lo = 1.0 - min(e1, n2) / n2
        sbar1, sbar2 = (s0[0] + s1[0]) / 2, (s0[1] + s1[1]) / 2
        t_hat = max(0.0, e1 - (e2 / sbar2) * sbar1)
        mu_ref = 1.0 - t_hat / n2
        rows.append([label, n1, f"{dS1:.0f}", f"{e1:.0f}", n2, f"{dS2:.0f}",
                     f"{e2:.0f}", f"{100 * e1 / sbar1:.2f}", f"{100 * e2 / sbar2:.2f}",
                     f"{mu_lo:.3f}", f"{t_hat:.0f}", f"{mu_ref:.3f}"])
        print(f"[{label}] 수도권 N₁={n1:,} ΔS₁={dS1:+,.0f} E₁={e1:,.0f} | "
              f"비수도권 N₂={n2:,} ΔS₂={dS2:+,.0f} E₂={e2:,.0f} | "
              f"퇴출률 {100 * e1 / sbar1:.2f}% vs {100 * e2 / sbar2:.2f}% | "
              f"μ_하한={mu_lo:.3f} · 초과퇴출 T̂={t_hat:,.0f} → μ_참고={mu_ref:.3f}")

    calc("2024H1", stock["2023H2"], stock["2024H1"], flows["2024H1"][0], flows["2024H1"][1])
    calc("2024H2", stock["2024H1"], stock["2024H2"], flows["2024H2"][0], flows["2024H2"][1])
    calc("2024연간", stock["2023H2"], stock["2024H2"],
         flows["2024H1"][0] + flows["2024H2"][0],
         flows["2024H1"][1] + flows["2024H2"][1])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        f.write("# μ = 비수도권 신규등록 중 신설 비중. 하한 = 1 − min(E₁,N₂)/N₂ "
                "(수도권 퇴출 전량을 전출로 보는 최보수 경계), "
                "참고 = 수도권 초과 퇴출만 전출로 보는 가정. 상한 μ=1.\n")
        w = csv.writer(f)
        w.writerow(["기간", "N1_수도권신규", "dS1", "E1_수도권퇴출", "N2_비수도권신규",
                    "dS2", "E2_비수도권퇴출", "퇴출률1_pct", "퇴출률2_pct",
                    "mu_하한", "초과퇴출_T", "mu_참고"])
        w.writerows(rows)
    print("저장:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
