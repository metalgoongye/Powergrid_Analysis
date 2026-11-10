# -*- coding: utf-8 -*-
"""[모수 추정] w₁·w₂(구역별 임금) + 인구비 n₂/n + 정리 4 임계 s* 판정.

자료: 통계청 지역별고용조사 2024 하반기 공개용 A형(시군구·대분류), MDIS.
      전 통계는 시군구가중값 적용. 구역 1 = 수도권(서울·인천·경기),
      구역 2 = 비수도권. 시도 판별은 파일설계서 코드집의 코드→명칭
      (예 '1101 서울종로구')을 직접 읽어 구성 — 임의 매핑 없음.

산출:
  ① w — 임금근로자(상용·임시·일용) 최근 3개월 월평균급여(만원)의 가중평균.
     직장 소재 구역 기준(모형의 '구역 i 기업이 주는 임금')이 본안,
     거주 구역 기준·상용근로자 한정·가중 중위수를 병기.
  ② n₂/n — 15세 이상 인구·가구(가구주)·취업자(거주)·취업자(직장) 4기준
     (qa_econ_concepts Q11: 가구 기준과 취업자 기준 병기).
  ③ 거주×직장 2×2 취업자·임금총액 행렬 — s 통근 보정(다음 모듈)의 재료.
  ④ 정리 4 임계 s* = μ·n₂/n — μ ∈ {하한, 참고, 1}(param_mu_bound.csv)
     × n₂/n 4기준, 실측 s(param_s_estimate.csv 본안 최신년)와 대조.

출력: out/public/param_w_wage.csv, out/public/param_sstar_check.csv
"""
from __future__ import annotations

import csv
import glob
import os
import sys
import warnings
from collections import defaultdict

warnings.filterwarnings("ignore")
import openpyxl  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
D = os.path.join(DATA_ROOT, "rdb", "지역별고용조사")
PUB = os.path.join(REPO, "out", "public")
CAPITAL = ("서울", "인천", "경기")


def zone_dict():
    """코드집 '행정구역코드' 목록 → 시군구 4자리코드 → 구역(1/2/None)."""
    p = glob.glob(os.path.join(D, "*descriptionFiles", "파일설계서*.xlsx"))[0]
    ws = openpyxl.load_workbook(p, data_only=True)["코드정보"]
    cur, out = None, {}
    for r in ws.iter_rows(min_row=3, values_only=True):
        if r[1] is not None:
            cur = str(r[1])
        if cur != "행정구역코드" or r[2] is None:
            continue
        code, name = str(r[2]).strip(), str(r[3]).strip()
        if name in ("외국", "선박"):
            out[code] = None
        else:
            out[code] = 1 if name.startswith(CAPITAL) else 2
    n1 = sum(1 for v in out.values() if v == 1)
    n2 = sum(1 for v in out.values() if v == 2)
    assert n1 == 66 and n1 + n2 == 229, f"구역 사전 이상: 수도권 {n1}, 계 {n1 + n2}"
    return out


def wmedian(pairs):
    pairs = sorted(pairs)
    tot = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= tot / 2:
            return v
    return float("nan")


def main() -> int:
    zd = zone_dict()
    src = glob.glob(os.path.join(D, "2024_하반기*.csv"))[0]

    pop = defaultdict(float)      # 15세+ 인구(거주 구역)
    hh = defaultdict(float)       # 가구주(거주)
    emp_res = defaultdict(float)  # 취업자(거주)
    emp_wrk = defaultdict(float)  # 취업자(직장)
    cross_n = defaultdict(float)  # (거주, 직장) 취업자
    cross_w = defaultdict(float)  # (거주, 직장) 임금총액(만원×가중)
    wage = {k: defaultdict(lambda: [0.0, 0.0]) for k in
            ("직장_전체", "거주_전체", "직장_상용")}
    med = {1: [], 2: []}
    n_rows = n_wage = n_unk = 0

    with open(src, encoding="cp949", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            n_rows += 1
            w = float(row["시군구가중값"])
            zr = zd.get(row["행정구역코드"])
            if zr is None:
                n_unk += 1
                continue
            pop[zr] += w
            if row["가구주관계코드"] == "1":
                hh[zr] += w
            if row["경제활동구분코드"] != "1":
                continue
            emp_res[zr] += w
            zw = zd.get(row["사업체직장소재지_행정구역코드"])
            if zw is None:
                continue  # 외국·선박·미상 직장
            emp_wrk[zw] += w
            cross_n[(zr, zw)] += w
            st, pay = row["현직장종사상지위코드"], row["최근3개월_월평균급여"]
            if st in ("1", "2", "3") and pay.strip():
                p = float(pay)
                if p <= 0:
                    continue
                n_wage += 1
                cross_w[(zr, zw)] += p * w
                for key, z in (("직장_전체", zw), ("거주_전체", zr)):
                    a = wage[key][z]
                    a[0] += w
                    a[1] += p * w
                if st == "1":
                    a = wage["직장_상용"][zw]
                    a[0] += w
                    a[1] += p * w
                med[zw].append((p, w))

    print(f"행 {n_rows:,} · 임금 응답 {n_wage:,} · 거주 구역 미상 {n_unk}")

    def share2(d):
        return d[2] / (d[1] + d[2])

    bases = [("15세이상인구", pop), ("가구(가구주)", hh),
             ("취업자_거주", emp_res), ("취업자_직장", emp_wrk)]
    rows = []
    for name, d in bases:
        rows.append(["n", name, f"{d[1]:.0f}", f"{d[2]:.0f}", f"{share2(d):.4f}"])
        print(f"n₂/n [{name}]: 구역1 {d[1]:,.0f} · 구역2 {d[2]:,.0f} → {share2(d):.4f}")
    wm = {}
    for key in ("직장_전체", "거주_전체", "직장_상용"):
        w1 = wage[key][1][1] / wage[key][1][0]
        w2 = wage[key][2][1] / wage[key][2][0]
        wm[key] = (w1, w2)
        rows.append(["w_평균(만원)", key, f"{w1:.1f}", f"{w2:.1f}", f"{w2 / w1:.4f}"])
        print(f"w [{key}]: w₁ {w1:.1f} · w₂ {w2:.1f} 만원 (w₂/w₁ {w2 / w1:.3f})")
    m1, m2 = wmedian(med[1]), wmedian(med[2])
    rows.append(["w_중위(만원)", "직장_전체", f"{m1:.0f}", f"{m2:.0f}", f"{m2 / m1:.4f}"])
    print(f"w 중위 [직장_전체]: {m1:.0f} · {m2:.0f} 만원")
    for (zr, zw) in ((1, 1), (1, 2), (2, 1), (2, 2)):
        rows.append(["통근교차", f"거주{zr}→직장{zw}",
                     f"{cross_n[(zr, zw)]:.0f}",
                     f"{cross_w[(zr, zw)] / 1e4:.1f}", ""])
    out_res2 = cross_w[(2, 1)]   # 구역2 거주자가 구역1 직장에서 번 임금
    in_res1 = cross_w[(1, 2)]    # 구역1 거주자가 구역2 직장에서 번 임금
    wb2 = cross_w[(1, 2)] + cross_w[(2, 2)]  # 구역2 소재 일자리 임금총액
    print(f"통근 임금 흐름: 구역2 일자리 임금의 {100 * in_res1 / wb2:.2f}%가 "
          f"구역1 거주자에게 유출, 구역2 거주자의 구역1 임금 유입은 "
          f"구역2 일자리 임금총액 대비 {100 * out_res2 / wb2:.2f}%")

    # s 통근 보정 — 가정 A(피용자보수 발생 = 수취)의 완화.
    #   구역2 피용자보수 '발생'(구역2 소재 일자리) / '수취'(구역2 거주자) 비율을
    #   고용조사 임금총액으로 추정해 s 분모(영업잉여 발생 = 요소소득 − 피용자보수
    #   발생)를 다시 계산. 임금 구조(2024H2)를 계정 최신년에 적용(시점 차 주석).
    recv2 = cross_w[(2, 1)] + cross_w[(2, 2)]
    adj = wb2 / recv2
    with open(os.path.join(PUB, "param_s_estimate.csv"), encoding="utf-8-sig") as f:
        base = [r for r in csv.DictReader(f) if r["구역정의"].startswith("본안")][-1]
    fy, comp, os_recv = (float(base["요소소득2_조원"]),
                         float(base["피용자보수수취2_조원"]),
                         float(base["영업잉여수취2_조원"]))
    s_raw = os_recv / (fy - comp)
    s_adj = os_recv / (fy - comp * adj)
    rows.append(["s_통근보정", f"본안 {base['연도']}",
                 f"{s_raw:.4f}", f"{s_adj:.4f}", f"{adj:.5f}"])
    print(f"s 통근 보정(본안 {base['연도']}): 피용자보수 발생/수취 = {adj:.5f} → "
          f"s {s_raw:.4f} → {s_adj:.4f} ({s_adj - s_raw:+.4f})")

    os.makedirs(PUB, exist_ok=True)
    p1 = os.path.join(PUB, "param_w_wage.csv")
    with open(p1, "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 지역별고용조사 2024H2 A형, 시군구가중값 적용. 구역1=수도권, "
                "구역2=비수도권. n행: 값1·값2=가중 인원, 비율=n₂/n. "
                "w행: 값=월평균급여(만원), 비율=w₂/w₁. "
                "통근교차행: 값1=취업자, 값2=임금총액(억 원/월). "
                "s_통근보정행: 값1=보정 전 s, 값2=보정 후 s, 비율=피용자보수 발생/수취\n")
        w = csv.writer(f)
        w.writerow(["항목", "기준", "값1", "값2", "비율"])
        w.writerows(rows)

    # 정리 4 임계 s* = μ·n₂/n
    with open(os.path.join(PUB, "param_mu_bound.csv"), encoding="utf-8-sig") as f:
        mu_row = [r for r in csv.DictReader(ln for ln in f if not ln.startswith("#"))
                  if r["기간"].endswith("연간")][0]
    mus = [("μ_하한", float(mu_row["mu_하한"])),
           ("μ_참고", float(mu_row["mu_참고"])), ("μ_상한", 1.0)]
    with open(os.path.join(PUB, "param_s_estimate.csv"), encoding="utf-8-sig") as f:
        srows = list(csv.DictReader(f))
    s_defs = []  # (정의, 최신, 최소, 최대) — 구역정의 × {전체, 법인만}
    for zdef in dict.fromkeys(r["구역정의"] for r in srows):
        for col, tag in (("s_전체", "전체"), ("s_법인만", "법인만")):
            vals = [float(r[col]) for r in srows if r["구역정의"] == zdef]
            s_defs.append((f"{zdef}·{tag}", vals[-1], min(vals), max(vals)))
    # 인구비는 4기준의 최소·최대만 사용(전부 0.49~0.50대)
    ratios = [share2(d) for _n, d in bases]
    r_lo, r_hi = min(ratios), max(ratios)
    chk = []
    for sname, s_last, s_lo, s_hi in s_defs:
        for mname, mu in mus:
            st_lo, st_hi = mu * r_lo, mu * r_hi
            verdict = ("충족(s>s* 전 범위)" if s_lo > st_hi else
                       "미달(s<s* 전 범위)" if s_hi < st_lo else
                       "최신년 충족·범위 걸침" if s_last > st_hi else
                       "최신년 미달·범위 걸침" if s_last < st_lo else "경계")
            chk.append([sname, f"{s_last:.3f}", f"{s_lo:.3f}~{s_hi:.3f}",
                        mname, f"{mu:.3f}", f"{st_lo:.3f}~{st_hi:.3f}", verdict])
    p2 = os.path.join(PUB, "param_sstar_check.csv")
    with open(p2, "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# 정리 4: 이윤 채널이 격차 축소 방향 ⇔ s > s* = μ·n₂/n. "
                f"n₂/n = {r_lo:.4f}~{r_hi:.4f}(4기준). s 범위 = 2020~2023\n")
        w = csv.writer(f)
        w.writerow(["s정의", "s_최신", "s_범위", "μ사양", "μ", "s*_구간", "판정"])
        w.writerows(chk)
    print(f"\nn₂/n 4기준 구간 {r_lo:.4f}~{r_hi:.4f}")
    print(f"{'s 정의':<22}{'최신':>6} {'범위':<13}{'μ사양':<7}{'s*':<13} 판정")
    for c in chk:
        print(f"{c[0]:<22}{c[1]:>6} {c[2]:<13}{c[3]:<7}{c[5]:<13} {c[6]}")
    print("저장:", p1, p2, sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
