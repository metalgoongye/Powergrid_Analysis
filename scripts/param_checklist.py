# -*- coding: utf-8 -*-
"""모수별 구축 체크리스트 — 재료(rdb/공간)·스크립트·산출물의 실존을 검사해 출력.

docs/data_provenance.md의 체크리스트 절은 이 스크립트 출력으로 갱신한다
(손으로 고치지 않는다). /status 에서도 호출된다.

상태: [v] 존재 · [ ] 없음(예정) · [~] 부분(부가 설명)
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")

# (모수, 구축 방법 한 줄, [(구분, 상대경로 또는 설명, 기준루트)])
# 기준루트: 'data' = DATA_ROOT, 'repo' = 저장소. 경로 None 이면 미작성 항목.
M = [
    ("λ 혼잡료",
     "EPSIS 시간별 SMP(육지·제주) 격차 → 구속시간·구속시 평균 (param_lambda_smp.py)",
     [("재료", r"rdb\SMP_시간별_2024\epsis_smp_hourly_2024_육지.csv", "data"),
      ("재료", r"rdb\SMP_시간별_2024\epsis_smp_hourly_2024_제주.csv", "data"),
      ("전거", r"literature\전력시장 제도개선 제주 시범사업에 대한 정산기준.pdf", "data"),
      ("코드", r"scripts\param_lambda_smp.py", "repo"),
      ("산출", r"out\public\param_lambda_smp_2024.csv", "repo")]),
    ("s 귀속률",
     "영업잉여 수취(DT_1C94)/발생(요소소득−피용자보수) 결합 (param_s_estimate.py 외 2)",
     [("재료", r"rdb\GRDP_GRNI_2023_2024\GRDP_GRI_지역외순수취본원소득_시도__20261008144507.xlsx", "data"),
      ("재료", r"rdb\GRDP_GRNI_2023_2024\101_DT_1C94_20261013154150.xls", "data"),
      ("재료", r"rdb\GRDP_GRNI_2023_2024\시도별_경제활동별_지역내총부가가치_및_요소소득_20261013154245.xlsx", "data"),
      ("검증", r"rdb\GRDP_GRNI_2023_2024\2024지역소득통계.pdf", "data"),
      ("코드", r"scripts\param_s_estimate.py", "repo"),
      ("코드", r"scripts\param_s_attribution.py", "repo"),
      ("코드", r"scripts\param_s_operating_surplus.py", "repo"),
      ("산출", r"out\public\param_s_estimate.csv", "repo"),
      ("보정", r"rdb\지역별고용조사\2024_하반기(A형_시군구_대분류)(2021~)_20261007_03570.csv", "data"),
      ("보정코드", None, "통근 순임금 보정 스크립트 — 미작성")]),
    ("σ(Z) 신호보존율",
     "격자 수요지도 × 변전소 권역 배정(규칙①~④) → 원가신호 보존율",
     [("재료", r"powergrid_spatial_data\36pJ2bwdQr\KOR.gpkg", "data"),
      ("재료", r"rdb\변전소별_접속가능여유용량\한전ON_변전소별_전력공급_여유용량_20261008.csv", "data"),
      ("재료", r"out\public\s1_grid_demand_2024.csv.gz", "repo"),
      ("코드", r"scripts\s2_zones_nearest.py", "repo"),
      ("산출", r"out\public\s2_zone_demand_nearest.csv", "repo"),
      ("코드", None, "규칙② 네트워크거리·③ LP·④ 무작위화 — 미작성"),
      ("산출", None, "σ(Z) 수치 — 미산출")]),
    ("μ 신규 진입 비중",
     "팩토리온 반기 신규등록 유량·스톡 → 하한 역산 (상한 μ=1 확정)",
     [("재료", r"rdb\공장등록통계\(2024.06월말기준)_공장등록통계.xlsx", "data"),
      ("재료", r"rdb\공장등록통계\(2024.12월말기준)_공장등록통계.xlsx", "data"),
      ("코드", None, "유량·스톡 시계열 → μ 하한 스크립트 — 미작성"),
      ("산출", None, "param_mu — 미산출")]),
    ("w₁·w₂ 임금",
     "지역별고용조사 월평균급여 × 거주/직장 구역 (가중값 적용)",
     [("재료", r"rdb\지역별고용조사\2024_하반기(A형_시군구_대분류)(2021~)_20261007_03570.csv", "data"),
      ("코드", None, "임금 채널 캘리브레이션 스크립트 — 미작성"),
      ("산출", None, "param_w — 미산출")]),
    ("ε·e 전력수요",
     "건물에너지DB 필지 검침 → PNU 조인 → 규율 레이킹 → 100m 격자 (s1_*)",
     [("재료", r"rdb\건물에너지_지번별월별_24_25", "data"),
      ("재료", r"rdb\시군구별_월별_계약종별판매전력량_2023_2024\한전_시군구별_계약종별_월별_판매전력량_2024.csv", "data"),
      ("재료", r"powergrid_spatial_data\연속지적도형정보", "data"),
      ("코드", r"scripts\s1_grid_demand.py", "repo"),
      ("산출", r"out\public\s1_grid_demand_2024.csv.gz", "repo"),
      ("코드", None, "산업용 배분 트랙·용도별 2D 레이킹 — 미작성")]),
]


def main():
    n_ok = n_todo = n_miss = 0
    for name, how, items in M:
        print(f"\n## {name}")
        print(f"   구축: {how}")
        for kind, path, base in items:
            if path is None:
                print(f"   [ ] ({kind}) {base}")
                n_todo += 1
                continue
            full = os.path.join(DATA_ROOT if base == "data" else ROOT, path)
            ok = os.path.exists(full)
            mark = "v" if ok else "X"
            loc = "DATA_ROOT" if base == "data" else "repo"
            print(f"   [{mark}] ({kind}) {loc}\\{path}")
            n_ok += ok
            n_miss += (not ok)
    print(f"\n요약: 존재 {n_ok} · 예정 {n_todo} · 누락 {n_miss}"
          + ("  ← 누락은 즉시 확인 필요!" if n_miss else ""))
    return 1 if n_miss else 0


if __name__ == "__main__":
    sys.exit(main())
