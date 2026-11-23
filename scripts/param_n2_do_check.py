# -*- coding: utf-8 -*-
"""[점검] 구역 2를 8개 도로 한정할 때의 인구 비중 n₂/n.

배경: param_sstar_check.csv의 '도지역만(8)' 행은 귀속률 s를 8개 도 기준으로
잰 값인데, 견주는 기준값 s* = μ·n₂/n의 n₂/n은 비수도권 14개 시도 기준
(0.490~0.504)이다. 구역 2의 범위가 서로 달라 같은 잣대가 아니므로, 8개 도
기준의 n₂/n을 같은 자료로 구해 둔다(보고서 검토 2026-11-23).

자료: 통계청 지역별고용조사 2024 하반기 공개용 A형(MDIS), 시군구가중값 적용 —
param_w_wage.py와 같은 파일·같은 코드집. 시도는 코드집 명칭의 앞머리로 가른다
(임의 매핑 없음, 17개 시도·229개 시군구 단언 검증).

출력: out/public/param_n2_do_check.csv
"""
from __future__ import annotations

import csv
import glob
import os
import sys
import warnings
from collections import Counter, defaultdict

warnings.filterwarnings("ignore")
import openpyxl  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("DATA_ROOT", r"D:\z03.SmartGrid")
D = os.path.join(DATA_ROOT, "rdb", "지역별고용조사")
OUT = os.path.join(REPO, "out", "public", "param_n2_do_check.csv")
CAPITAL = ("서울", "인천", "경기")
METRO = ("부산", "대구", "광주", "대전", "울산", "세종")
DO = ("강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주")


def group_dict():
    p = glob.glob(os.path.join(D, "*descriptionFiles", "파일설계서*.xlsx"))[0]
    ws = openpyxl.load_workbook(p, data_only=True)["코드정보"]
    cur, out, pre = None, {}, Counter()
    for r in ws.iter_rows(min_row=3, values_only=True):
        if r[1] is not None:
            cur = str(r[1])
        if cur != "행정구역코드" or r[2] is None:
            continue
        code, name = str(r[2]).strip(), str(r[3]).strip()
        if name in ("외국", "선박"):
            continue
        g = ("수도권" if name.startswith(CAPITAL) else "광역시" if name.startswith(METRO)
             else "도" if name.startswith(DO) else None)
        assert g, f"분류되지 않은 명칭: {name}"
        out[code] = g
        pre[name[:2]] += 1
    assert sum(pre.values()) == 229 and len(pre) == 17, f"시도·시군구 수 이상: {dict(pre)}"
    return out


def main() -> int:
    gd = group_dict()
    src = glob.glob(os.path.join(D, "2024_하반기*.csv"))[0]
    pop, hh = defaultdict(float), defaultdict(float)
    with open(src, encoding="cp949", newline="") as f:
        for row in csv.DictReader(f):
            g = gd.get(row["행정구역코드"])
            if g is None:
                continue
            w = float(row["시군구가중값"])
            pop[g] += w
            if row["가구주관계코드"] == "1":
                hh[g] += w
    rows = []
    for name, d in (("15세이상인구", pop), ("가구(가구주)", hh)):
        tot = sum(d.values())
        r14 = (d["광역시"] + d["도"]) / tot
        r8a = d["도"] / (d["수도권"] + d["도"])
        r8b = d["도"] / tot
        rows.append([name, f"{d['수도권']:.0f}", f"{d['광역시']:.0f}", f"{d['도']:.0f}",
                     f"{r14:.4f}", f"{r8a:.4f}", f"{r8b:.4f}"])
        print(f"[{name}] 비수도권 14개 시도/전국 {r14:.4f} · 8개 도/(수도권+8개 도) {r8a:.4f} · 8개 도/전국 {r8b:.4f}")
    # 검산: 14개 시도 기준은 param_w_wage.csv의 n₂/n과 같아야 한다
    ref = {}
    with open(os.path.join(REPO, "out", "public", "param_w_wage.csv"), encoding="utf-8-sig") as f:
        for r in csv.reader(ln for ln in f if not ln.startswith("#")):
            if r and r[0] == "n":
                ref[r[1]] = r[4]
    for r in rows:
        assert ref[r[0]] == r[4], f"기산출 n₂/n과 불일치: {r[0]} {ref[r[0]]} vs {r[4]}"
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        f.write("# 지역별고용조사 2024H2 A형, 시군구가중값. 구역 2를 8개 도(강원·충북·충남·전북·전남·경북·경남·제주)로 "
                "한정할 때의 인구 비중. 14개 시도 기준 열은 param_w_wage.csv와 일치 검산\n")
        w = csv.writer(f)
        w.writerow(["기준", "수도권", "비수도권_광역시(세종 포함)", "8개도", "n2/n_비수도권14",
                    "n2/n_8개도÷(수도권+8개도)", "n2/n_8개도÷전국"])
        w.writerows(rows)
    print("→", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
