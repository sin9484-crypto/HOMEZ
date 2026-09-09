# 실제 Provider 연결 체크리스트(Gate RP-2, 2026-08-22)

이 문서는 `app/domains/retail_purchase/provider.py`의 미계약
Provider(`ProcurementGatewayProvider`/`OfficialMarketplacePurchaseProvider`/
`CorporateProcurementProvider`/`VirtualCardProcurementProvider`/
`DirectSupplierProvider`) 중 하나를 실제로 연결할 때 순서대로 확인할
항목이다. 어떤 항목도 건너뛰고 "연결됨"으로 표시하지 않는다 —
`app/domains/retail_purchase/router.py::_connection_status_for()`가
반환하는 상태는 항상 아래 단계와 정직하게 일치해야 한다.

## 0. 전제

- [ ] 이 문서의 "확인 불가" 항목은 실제 파트너/개발자 지원팀에 문의해
      해소한 뒤에만 다음 단계로 진행한다(추정으로 채우지 않는다).
- [ ] 계약·API 키·Webhook secret은 이 저장소(git)에 절대 커밋하지
      않는다 — `app/core/windows_credential_store.py`(또는 동등한
      운영 환경의 Secret 저장소)에만 둔다.

## 1. 계약·법무

- [ ] 서면 계약(또는 공식 파트너 프로그램 가입) 완료.
- [ ] 개인정보 처리위탁 조건 확인 및 처리방침 반영(연구 결과 대부분
      "확인 불가" — 문의 필요, 아래 3절 참고).
- [ ] 취소·반품 시 재고 소유권·비용 부담 조항이 계약서에 명시됨
      (일반 법리상 "재고 소유권 보유 측이 부담"이 원칙이나 계약별로
      다름 — 반드시 이 계약의 실제 조항을 확인).
- [ ] 세금계산서/거래명세서 발급 주체(HOMEZ vs 사업자 회원) 확인.

## 2. Capability 선언(`ProviderCapability`)

Provider가 실제로 지원하는 기능만 `capabilities`에 선언한다 —
지원하지 않는 기능은 화면 자동화로 몰래 보완하지 않고 그대로
`NOT_SUPPORTED`로 둔다.

- [ ] `PRODUCT_SEARCH` — 공식 검색 API 문서 확인, 응답 필드를
      `ProductSearchResultItem`에 정확히 매핑.
- [ ] `LIVE_PRICE` / `LIVE_STOCK` — 실시간 가격·재고 조회 가능 여부.
- [ ] `PURCHASE_QUOTE` — 견적 API(만료 시각 포함) 존재 여부.
- [ ] `ORDER_CREATE` — 실제 발주 API. **이 capability를 선언하기
      전까지는 `place_order()`가 `LiveInputRequiredError`로 계속
      차단돼야 한다.**
- [ ] `ORDER_CANCEL` / `REFUND` — 취소·환불 API.
- [ ] `TRACKING` — 배송조회 API.
- [ ] `BALANCE` — 잔액/한도 조회 API.
- [ ] `IDEMPOTENCY` — Provider 쪽이 자체 idempotency key를
      지원하는지(지원 안 하면 HOMEZ 쪽 `idempotency_key`만으로 재요청
      안전성을 보장할 수 있는지 별도 검토).
- [ ] `SANDBOX` — 테스트 환경 제공 여부(이번 조사에서 대부분 플랫폼
      "확인 불가" — 계약 시 반드시 확인).

## 3. 오류 계약(`ProviderErrorCode`)

- [ ] Provider의 실제 오류 응답을
      `NOT_SUPPORTED`/`PROVIDER_NOT_CONNECTED`/`CREDENTIAL_REQUIRED`/
      `REAUTH_REQUIRED`/`PRODUCT_NOT_FOUND`/`PRODUCT_MISMATCH`/
      `OUT_OF_STOCK`/`PRICE_CHANGED`/`BUDGET_EXCEEDED`/
      `POLICY_BLOCKED`/`RATE_LIMITED`/`PURCHASE_FAILED`/
      `RESULT_UNCERTAIN`/`PROVIDER_UNAVAILABLE` 14종 중 하나로 매핑하는
      표를 작성한다(Provider 원본 오류코드 → 표준 코드 대응표).
  - [ ] "결제됐는지 실패했는지 서버 응답만으로 확신할 수 없는" 모든
        경우는 예외를 던지지 않고 `PlaceOrderResult.status="UNCERTAIN"`
        으로 반환하도록 구현한다(절대 재시도 유도 금지 — 이 규칙은
        정책으로도 끌 수 없다).
  - [ ] Rate limit 응답에는 `retry_after_seconds`를 채운다.
  - [ ] `correlation_id`/`idempotency_key`가 요청→응답까지 그대로
        보존되는지 실측 확인(로그로 대조).

## 4. Webhook(선택, Provider가 제공하는 경우만)

- [ ] 실제 signing secret을 안전한 저장소에 연결(`app/domains/
      retail_purchase/webhook.py::verify_webhook_signature()`가
      secret 없이는 항상 실패하는 것을 그대로 유지 — secret 연결
      전에는 어떤 Webhook도 성공 처리되지 않아야 한다).
  ⚠️ **주의**: `webhook.py`는 서명 검증·재사용(replay) 방지 계약까지만
  구현되어 있다. 실제 이벤트를 받아 `RetailPurchaseOrder` 상태를
  갱신하는 dispatch 로직은 아직 없다(NOT_IMPLEMENTED) — Provider
  연결 시 반드시 별도로 구현해야 한다.
- [ ] Provider의 실제 서명 알고리즘이 HMAC-SHA256 hex인지 확인(다르면
      `verify_webhook_signature()`를 그 Provider 전용으로 확장).
- [ ] `(provider_code, event_id)` UNIQUE 제약이 실제 Provider의
      이벤트 ID 체계와 충돌하지 않는지 확인.

## 5. 결제계정(`PaymentAccountReference`)

- [ ] `external_account_reference`가 실제 Credential Manager(또는
      운영 환경의 동등한 Secret 저장소) target name을 정확히 가리키는지
      확인 — 카드번호·CVC·PIN·비밀번호가 이 테이블 컬럼 어디에도
      새로 추가되지 않았는지 마이그레이션 diff로 재확인.
- [ ] `status` 값이 `ProviderConnectionStatus`(`CONTRACT_REQUIRED`→
      `CREDENTIAL_REQUIRED`→`CONNECTED`→`REAUTH_REQUIRED`→
      `AUTO_PURCHASE_ENABLED`/`AUTO_PURCHASE_DISABLED`)를 실제 상태와
      정직하게 반영하는지 확인.

## 6. 테스트

- [ ] 실 Provider의 공식 Sandbox(있는 경우)로 `test_retail_purchase_*`
      와 동일한 시나리오 집합을 재현 — 실제 계정으로 첫 최소 금액
      구매를 시도하기 전, Sandbox에서 먼저 전부 통과해야 한다.
- [ ] Sandbox가 없는 Provider는 이 사실 자체를 `docs/HOMEZ_PROJECT_
      STATE.md`에 정직하게 기록하고, 실 계정 최초 검증은 사용자
      명시 승인 아래 최소 금액으로만 진행한다(LIVE_PURCHASE_
      APPROVAL_REQUIRED).

## 7. UI

- [ ] `app/web/console.js::RP_PROVIDER_LABEL_KEYS`에 실제 Provider
      표시명 추가(이미 존재하면 그대로 재사용).
- [ ] `/retail-purchase/providers` 응답이 이 체크리스트의 실제 진행
      단계와 정확히 일치하는지 확인(과장 금지 — capability가 하나라도
      실 연동 전이면 그 capability는 계속 `not_supported`에 남는다).

## 8. 최종 승인

- [ ] 위 1~7 전부 완료 확인 후에만 `RetailPurchaseOrder.place_order()`
      가 이 Provider에 대해 실제로 호출되도록 코드 변경(현재는
      `_UncontractedRetailPurchaseProvider`가 이 Provider를 대신하고
      있음 — 실 계약 완료 시 전용 서브클래스로 교체).
- [ ] 첫 실 발주는 사용자의 명시적 승인과 함께, 최소 금액·단일 건으로만
      진행한다.
