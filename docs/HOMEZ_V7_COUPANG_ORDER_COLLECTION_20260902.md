# HOMEZ V7 쿠팡 주문 수집 완료 보고

작성일: 2026-09-02

## 최종 판정

`ISOLATED_IMPLEMENTATION_VERIFIED`

쿠팡 주문 수집의 코드, 격리 DB, Fake Provider, 데스크톱/모바일 브라우저 흐름은 검증됐다. 실제 쿠팡 주문 API 호출, 운영 DB Migration 적용, 공식 설치본 검증은 수행하지 않았으므로 `LIVE_VERIFIED`, `COMPLETE`, `RELEASE_READY`로 판정하지 않는다.

## 구현 범위

- 쿠팡 발주서 단위 조회 Provider: GET 전용, HMAC 인증, 24시간 이하 조회창, 페이지 토큰, 오류코드 정규화
- 주문 수집 위치 관리: 마지막 성공 시각, 중복 실행 잠금, 성공 시에만 위치 전진, 실패/부분 실패 재시도
- 원본 주문 정규화·보존: orderId와 shipmentBoxId 분리, 구매자/수취인 구분, 실배송 수량 계산
- HOMEZ 주문 연결: Order, OrderItem, 배송 묶음, 미연결 품목, 재고 SKU 매핑, PurchaseTask 연계
- 중복 방지: 동일 주문 재수집 시 신규 주문을 만들지 않고 중복으로 집계
- 미연결 품목 처리: 사용자가 재고 SKU를 선택해 연결하고 이후 동일 채널 SKU는 자동 연결
- 개인정보 보호: 일반 목록 마스킹, 원본 수집 응답 미노출, 배송정보는 최근 비밀번호 재인증 후 한 번만 조회
- 금액 처리: 수집 계층은 Decimal/Numeric(18,4), 기존 Float 주문 모델 진입 시 반올림 경계와 거부 조건 명시
- API: 미리보기, 수집 실행, 수집 상태, 미연결 목록, SKU 연결, 민감 배송정보 조회
- UI: 자연스러운 한국어, 판매계정/주문상태 선택, 수집 결과 카운터, 미연결 SKU 연결, 개인정보 재인증

주문 수집은 상품준비중 처리 API를 호출하지 않는다. 조회와 상태 변경 작업을 분리했다.

## 실브라우저 검증

격리 서버와 합성 개인정보만 사용했다.

1. 합성 쿠팡 계정으로 주문 1건 수집: 신규 1, 연결 대기 1, 실패 0
2. `COUPANG-E2E-SKU`를 `HOMEZ-E2E-SKU`에 연결: 연결 대기 0
3. 같은 주문 재수집: 신규 0, 중복 1
4. 일반 주문 목록 구매자명 마스킹 확인
5. 비밀번호 재인증 전 배송정보 비공개 확인
6. 재인증 후 합성 배송정보 표시 확인
7. 390x844 모바일 화면에서 수집·상세·품목·발주 영역의 표시와 조작 가능 여부 확인

브라우저 검증 중 발견해 수정한 결함:

- 수집 API 응답에서 `error_codes`를 두 번 전달해 500이 발생하던 직렬화 결함
- 격리 테스트 계정의 예약 도메인 이메일 때문에 로그인 응답 검증이 실패하던 테스트 장치 결함
- 자동 SKU 연결의 후속 작업 실행자가 0으로 기록될 수 있던 감사 추적 결함

## 테스트 결과

- 주문 수집 집중 회귀: 58/58 OK
- 주문 도메인 전체: 142/142 OK
- Migration 전체: 484/484 OK
- JavaScript/Python 구문 검사: OK
- 최종 저장소 전체 회귀: `Ran 3527 tests in 4857.909s` / `OK`

첫 전체 회귀에서 확인된 2건도 수정 후 최종 전체 회귀에 포함해 재검증했다.

- i18n 키에 쿠팡 상태 대문자를 사용한 명명 규칙 위반
- Windows PowerShell 환경에서 `Get-FileHash`가 UNKNOWN을 반환하던 읽기 전용 DB 확인 도구의 환경 의존성

## Migration

- 파일: `migrations/20260831_00_create_coupang_order_collection_schema.sql`
- SHA-256: `bac4f3eecb5c3220963bca445abb086bec8d6d28e04eabdeda2511f8b8deaf45`
- 개발 DB 및 운영 DB 적용: 안 함
- 검증: 임시 SQLite와 Migration 전체 테스트에서만 적용

## 미검증·잔여 위험

- 실제 StoreConnection의 seller identifier가 쿠팡 vendorId와 같은 의미인지 현재 모델만으로 독립 대조할 근거가 부족하다. 실제 연결 감사에서 확인해야 한다.
- 쿠팡 실응답의 모든 변형과 오류코드는 Fake Provider만으로 증명할 수 없다.
- 기존 Order/OrderItem 금액 컬럼은 Float다. 수집 경계에서 정밀도를 통제하지만 원장 수준의 완전한 Decimal 전환은 별도 Migration 과제다.
- 실제 쿠팡 API, 운영 DB, 공식 설치본은 이번 작업에서 접촉하지 않았다.

## 다음 단계

1. 운영 적용 전 DB 복사본에 Migration 리허설
2. 공식 설치 직전 전체 백업과 설치 안전성 확인
3. 사용자가 쿠팡 자격증명을 재확인한 뒤 GET 주문 조회 1회 최종 승인
4. 실제 주문 1건의 orderId/shipmentBoxId/수량/금액/마스킹/중복 방지를 대조
5. 실조회 성공 후에만 `COUPANG_ORDER_COLLECTION_LIVE_VERIFIED` 판정 검토
