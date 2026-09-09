# HOMEZ V3.1 Coupang Marketplace Integration Foundation

# Current Version

Foundation 단계 완료(2026-07-29, 같은 날 재감사 반영 정책 하드닝
포함). ProductCandidate(APPROVED) → 쿠팡 정책 검증(fail-closed) →
수익성 계산(Decimal) → Dry Run(고시정보 게이트 포함) → 운영자 최종
승인(READY_FOR_SUBMISSION)까지만 구현되어 있다. 실제 쿠팡 API 호출,
실제 상품 등록, 실제 주문 수집·발주·정산 반영은 이번 단계 범위 밖이며
코드 자체가 존재하지 않는다.

# 재감사 반영 이력 (2026-07-29)

최초 구현 완료 보고(`HOMEZ_V3_1_COUPANG_FOUNDATION_READY`) 이후
Cursor/ChatGPT 재감사에서 High 2건 + Medium 2건이 지적되어 하드닝을
진행했다:

- **High-1(정책 0건 fail-open) → 수정 완료**: 활성 정책 규칙이 0건이면
  자동으로 `SELLABLE`로 판정하던 문제를 제거하고, 사용 가능한(VERIFIED·
  활성·완전·유효기간 내) 정책 세트가 하나도 없으면
  `RiskLevel.POLICY_DATA_UNAVAILABLE`로 fail-closed하도록 변경했다.
- **High-2(단순 문자열 매칭) → 수정 완료**: `CoupangPolicyRule`에
  `match_field`/`match_value`를 도입해 구조화된 필드(카테고리 코드,
  브랜드, 해외구매대행 여부, PCC 필요 여부, GTIN/MPN 누락 여부) 매칭을
  우선하고, 자유문자(KEYWORD) 매칭은 보조 위험 탐지로만 사용하도록
  제한했다(KEYWORD+SELLABLE 조합은 판정 후보에서 제외).
- **Medium-1(고시정보 계약 부재) → 수정 완료**: `CoupangProductNotice`
  테이블을 신설하고, VERIFIED 상태·비어있지 않은 content를 가진
  고시정보가 최소 1건 없으면 Dry Run이 실패하도록 gateway의
  `REQUIRED_PAYLOAD_FIELDS`에 `notices`를 추가했다.
- **Medium-2(Fixture 인증 상태 부재) → 수정 완료**: `CoupangPolicySet`
  헤더 테이블에 `status`(DRAFT/VERIFIED/EXPIRED/REVOKED) 필드를 두고,
  `policy_data.py`의 기본 Fixture는 항상 `DRAFT`로 고정해(자동으로
  VERIFIED가 되지 않음) 실제 운영 반영 전에는 시스템이 항상
  fail-closed 상태를 유지하도록 했다.

# 판매 모델 고정

- 기본: 쿠팡 마켓플레이스 위탁판매/주문대행(`SalesMethod.MARKETPLACE`)
- 선택형 확장: 로켓그로스(`SalesMethod.ROCKET_GROWTH`)
- 두 판매방식은 `CoupangMarketplaceProduct.sales_method` 한 필드로
  구분되며, 재고·배송 Flow를 혼합하지 않는다.

# Domain 구조

`app/domains/coupang/` (신규, `app/domains/marketplace`/`order`/
`purchase`/`shipment`/`inventory`와는 별개 — 이들은 FK 기반의 기존
WIP e-commerce 도메인이며 이번 Coupang 계약과 목적이 다르다). 8개
테이블: `coupang_marketplace_products`, `coupang_product_options`,
`coupang_policy_sets`, `coupang_policy_rules`, `coupang_product_notices`,
`coupang_profit_estimates`, `coupang_dry_run_attempts`,
`coupang_integration_decisions` — 전부 FK 없음(논리 참조만).

# 상품 계약 (CoupangMarketplaceProduct)

섹션4 요구 필드 전부 반영 + 재고 신뢰도 분리: `supplier_stock`(공급처
재고) ≠ `marketplace_exposure_stock`(노출 재고,
`max(supplier_stock - safety_stock, 0)`) ≠ `safety_stock`.
`last_stock_checked_at`가 없거나 24시간 이상 오래되면
`marketplace_exposure_stock`을 0으로 강제하고 `stock_review_required`
를 True로 표시한다.

구매옵션은 `CoupangProductOption` 별도 테이블(구조화된 행)로 관리하며
문자열 한 칸에 저장하지 않는다.

GTIN/MPN이 모두 없으면 `identifier_exemption_reason`(예외 근거)이
반드시 있어야 정책 검증을 통과한다.

# 정책 검증 (fail-closed, 재감사 반영)

## CoupangPolicySet — 정책 세트 헤더

`policy_set_id`/`policy_version`/`source_reference`/`status`/
`is_complete`/`is_active`/`checked_at`/`effective_at`/`expires_at`.
다음을 **모두** 만족해야 "사용 가능"한 세트로 취급된다
(`CoupangIntegrationService._is_policy_set_usable`):

- `status == VERIFIED`
- `is_active == True`
- `is_complete == True`
- `policy_version`/`source_reference`가 비어있지 않음
- `effective_at <= 지금`
- `expires_at`이 없거나 아직 지나지 않음

사용 가능한 세트가 **하나도 없으면** `_evaluate_policy_rules()`는
규칙을 평가하지 않고 즉시 `RiskLevel.POLICY_DATA_UNAVAILABLE`을
반환하며, `validate_policy()`는 이를 `VALIDATION_FAILED`로 반영한다
(과거처럼 `SELLABLE`로 fail-open하지 않는다).

status를 `VERIFIED`로 바꾸는 것은 코드가 자동으로 하지 않는다 —
`POST /coupang-policy-sets`(admin_guard)를 통해 실제 운영자가
명시적으로 수행해야 하는 별도 작업이다.

## CoupangPolicyRule — 구조화 우선 매칭

`match_field`가 구조화된 필드(`display_category_code`/`brand`/
`overseas_purchase_agency`/`pcc_needed`/`gtin_mpn_missing`)이면 상품의
해당 값과 `match_value`를 정확히 비교한다. `match_field == KEYWORD`면
상품명/브랜드/카테고리 코드 문자열에 `match_value`가 포함되는지만
본다(자유문자, 보조 위험 탐지 전용).

**KEYWORD 매칭만으로는 SELLABLE을 확정할 수 없다** —
`risk_level == SELLABLE`인 KEYWORD 규칙은 후보에서 제외되고,
구조화된 필드 매칭만이 SELLABLE을 확정할 수 있다. 매칭되는 규칙이
하나도 없으면 `OPERATOR_REVIEW_REQUIRED`로 안전하게 판정한다(SELLABLE
기본값 없음). 여러 규칙이 매칭되면 `RiskLevel.SEVERITY`가 가장 높은
것을 채택한다(PROHIBITED > CERTIFICATION_REQUIRED >
OPERATOR_REVIEW_REQUIRED > SELLABLE) — PROHIBITED는 항상 SELLABLE보다
우선한다.

`CoupangIntegrationService`에는 risk_level을 낮추거나 정책 판정을
우회하는 메서드가 존재하지 않는다(테스트로 강제).

## policy_data.py — DRAFT 고정 Fixture

기본 제공 Fixture(`build_draft_policy_set`/`build_draft_policy_rules`)
는 `status=DRAFT`로 고정되어 있어 그대로 사용해도 시스템은
`POLICY_DATA_UNAVAILABLE`로 fail-closed한다. 앱 시작 시나 Migration에서
자동으로 시딩되지 않는다 — 실제 활성화는 별도 승인 하의 관리자 작업.

# 수익성 계산 (CoupangProfitEstimate)

전부 Decimal(Numeric), Float 미사용(AST 기반 테스트로 강제).
`expected_net_settlement == gross_revenue - marketplace_fee_total`
불변식, `coupang_sales_fee`가 UNKNOWN(None)이면 계산 자체 차단, 최소
마진 기준(5%) 미달 시 `VALIDATION_FAILED`로 되돌려 Dry Run 진행 차단.
`required_funding`은 예상 정산액과 무관하게 공급처 지급 예상치로만
계산되고 Funding에 전혀 반영되지 않는다(소스 레벨 import 금지로 강제).

# 고시정보 (CoupangProductNotice, 재감사 Medium-1 반영)

`notice_category_name`/`notice_category_detail_name`/`content`/
`source`/`verified_at`/`status`(DRAFT/VERIFIED). Dry Run은
`status == VERIFIED`이고 `content`가 비어있지 않은 고시정보가 최소
1건 이상 있어야 통과한다(`gateway.py`의 `REQUIRED_PAYLOAD_FIELDS`에
`notices` 포함). 빈 문자열·임시 문자열·자동 생성된 허위 정보를
허용하지 않는다 — 실제 고시정보가 없으면 Dry Run이 정직하게 실패한다.

**한계**: 상품 카테고리별로 정확히 어떤 고시정보가 필수인지의
전체 taxonomy는 이번 단계에 구현하지 않았다(실제 쿠팡 정책 문서 확인
필요) — 현재는 "VERIFIED 고시정보가 최소 1건 이상"이라는 최소 기준만
강제한다.

# Dry Run

`CoupangDryRunGateway.submit_dry_run()`은 외부 네트워크 라이브러리를
import하지 않고(ast 기반 검증), 함수 시그니처에 자격증명 파라미터가
없다. 완전한 Payload(옵션 + VERIFIED 고시정보 포함)만 PASSED를
반환한다. Dry Run 통과(`DRY_RUN_PASSED`)까지만 도달하며, 그 이후 실제
등록 상태(`SUBMITTED` 이상)로는 어떤 서비스 메서드도 전이시키지 않는다.

# 상태 모델

```
DRAFT → (READY_FOR_REVIEW | VALIDATION_FAILED)   # 정책 검증(fail-closed)
READY_FOR_REVIEW → (그대로 유지 | VALIDATION_FAILED)  # 수익성 계산(마진 미달 시)
READY_FOR_REVIEW/DRY_RUN_FAILED → APPROVED_FOR_DRY_RUN → (DRY_RUN_PASSED | DRY_RUN_FAILED)
DRY_RUN_PASSED → READY_FOR_SUBMISSION   # 운영자 최종 승인
```

`VALIDATING`은 계약(constants.py)에는 정의되어 있으나, 정책 검증이
동기적으로 즉시 끝나는 연산이라 실제로는 DRAFT에서 직접
READY_FOR_REVIEW/VALIDATION_FAILED로 전이한다. `SUBMITTED`/`REVIEWING`/
`APPROVED`/`REJECTED`/`SUSPENDED`는 상수에만 존재하고 이번 단계
서비스는 어떤 메서드도 이 상태들로 전이시키지 않는다.

# Transaction / 멱등성

`app/domains/product_candidate/service.py`와 동일한 패턴: Repository는
flush만 하고 Service가 commit 1회 소유, 조건부 UPDATE + rowcount 검증,
IntegrityError는 rollback 후 승자 재조회. 멱등성 대상: 초안 생성,
Dry Run 시도, 운영자 최종 승인(각각 독립된 idempotency_key). 동시
정책 검증 경쟁, 동시 최종 승인 경쟁 모두 실스레드 테스트로 검증됨.

# Migration

`migrations/20260729_00_create_coupang_integration_schema.sql` — 8개
테이블(Model 기준, FK 없음). 최초 6개 테이블로 작성된 후 같은 날
재감사 반영으로 정책 세트/고시정보 테이블이 추가되어 8개로 재작성됐다
— 이 파일은 재작성 전후 모두 실제 homez.db에 적용된 적이 없음을
재확인(크기/mtime 불변)했으므로, 별도 후속 Migration을 만드는 대신
같은 파일을 최신 Model 기준으로 완전히 재작성하는 방식을 택했다.
임시 SQLite DB에서만 정적/적용/재적용실패/중간실패 rollback 검증
완료. **실제 homez.db에는 미적용.**

# 다음 단계 (이번 단계 범위 밖, 별도 승인 필요)

1. `CoupangPolicySet`/`CoupangPolicyRule` 실제 데이터 시딩 — 실제
   쿠팡 공식 정책 문서 대조 후, `POST /coupang-policy-sets` +
   `POST /coupang-policy-sets/{id}/rules`(admin_guard)를 통해 운영자가
   명시적으로 status=VERIFIED 세트를 만들어야 한다. 이 작업 전에는
   시스템이 항상 fail-closed 상태를 유지한다(의도된 동작).
2. 상품 카테고리별 필수 고시정보 taxonomy 구체화(현재는 "VERIFIED
   고시정보 최소 1건" 최소 기준만 강제).
3. 실제 쿠팡 Open API 연동(`CoupangOpenApiGatewayConfig` 타입 계약을
   실제 구현으로 교체) — Wing API Key/HMAC 서명/재시도/Rate Limit 등.
4. 실제 상품 등록(SUBMITTED 이후 상태) → 주문 수집 → Funding Hold →
   공급처 구매 → 배송 → 구매확정 → Marketplace Settlement → Funding
   Add 순서의 다음 Phase 구현.

# Last Updated

2026-07-29 (재감사 정책 하드닝 반영)

# Updated By

Claude
