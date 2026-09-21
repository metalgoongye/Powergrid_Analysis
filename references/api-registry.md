# API 소스 레지스트리

각 항목은 **실제 호출 1건으로 검증한 뒤에** 등록한다. 문서와 응답이 다르면 응답이
맞다. 미검증 항목은 파이프라인에서 쓰지 않는다. 키는 환경변수에만 둔다.

---

## VWorld 오픈API (국토교통부)

- 발급: https://www.vworld.kr → 오픈API → 인증키. **키가 등록 도메인/referer에
  바인딩**되므로 서버 스크립트 호출 시 referer 헤더 필요 여부를 검증 시 확인할 것
- 일일 호출한도 있음 (발급 화면에서 확인 후 여기에 기록)
- 용도 후보: WFS(행정경계 — 단, 본 연구 정본은 로컬 SGIS 경계), 지오코더
  (주소→좌표), WMTS(배경지도·위성 타일)
- 환경변수: `VWORLD_API_KEY`
- 최종 확인: (미검증)

## 통계청 SGIS 오픈API

- 발급: https://sgis.kostat.go.kr → 개발지원센터. consumer_key/secret →
  토큰 발급(`/OpenAPI3/auth/authentication.json`) 후 호출
- 용도 후보: 인구·가구·사업체 격자/집계구 통계, 경계 최신화 확인
- **주의**: SGIS 행정구역 코드는 통계청 코드로 행정표준코드와 다름.
  `references/` 사전으로만 변환
- 환경변수: `SGIS_CONSUMER_KEY`, `SGIS_CONSUMER_SECRET`
- 최종 확인: (미검증)

## KOSIS 공유서비스

- 엔드포인트: `https://kosis.kr/openapi/Param/statisticsParameterData.do`
- 인증: `apiKey` 파라미터, 환경변수 `KOSIS_API_KEY`
- 알려진 함정(교육 리포에서 검증된 사항, 본 저장소에서는 재검증 전 참고만):
  필수 파라미터 누락 시 200 + 에러 객체 반환 / `DT`가 문자열 / `C1`은
  행정표준코드가 아님 / `objL1=ALL`이면 시군구까지 옴
- 용도 후보: 시군구 계약종별 판매전력량 (통계표 ID 탐색 필요)
- 최종 확인: (미검증 — 본 저장소 기준)

## 공공데이터포털 (data.go.kr)

- 한전·전력거래소 자료 창구. 자료별로 파일(다운로드)과 오픈API가 혼재
- 보유: 읍면동별 공급가능 변전소 CSV (2024-05-13, `config/sources.yml` 등록됨)
- 용도 후보: 변전소별 여유용량, 발전설비 현황 등 — 자료별 개별 검증
- 환경변수: `DATA_GO_KR_KEY`
- 최종 확인: (미검증)

## 국토정보플랫폼 (국토지리정보원)

- **API가 아니라 신청·다운로드 기반.** 항공정사영상(25cm)·수치지형도 등
- 절차: 포털 신청 → 다운로드 → sha256 manifest 작성 → `config/layers.yml`에
  raster로 등록 → 검증. `collect` 자동화 대상이 아님
- 대용량(전국 정사영상 수 TB)이므로 **연구 대상 권역(경계 회랑)만 선별 취득**
  (docs/04_geoai_feasibility.md, D7 결정 참조)
- 최종 확인: (해당 없음 — 수동 절차)

## Copernicus Data Space (Sentinel-2)

- STAC 카탈로그: `https://catalogue.dataspace.copernicus.eu/stac`
- 인증: 계정 발급 → 토큰. 환경변수 `CDSE_TOKEN`
- 용도 후보: 10m 다중분광 — 광역 토지피복·야간광 대체 지표, GeoAI 파일럿 보조.
  25cm 정사영상과 역할 구분(광역 저해상 vs 회랑 고해상)
- 최종 확인: (미검증)

---

## 등록 양식

```
- 엔드포인트:
- 인증:
- 필수 파라미터:
- 응답 최상위 구조 / data_path:
- 페이지 처리:
- 결측 표기:
- 호출 제한:
- 출처 표기 문구 / 라이선스:
- 최종 확인: YYYY-MM-DD (실호출 결과 요약)
```
