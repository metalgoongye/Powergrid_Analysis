---
name: status
description: 연구 현황판 — 모형 모수 4종 상태, 대기 작업, 마감 D-day를 한 화면으로 출력
---

# /status — 연구 현황판

다음을 읽어 현황판을 출력한다. 값을 새로 계산하지 않는다(있는 산출물만 읽음).

1. **마감**: 워킹페이퍼 마감 2026-11-27 기준 D-day를 오늘 날짜로 계산.
2. **모수 현황**: `docs/data_provenance.md` §0 표를 기준으로 하되,
   `out/public/param_*.csv`가 더 최신이면 그 값으로 보정해 표로 출력
   (λ, s, σ(Z), μ, w, ε·e — 값/상태/다음 단계).
3. **파이프라인 단계**: 1단계(수요지도)·2단계(권역)·3단계(GeoAI)·4단계(후생)의
   완료/진행/대기 — `docs/analysis_note.md` 목차와 `out/` 산출물 존재로 판정.
4. **대기 작업**: `docs/data_provenance.md` §5(보유·대기 자료) +
   `references/key-evidence.md`의 "할 일" 표식을 긁어 목록화.
   사용자 액션 대기(다운로드·신청·결정)와 Claude 작업 대기를 구분해 표시.
5. **건강 점검**: `validate_layers.py`, `validate_sources.py`,
   `param_checklist.py`(모수별 재료·코드·산출물 실존 검사)를 실행해
   각 한 줄 요약. param_checklist에서 누락(X)이 나오면 최상단에 경고.

출력은 간결한 현황판 하나로: 표 2개(모수, 단계) + 대기 목록 + 검증 요약.
새 분석을 시작하지 말 것 — 이 명령은 읽기 전용 점검이다.
