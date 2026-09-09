# HOMEZ V4 Decision AI — 정책(DecisionPolicy) 거버넌스 절차

(2026-07-30, "Desktop 로그인부터 V5 진입 Gate 직전까지" 작업 — Phase H)

이 문서는 **운영 DB에 실제 정책 데이터를 시딩하지 않는다** — 절차와
API 초안만 정의한다. 실제 시딩은 별도 사용자 승인 후 진행한다.

## 1. 상태 전이

```
DRAFT → VERIFIED → (ACTIVE 는 is_active=True 플래그로 표현) → EXPIRED / REVOKED
```

`app/domains/decision/constants.py::DecisionPolicyStatus`에 이미
`DRAFT/VERIFIED/EXPIRED/REVOKED` 4개 상태가 정의돼 있다(Coupang
`CoupangPolicyStatus`와 동일한 패턴). "사용 가능"(평가에 실제로
채택됨) 조건은 `DecisionService._is_policy_usable()`이 다음을 **모두**
검사한다:

- `status == VERIFIED`
- `is_active is True`
- `is_complete is True`
- `checked_at <= now <= (expires_at or +infinity)`이고 `effective_at <= now`

하나라도 어긋나면 그 정책은 "사용 불가"로 취급되고, 사용 가능한 정책이
하나도 없으면 평가 자체가 `BadRequestException`으로 차단된다
(fail-closed — 정책 미등록을 정상 추천처럼 표시하지 않는다).

## 2. 승인 권한

`POST /decision-policies`(`app/domains/decision/router.py::create_policy`)는
이미 `admin_guard`로 보호된다 — 관리자만 정책을 생성할 수 있다. 이번
단계에서 별도의 `activate`/`verify` 전이 API는 추가하지 않았다(범위
밖) — 정책의 `status`/`is_active`/`is_complete` 값은 **생성 시점에
관리자가 직접 지정**한다(`DecisionPolicyCreateRequest`).

## 3. 버전 불변성 (이미 서비스 계층에서 강제됨)

`create_policy()`는 `policy_set_id`가 이미 존재하면 `ConflictException`
(그리고 DB `UniqueConstraint` 위반 시 `IntegrityError` 복구)으로
거부한다 — 기존 정책 행을 수정하는 API 자체가 없으므로, **한 번 만든
정책은 절대 변경되지 않는다.** 정책을 바꾸려면 반드시 **새
`policy_set_id`** (예: `homez-decision-default-v2`)로 새 행을 만들어야
한다. 이 설계 덕분에:

- 과거에 이미 완료된 평가(`DecisionEvaluation.policy_version`에 평가
  당시 정책 버전이 스냅샷으로 저장됨)는 이후 정책이 바뀌어도 그
  결과가 절대 바뀌지 않는다 — "이전 평가 재현 가능성"이 설계 자체로
  보장된다.
- 롤백은 "이전 버전으로 재전환"이 아니라 "새 정책 행을 비활성화하고
  그 이전 정책 행의 `is_active`를 다시 True로" 만드는 방식이어야
  한다 — 그러나 이번 단계에는 `is_active` 토글 API가 없으므로, 롤백이
  필요하면 **새 정책 행을 다시 생성**해 사실상 이전 가중치로 되돌리는
  방식만 가능하다(운영 절차상 한계로 명시).

## 4. 기본 가중치·임계값의 근거

이번 단계는 **실제 쿠팡/시장 데이터 기반 가중치 산정을 수행하지
않았다** — `docs/HOMEZ_V4_DECISION_AI.md`에 기록된 대로 12개 축은
균등하지 않게 설계 의도상 나눌 수 있지만, 실제 운영 투입 전에는
사업팀(ChatGPT Architecture 단계 또는 운영자)이 각 축의 상대적
중요도를 결정해 `axis_weights`(합계 정확히 1.0000)와
`min_approve_total_score`/`min_confidence_for_recommendation`를 채워야
한다. 이 문서는 그 값 자체를 제안하지 않는다(임의로 만들지 않는다는
원칙).

## 5. 운영 시딩 API 초안 (실행하지 않음 — 형태만 제시)

기존 `POST /decision-policies` 엔드포인트가 이미 이 목적의 API다.
호출 예시(실행하지 않음, 문서화 목적):

```json
POST /decision-policies
{
  "policy_set_id": "homez-decision-default-v1",
  "policy_version": "1.0.0",
  "source_reference": "<내부 정책 문서 링크 또는 회의록>",
  "status": "VERIFIED",
  "is_complete": true,
  "axis_weights": {
    "REVENUE_POTENTIAL": "0.1000",
    "MARGIN_AND_FEES": "0.1500",
    "PRICE_COMPETITIVENESS": "0.1000",
    "DEMAND_TREND_STRENGTH": "0.1000",
    "COMPETITION_INTENSITY": "0.0500",
    "SUPPLY_STABILITY": "0.0500",
    "INVENTORY_SHIPPING_RISK": "0.0500",
    "RETURN_CLAIM_RISK": "0.0500",
    "POLICY_PROHIBITED_RISK": "0.1500",
    "BRAND_IP_RISK": "0.1000",
    "DATA_RELIABILITY": "0.0500",
    "FUNDING_LIMIT_IMPACT": "0.0500"
  },
  "min_approve_total_score": "70.0000",
  "min_confidence_for_recommendation": "0.5000",
  "checked_at": "2026-07-30T00:00:00",
  "effective_at": "2026-07-30T00:00:00",
  "expires_at": null
}
```

가중치 예시값은 **자리 표시자일 뿐 실제 채택 값이 아니다** — 반드시
운영자/사업 담당이 검토 후 결정해야 한다. 이 문서는 이 예시를 실제
DB에 삽입하지 않았다.

## 6. supplementary_inputs 자동 수집 계층 설계

12개 축 중 7개(예상 매출, 마진·수수료, 가격 경쟁력, 공급 안정성,
배송 위험, 반품 위험, 브랜드·IP 위험, 자금 한도 영향 — 문서 원본은
8개 항목을 나열했으나 실제 구현은 아래 7개 축에 대응한다)는 현재
운영자가 API 호출 시 수동으로 `supplementary_inputs`를 채워야 한다.
자동화하려면 다음 오케스트레이션 계층이 필요하다(이번 단계에서는
**설계만** 하고 구현하지 않았다 — 아래 "다음 단계" 참고):

| 축 | 잠재적 자동 출처 | 현재 상태 |
|---|---|---|
| REVENUE_POTENTIAL | Coupang `CoupangProfitEstimate.gross_revenue` | 미연동(수동 입력) |
| PRICE_COMPETITIVENESS | Coupang 상품 Payload의 판매가 필드 | 미연동(수동 입력) |
| SUPPLY_STABILITY | 공급처(Supplier) 재고/리드타임 데이터 | 해당 Domain에 아직 구조화된 필드 없음 |
| INVENTORY_SHIPPING_RISK | 배송/재고 Domain 이벤트 | 미연동 |
| RETURN_CLAIM_RISK | 향후 반품(Return) Domain 통계 | Domain 자체가 초기 단계 |
| BRAND_IP_RISK | 상표/지식재산 데이터베이스(외부) | 외부 연동 필요, 이번 단계 범위 밖 |
| FUNDING_LIMIT_IMPACT | Funding `FundingAccount.available_amount` | 읽기는 가능(Console이 이미 조회) — Decision 서비스에 아직 연결 안 됨 |

**실제 데이터가 없는 상태를 명확히 표시한다**: 이 표의 모든 항목은
현재 fixture/수동 입력 상태이며, `DecisionScore.data_sufficient=False`
+ `evidence_text`로 그 사실이 각 평가 결과에 그대로 노출된다(숨기지
않음). 이 연동을 완료했다고 보고하지 않는다.

## 다음 단계 (별도 승인 필요)

1. 사업팀이 실제 axis_weights/임계값을 결정 → `POST /decision-policies`로
   VERIFIED 정책 1건 생성(운영 DB 대상, 별도 승인 후).
2. Coupang `CoupangProfitEstimate`/Funding `FundingAccount`을 자동으로
   `supplementary_inputs`에 채우는 오케스트레이션 서비스 구현.
3. `is_active` 토글(또는 REVOKED 전이) 전용 관리 API 추가 여부 결정.
