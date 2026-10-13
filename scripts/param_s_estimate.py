# -*- coding: utf-8 -*-
"""귀속률 s 추정(법인 단계) — 발생 기준 영업잉여 × 수취 기준 영업잉여 결합.

재료 (KOSIS 지역소득, 2020년 기준 계열; 2026-10-13 직접 다운로드)
  ① DT_1C94 시도별 제도부문별 소득계정 (XML xls, 2020~2024)
     - 수취(제도부문 거주) 기준: 피용자보수·영업잉여·총본원소득잔액 (원천)
  ② DT_1C92 시도별 경제활동별 지역내총부가가치 및 요소소득 (xlsx, 2020~2023)
     - 발생(사업장 소재) 기준: 지역내요소소득(명목) = 피용자보수발생 + 영업잉여발생(순)
  ③ GRDP_GRI_지역외순수취본원소득 xlsx — GRI 교차검증용

식
  영업잉여발생2 = 요소소득2 − 피용자보수발생2,
  피용자보수발생2 ≈ 피용자보수수취2   [가정 A: 구역 경계 통과 통근의 순임금흐름 ≈ 0
                                     — 지역별고용조사(직장-거주 교차)로 추후 보정]
  s(법인 단계) = 영업잉여수취2 / 영업잉여발생2
  법인만 변형: 가계 영업잉여(귀속임대)는 발생=수취(현지 고정)로 보고 양변에서 차감.

검증(실패 시 멈춤): 시도 완비, GRI(1C94 2024)==GRI(xlsx 2024),
  전국 정합 |요소소득 − (피용자보수+영업잉여)| < 1% (국외 통근보수 등 잔차 보고).

출력: out/public/param_s_estimate.csv + 콘솔 전 과정. 단위: 조원.
"""
import csv
import os
import re
import sys
import xml.etree.ElementTree as ET

from openpyxl import load_workbook

sys.stdout.reconfigure(encoding="utf-8")

DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
DIR = os.path.join(DATA_ROOT, "rdb", "GRDP_GRNI_2023_2024")
F_1C94 = os.path.join(DIR, "101_DT_1C94_20261013154150.xls")
F_1C92 = os.path.join(DIR, "시도별_경제활동별_지역내총부가가치_및_요소소득_20261013154245.xlsx")
F_GRI = os.path.join(DIR, "GRDP_GRI_지역외순수취본원소득_시도__20261008144507.xlsx")
OUT = os.path.join(os.path.dirname(__file__), "..", "out", "public",
                   "param_s_estimate.csv")

ZONE1 = {"서울특별시", "인천광역시", "경기도"}
METRO2 = {"부산광역시", "대구광역시", "광주광역시", "대전광역시",
          "울산광역시", "세종특별자치시"}
YEARS94 = ["2020", "2021", "2022", "2023", "2024"]
YEARS92 = ["2020", "2021", "2022", "2023"]
NS = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}


def load_1c94():
    """(시도, 항목, 부문, 사용/원천) -> {연도: 백만원}"""
    raw = open(F_1C94, "rb").read().lstrip(b"\t\r\n ")
    txt = raw.decode("cp949")
    txt = re.sub(r"encoding=[\"'][^\"']+[\"']", 'encoding="utf-8"', txt, count=1)
    txt = re.sub(r"<(?=\s)", "&lt;", txt)           # 메타 시트의 '< ... >' 텍스트
    txt = re.sub(r"(?<=\s)>", "&gt;", txt)
    txt = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", txt)
    root = ET.fromstring(txt)
    ws = root.find(".//ss:Worksheet[@ss:Name='데이터']", NS)
    data = {}
    header = None
    for row in ws.findall(".//ss:Row", NS):
        cells = []
        for c in row.findall("ss:Cell", NS):
            el = c.find("ss:Data", NS)
            cells.append(el.text if el is not None else "")
        if not cells:
            continue
        if cells[0] == "시도별":
            header = cells
            continue
        if header is None or len(cells) < 6:
            continue
        sido, item, sector, flow = (str(x or "").strip() for x in cells[:4])
        item = re.sub(r"^\d\)\s*", "", item)        # 각주 번호 제거
        vals = {}
        for i, h in enumerate(header):
            m = re.match(r"(20\d\d)", str(h or ""))
            if m and i < len(cells):
                try:
                    vals[m.group(1)] = float(cells[i])
                except (TypeError, ValueError):
                    pass
        data[(sido, item, sector, flow)] = vals
    return data


def load_1c92():
    """시도 -> {연도: 요소소득(명목, 백만원)} (경제활동 합계)"""
    wb = load_workbook(F_1C92, data_only=True)
    rows = list(wb["데이터"].iter_rows(values_only=True))
    yr_row, item_row = rows[0], rows[1]
    cols = {}
    for i in range(2, len(item_row)):
        if str(item_row[i]).strip() == "지역내요소소득(명목)":
            cols[str(yr_row[i]).strip()] = i
    assert set(YEARS92) <= set(cols), f"요소소득 연도 열 부족: {sorted(cols)}"
    out = {}
    for r in rows[2:]:
        if r and r[0] and str(r[1]).strip() == "합계":
            out[str(r[0]).strip()] = {y: float(r[cols[y]]) for y in YEARS92}
    return out


def main():
    d94 = load_1c94()
    f92 = load_1c92()

    sidos = sorted({k[0] for k in d94} - {"전국"})
    assert len(sidos) == 17, f"1C94 시도 수 {len(sidos)}"
    assert len(f92) == 18, f"1C92 시도 수 {len(f92)}"

    def g94(sido, item, sector="합계"):
        return d94[(sido, item, sector, "원천")]

    # (1) GRI 교차검증 (2024)
    wb = load_workbook(F_GRI, read_only=True, data_only=True)
    xl = {str(r[0]).strip(): float(r[7])
          for r in list(wb["데이터"].iter_rows(values_only=True))[2:] if r and r[0]}
    # INH_1C91 쪽 값이 백만원 단위 반올림이라 수백만 원 차이 허용
    bad = sum(1 for s in sidos + ["전국"]
              if abs(g94(s, "총본원소득잔액")["2024"] - xl[s]) > 100.0)
    print(f"(1) GRI 교차검증(1C94 vs 기존 xlsx, 2024, 허용 1억원): 불일치 {bad}건"
          + (" — 중단" if bad else " — 통과"))
    if bad:
        return 1

    # (2) 전국 정합: 요소소득 vs 피용자보수+영업잉여 (국외 통근보수 등 잔차)
    print("(2) 전국 정합(발생 합 vs 수취 합):")
    for y in YEARS92:
        f = f92["전국"][y]
        cs = g94("전국", "피용자보수")[y] + g94("전국", "영업잉여")[y]
        diff = 100 * (f - cs) / f
        print(f"    {y}: 요소소득 {f/1e6:.1f}조 vs 피보+영잉 {cs/1e6:.1f}조 "
              f"(잔차 {diff:+.2f}%)")
        assert abs(diff) < 1.0, "전국 정합 1% 초과 — 개념 불일치 의심, 중단"

    # (3) 구역별 s 계산
    zones = {
        "본안(비수도권14)": [s for s in sidos if s not in ZONE1],
        "도지역만(8)": [s for s in sidos if s not in ZONE1 and s not in METRO2],
    }
    results = []
    for zname, zz in zones.items():
        print(f"\n(3) [{zname}] (조원)")
        for y in YEARS92:
            F2 = sum(f92[s][y] for s in zz) / 1e6
            comp2 = sum(g94(s, "피용자보수")[y] for s in zz) / 1e6
            os_rec2 = sum(g94(s, "영업잉여")[y] for s in zz) / 1e6
            os_hh2 = sum(g94(s, "영업잉여", "가계 및 비영리단체")[y] for s in zz) / 1e6
            os_gen2 = F2 - comp2                      # 발생 영업잉여(순), 가정 A
            s_all = os_rec2 / os_gen2
            s_corp = (os_rec2 - os_hh2) / (os_gen2 - os_hh2)
            print(f"    {y}: 요소소득 {F2:.1f} − 피보수취 {comp2:.1f} → "
                  f"영잉발생 {os_gen2:.1f} | 영잉수취 {os_rec2:.1f} "
                  f"⇒ s={s_all:.3f} (법인만 {s_corp:.3f})")
            results.append({"구역정의": zname, "연도": y,
                            "요소소득2_조원": round(F2, 1),
                            "피용자보수수취2_조원": round(comp2, 1),
                            "영업잉여발생2_조원": round(os_gen2, 1),
                            "영업잉여수취2_조원": round(os_rec2, 1),
                            "가계영업잉여2_조원": round(os_hh2, 1),
                            "s_전체": round(s_all, 4),
                            "s_법인만": round(s_corp, 4)})

    print("\n가정 A: 피용자보수 발생≈수취(구역 경계 통근 순임금흐름 0) — "
          "지역별고용조사로 보정 예정.")
    print("해석: s는 법인 등록지 단계의 귀속(배당의 2차 재분배 이전). "
          "모형 s의 측정 대응물로 사용, 재산소득 순수취로 감도 보완.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print(f"저장: {os.path.relpath(OUT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
