# 2026_WP — 공간정보 분석 파이프라인

**GeoAI·공간정보 기반 지역별 차등 전기요금제 도입의 국토균형발전 효과 분석**
(2026 Working Paper)의 데이터 파이프라인 저장소. 코드·설정·문서만 담고,
원본 데이터는 저장소 밖 `DATA_ROOT`(기본 `D:\z03.SmartGrid`)에 둔다.

연구 문서(제안서 요약·설계 결정 D1~D10·12주 계획)는 `D:\z03.SmartGrid\docs\` 참조.

## 바로 실행

```bash
pip install -r requirements.txt
python scripts/validate_layers.py
```

등록된 모든 데이터(KOR.gpkg 26 레이어, 행정경계 3종, 한전 CSV)가 레지스트리
기준값과 일치하는지 검증한다. **검증 실패 시 이후 단계 진행 금지.**

## 구조

```
CLAUDE.md                  에이전트 규칙 (보안·검증·좌표계·라이선스)
config/
  layers.yml               공간 레이어 레지스트리 ← 레이어 추가는 여기
  sources.yml              표형 소스·영상 취득 계획
references/
  schema.md                표형/벡터/래스터 스키마와 중단 조건
  api-registry.md          API 명세 (실호출 검증 후 등록)
  crs-policy.md            좌표계 정책 (분석은 EPSG:5179)
scripts/
  validate_layers.py       레지스트리 vs 실제 파일 정합성 검증
out/public/                집계 단위 산출물 (공개 가능)
out/internal/              좌표 포함 산출물 (git 제외, 반출 금지)
```

## 데이터 (로컬 보관, git 제외)

| 자료 | 위치 (DATA_ROOT 기준) | 라이선스 |
|---|---|---|
| 전력·인프라 벡터 26 레이어 | `powergrid_spatial_data/36pJ2bwdQr/KOR.gpkg` | ODbL (파생물 동일조건) |
| 행정경계 시도 17·시군구 252·읍면동 3,559 (SGIS 2025 2Q, EPSG:5179) | `행정경계/bnd_*_00_2025_2Q/` | 공공누리 |
| 한전 읍면동별 공급변전소 4,557행 | `한국전력공사_지역별 공급가능 변전소 정보_20240513.csv` | 공공데이터포털 |

## 로드맵

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | 골격·규칙·레지스트리·검증 | ✅ 완료 (검증 30건 통과) |
| 1 | 정규화: KOR.gpkg → EPSG:5179, 행정경계 공간조인 | 예정 |
| 2 | 변전소 권역 배정 (최근접·티센 / 네트워크 / 무작위화 — 결정 D1) | 예정 |
| 3 | 위성/항공영상 취득·등록 (NGII 정사영상 회랑 선별 + Sentinel-2) | 예정 |
| 4 | 공간가중치 행렬(W) 추정: 행정경계·권역 기반 queen/rook, 설비 기반 knn/distance band (libpysal), 영상 파생 변수 결합 | 예정 |
| 5 | 요금구역 대안 비교·후생모형 투입 테이블 | 예정 |

## 보안 규칙 (요약)

- 전력망 좌표가 포함된 산출물은 `out/internal/`에만. 공개는 집계 단위만.
- 원본 공간자료·`.env`는 커밋 금지 (`.gitignore` + `git add -f` 금지).
- API 키는 환경변수. `.env.example`에는 이름만.

상세 규칙은 `CLAUDE.md`.
