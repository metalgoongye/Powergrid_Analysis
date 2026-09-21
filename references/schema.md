# 스키마와 중단 조건

이 저장소는 자료를 세 부류로 다룬다. 부류마다 스키마와 검증이 다르다.

## 1. 표형 지표 (통계)

KOSIS 판매전력량, 한전 CSV 등. 표준 10컬럼으로 수렴한다.

```
source | indicator_code | region_code | period | value | unit | vintage | retrieved_at | source_url | missing_reason
```

- `region_code`: 행정표준코드 기준. SGIS 코드·KOSIS C1 코드는 정규화 단계에서
  사전으로 변환하고, 사전에 없으면 멈춘다.
- 결측 3구분: `NA_NOTSURVEYED`(미조사) / `NA_NOTAPPLICABLE`(해당없음) /
  `NA_CONFIDENTIAL`(비공개). 사유 없는 빈칸 금지.

## 2. 공간 벡터 레이어

`config/layers.yml`에 등록된 것만 쓴다. 레이어 메타 스키마:

| 항목 | 필수 | 내용 |
|---|---|---|
| `id` | ✔ | 저장소 내 유일 식별자 |
| `kind` | ✔ | gpkg / shapefile / raster |
| `path` | ✔ | DATA_ROOT 기준 상대경로 |
| `source` | ✔ | 출처·export 일자 |
| `license` | ✔ | ODbL / 공공누리 등. 파생물에 전파 |
| `security` | ✔ | internal(좌표 산출물 비공개) / public |
| `crs_epsg` | ✔ | 원본 좌표계 |
| `expected_count` | ✔ | 검증 기준 피처 수 (gpkg는 레이어별) |
| `verified` | ✔ | 마지막 검증일 |

## 3. 래스터 / 영상

취득 후 `layers.yml`에 등록. 추가 항목: `resolution_m`, `acquisition_date`,
`manifest`(타일/씬 파일 목록 + sha256). 원본 영상은 절대 저장소에 넣지 않는다.

## 중단 조건 (검증 실패 → 산출물 생성 금지)

`scripts/validate_layers.py` 기준:

- 등록된 파일이 없음 (경로 오류·이동)
- gpkg에 등록된 레이어가 없음
- 피처 수가 `expected_count`와 불일치 (재수집 시 사용자 확인 후 기준값 갱신)
- 좌표계가 `crs_epsg`와 불일치
- (geopandas 도입 후 확장) invalid geometry 존재, extent가 한반도 bbox
  (EPSG:4326 기준 124~132E, 33~39N) 이탈

표형 소스: 행 수 불일치, 스키마 컬럼 누락, 중복 키, 사유 없는 결측.

## 기록만 하고 진행 (경고)

- 전압·용량 등 속성 결측 비율 (원자료가 실제로 결측)
- 마스킹된 값(한전 CSV 변전소명 `가*` 등)의 매칭 판정 — 코드북에 미확인으로 기록

## 공간 산출물 규칙

- `out/internal/`: 좌표·지오메트리 포함 산출물. git 제외. 외부 반출 금지.
- `out/public/`: 행정구역(시도·시군구·읍면동)·격자 집계 단위만. 개별 설비
  위치가 역산되는 수준의 세분화 금지 (제안서 6쪽, docs/03_decisions.md D6).
- ODbL 원천(KOR.gpkg) 파생물에는 `© OpenStreetMap contributors, ODbL` 고지 포함.
