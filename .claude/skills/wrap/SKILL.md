---
name: wrap
description: 세션 마무리 루틴 — 검증, 문서 갱신 누락 점검, 커밋·푸시, 다음 할 일 출력
---

# /wrap — 세션 마무리 루틴

## 절차

1. **검증**: `validate_layers.py` + `validate_sources.py` 실행. FAIL이면 여기서
   멈추고 원인 보고(검사를 고치지 않는다).
2. **문서 갱신 누락 점검**: 이번 세션에서 한 일 대비 네 장부의 반영 여부 확인 —
   - `docs/analysis_note.md` (분석 단계 완결 시)
   - `docs/data_provenance.md` (모수 추정·데이터 가공 완결 시)
   - `docs/qa_econ_concepts.md` (개념 질문이 있었을 시)
   - `references/key-evidence.md` (새 외부 근거·확정 사실)
   누락이 있으면 지금 채운다(쉬운 용어, 기존 문체).
3. **git 정리**: `git status`로 변경 확인 → 민감 파일(공간 원본·internal) 없음
   확인 → 변경 요지를 담은 커밋 메시지 제안 → **사용자 확인 후** 커밋·푸시.
   (pre-commit 훅이 차단하면 우회하지 말고 보고.)
4. **다음 할 일**: 사용자 액션 대기 / Claude 작업 대기를 나눠 3~5개 출력.
   다음 세션 첫 명령으로 `/status`를 권한다.

## 주의

- 커밋 없이 끝내달라는 지시가 있으면 3번은 생략.
- `git add -f` 금지, 10MB 초과 파일 커밋 금지(훅과 동일 기준).
