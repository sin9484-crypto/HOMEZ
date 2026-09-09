# HOMEZ V7 실제 쿠팡 상품등록 후 감사

- 감사일: 2026-08-30
- 대상: `C:\Users\Daum pc\Homez-OS`
- 범위: 쿠팡 판매자배송 상품등록 1~10단계, 실제 등록 응답, 등록 후
  상태 관리, 운영 DB 및 관련 UI
- 현재 판정: `LIVE_CREATED_WITH_POST_LISTING_DEFECTS`

## 결론

쿠팡 상품 생성 1회는 실제로 성공했다. 실제 응답은 최상위
`code=SUCCESS`와 숫자형 sellerProductId를 반환했다. 실제 상품 ID는
이 문서·코드·테스트에는 남기지 않는다(제한된 감사 증거 문서에만
보존 — 2026-08-30 후속 지시에 따른 정정).

그러나 HOMEZ가 이 성공을 정상적인 운영 기록으로 끝까지 관리하지
못했다. 성공 당시 파서가 응답 모양을 잘못 가정해 `UNKNOWN`으로
기록했고, 현재 `%LOCALAPPDATA%\HOMEZ\data\homez.db`는 그 성공 기록이
존재하기 전 상태로 후퇴해 있다. 따라서 "상품 생성 성공"은 확인됐지만
"HOMEZ 상품등록 업무 흐름 완결"은 확인되지 않았다.

## 기준선

| 대상 | 현재 값 |
|---|---|
| 저장소 branch | `main` |
| Git 상태 | 기존 WIP 271개, 보존 |
| 개발 DB SHA-256 | `7688B8B08018B5B896EE55B2318E424044B97C3E5A8493BCC6F185D9185CEF87` |
| 현재 운영 DB SHA-256 | `DC582640F91AEA606A2EF07FA58B9B49F908F15F00382FD1BB4841FF3947C71E` |
| 현재 운영 DB Migration | 38개 적용 |
| 최신 코드가 전제하는 Migration | 41개 |
| 외부 API 호출 | 이번 감사에서 0회 |
| 운영 DB 쓰기 | 이번 감사에서 0회 |

## 확인된 사실

1. 실제 성공 응답은 평면 구조였다.
   `{"code":"SUCCESS","message":...,"data":<sellerProductId 숫자,
   이 문서에는 미기재>}` 형태다.
2. 응답에는 Brand Enrollment 관련 경고가 함께 있었다.
3. 당시 HOMEZ는 성공 응답을 해석하지 못해 제출을 `UNKNOWN`으로
   기록했고 sellerProductId를 원장에 저장하지 못했다.
4. 이후 평면 성공 응답 파싱과 제출 정합화 서비스가 코드에 추가됐다.
5. 현재 운영 DB에는 sellerProductId와 성공 제출 원장이 없으며, 최근
   제출 기록은 구매옵션·vendorUserId·최대구매수량기간 오류 등 과거
   실패 기록까지만 남아 있다.
6. 현재 운영 DB에는 3개 Migration이 미적용되어 최신 모델과 일치하지
   않는다.

## 발견 사항

### P0. 운영 DB 기준선 후퇴

앞선 감사에서 확인한 성공 응답·정합화 대상 기록과 현재 운영 DB가
일치하지 않는다. 현재 DB는 2026-08-28 18:00 KST 수정 상태이고
Migration도 38개뿐이다. 이 상태에서 정합화나 신규 제출을 진행하면
성공 상품과 HOMEZ 원장이 다시 갈라지거나 중복 등록될 수 있다.

조치 전제:

- 성공 기록이 남은 DB 또는 감사 원본의 위치와 복원 경위를 확인한다.
- 현재 DB와 후보 DB를 행 단위로 비교한다.
- 원본 DB를 직접 덮어쓰지 않는다.
- 실제 쿠팡 상품 존재 여부를 읽기 전용 조회 또는 WING 화면으로
  확인한 뒤 정합화한다.

### P0. 실제 등록 상품의 오류 증거가 불완전

현재 확보된 실제 오류는 Brand Enrollment 경고 하나다. 사용자가 말한
"몇몇 오류"의 정확한 WING 필드·상태·문구·화면은 저장소에 모두 남아
있지 않다. 오류를 추측해 수정하면 다른 상품 유형을 깨뜨릴 수 있다.

필요 증거:

- WING 상품 목록의 상태와 노출 상태
- 상품 수정 화면의 오류·경고 문구 전체
- 대표이미지·상품명·브랜드·옵션·가격·배송·반품·고시정보 화면
- 실제 구매자 상품 페이지에서 잘못 표시된 항목

### P1. 성공 응답 경고 손실

`CoupangLiveProductProvider.create_product()`는 성공 시 sellerProductId만
반환하고 `message`를 버린다. 따라서 "등록 성공 + 브랜드 등록 필요"와
같은 후속 조치가 HOMEZ 결과 화면에 나타나지 않는다.

필요 수정:

- 성공 응답에도 구조화된 `warnings`를 보존한다.
- 감사로그와 10단계 결과 화면에 경고를 표시한다.
- 경고와 실패를 구분하고, 경고가 있다고 성공을 실패로 바꾸지 않는다.

### P1. 브랜드 계약의 UI·실 Provider 미완성

백엔드에는 `NO_BRAND`/`OFFICIAL_BRAND`/`UNRESOLVED` 3상태 계약이
추가됐지만, 필드 대조표가 명시하듯 실제 프론트엔드 브랜드 선택 UI가
완성되지 않았고 실제 쿠팡 브랜드 검색 Provider도 없다. Fake 검색만
존재한다. 이 상태는 범용 상품등록으로 볼 수 없다.

### P1. 성공 판정이 지나치게 느슨함

현재 파서는 최상위 `code`가 없는 응답도 허용한다. `data`에 임의의
scalar가 있으면 성공으로 오판할 수 있다. 성공은 확인된 성공 code와
유효한 sellerProductId가 함께 있을 때만 인정해야 한다.

### P1. 등록 후 원장 정합화가 설치·운영 DB에 검증되지 않음

`submission_reconciliation_service.py`는 append-only 정합화 계약을
구현했지만 현재 운영 DB가 필요한 최신 Migration 상태가 아니며 실제
상품을 대상으로 정합화가 완료됐다는 증거도 없다.

### P2. 실제 ID와 응답을 테스트 fixture에 직접 사용

실제 sellerProductId와 실제 경고 원문이 테스트에 직접 들어 있다.
Credential은 아니지만 테스트 데이터와 운영 증거를 분리하는 편이
낫다. 운영 증거는 제한된 감사 문서에 보존하고 테스트는 합성 ID를
사용해야 한다.

### P2. 등록 후 상태 의미가 혼재됨

`SAVED`, `IN_REVIEW`, `APPROVING`, `APPROVED`는 모두 상품 존재를
확인하는 상태지만 판매 승인·노출 완료의 의미는 서로 다르다. 정합화와
판매 성공 판정을 별도 상태로 관리해야 한다.

## 테스트

저장소의 `venv`에서 다음 집중 테스트를 실행했다.

- `tests.test_coupang_submission_contract`
- `tests.test_coupang_live_submission`
- `tests.test_submission_reconciliation`
- `tests.test_listing_wizard_ui`

결과: `Ran 157 tests in 334.884s`, `OK`.

이는 현재 단위·통합 계약이 통과한다는 뜻이지, 실제 등록 상품의 모든
필드가 정상이라는 의미는 아니다. 전체 회귀는 이번 감사에서 아직
실행하지 않았다.

## 권장 진행 순서

1. WING의 실제 오류 화면과 상품 표시값을 증거로 확보한다.
2. 성공 기록이 존재한 DB와 현재 운영 DB의 교체·복원 경위를 확인한다.
3. sellerProductId(제한된 감사 증거 문서 참고)의 존재·상태를 읽기
   전용으로 확인한다.
4. 성공 응답 경고 보존과 엄격한 성공 판정을 수정한다.
5. 브랜드 3상태 UI와 실제 Provider 계약을 완성한다.
6. 제출 정합화를 격리 DB에서 검증한 뒤 운영 적용 계획을 승인받는다.
7. 집중 테스트, 전체 회귀, 격리 패키징을 순서대로 수행한다.
8. 모든 결함이 닫히기 전 신규 실제 상품등록을 재시도하지 않는다.

## 현재 완료 기준

- `LIVE_CREATED`: 충족
- `LIVE_RECORDED_IN_HOMEZ`: 미충족
- `LIVE_POST_STATUS_VERIFIED`: 미충족
- `LIVE_FIELD_QUALITY_VERIFIED`: 미충족
- `WORKFLOW_1_LIVE_END_TO_END_VERIFIED`: 미충족
- `V7_RELEASE_READY`: 미충족
