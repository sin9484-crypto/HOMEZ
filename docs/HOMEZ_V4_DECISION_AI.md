# HOMEZ V4 Decision AI

# Current Version

Foundation 완료(2026-07-30). V3 Discovery Core(ProductCandidate) 산출물을
결정적(deterministic) 평가기로 채점·추천하되, 최종 상태 전환은 사람이
한다. 진입 전제였던 Desktop 로그인 흐름의 두 가지 Critical/High
차단 결함(Company.users mapper, passlib/bcrypt)도 이번 세션에서 함께
해결했다 — 자세한 내용은 아래 "진입 Gate에서 발견·해결한 선행 결함"
참고.

# 설계 결정 — 5개 테이블로 통합

요청에서 열거한 8개 개념(DecisionPolicy/DecisionEvaluation/
DecisionScore/DecisionEvidence/DecisionRecommendation/DecisionReview/
DecisionOverride/DecisionAuditLog)을 5개 테이블로 통합했다:

- **DecisionPolicy** — 축별 가중치·임계값의 버전 관리 세트. Coupang의
  `CoupangPolicySet`과 동일한 거버넌스(VERIFIED·활성·완전·유효기간
  내여야 "사용 가능") — 사용 가능한 정책이 없으면 평가 자체를 만들지
  않는다(fail-closed).
- **DecisionEvaluation** — 평가 1건. DecisionEvidence/
  DecisionRecommendation을 별도 테이블로 쪼개지 않고 이 테이블의
  필드(추천 결과·사유·입력 스냅샷·지문·총점·신뢰도)로 흡수했다.
- **DecisionScore** — 축마다 1행(append-only). "항목별 점수"와 "근거"를
  분리하지 않고 한 행에 묶었다.
- **DecisionReview** — 사람의 승인·보류·거절 **및** override를 함께
  기록한다(append-only). override는 별도 테이블 대신
  `action="OVERRIDE"` + `previous_value`/`new_value`/`override_reason`
  컬럼으로 표현한다.
- **DecisionAuditLog** — AI 평가 생성과 사람의 결정을 모두 포괄하는
  단일 감사 이벤트 스트림(append-only). Emergency Stop에 의해 평가
  자체가 차단된 경우에도(evaluation_id가 없는 경우) 이 로그에 남는다.

전부 FK 없음(논리 참조 컬럼만). `app/domains/decision/**`는
Funding/Settlement/Order/Purchase Model을 import하지 않는다.

# 평가 축 (12종) 및 데이터 출처

`app/domains/product_candidate`(V3 Discovery Core)의 6개 점수 필드
(trend/novelty/demand/competition/margin/risk_score + confidence)를
1차 입력으로 삼는다. 이 필드만으로 채울 수 없는 축(매출/가격경쟁력/
공급안정성/배송위험/반품위험/브랜드IP위험/자금한도영향)은 운영자가
API 호출 시 제공하는 `supplementary_inputs`로 보강한다 — **제공되지
않으면 임의의 긍정 점수를 만들지 않고 `data_sufficient=False`로
표시**하며, 사용 가능한 축이 12개 중 6개 미만이면 전체 추천을
`INSUFFICIENT_DATA`로 강제한다.

| 축 | 1차 출처 | 보강 필요 시 |
|---|---|---|
| REVENUE_POTENTIAL | — | gross_revenue |
| MARGIN_AND_FEES | candidate.margin_score | expected_margin_rate |
| PRICE_COMPETITIVENESS | — | price_competitiveness_score |
| DEMAND_TREND_STRENGTH | candidate.demand_score/trend_score | — |
| COMPETITION_INTENSITY | candidate.competition_score | — |
| SUPPLY_STABILITY | — | supply_stability_score |
| INVENTORY_SHIPPING_RISK | — | inventory_shipping_risk_score |
| RETURN_CLAIM_RISK | — | return_claim_risk_score |
| POLICY_PROHIBITED_RISK(HARD BLOCK) | candidate.risk_score(반전) | — |
| BRAND_IP_RISK | — | brand_ip_risk_score |
| DATA_RELIABILITY | candidate.confidence | — |
| FUNDING_LIMIT_IMPACT | — | required_funding + available_funding |

**한계**: 위 표의 "보강 필요" 축들은 아직 Coupang(수익성 계산)/Funding
Domain과 자동 연동되어 있지 않다 — 운영자/상위 오케스트레이션 레이어가
`supplementary_inputs`로 직접 채워야 한다. 자동 조회 연동은 다음
단계로 남긴다(아래 "다음 단계" 참고).

# 고정 규칙 구현

- **AI가 상태를 자동 전환하지 않음**: `DecisionService`에는
  `ProductCandidate.status`를 쓰는 코드가 없다(테스트로 강제,
  `test_ai_never_directly_sets_product_candidate_status`). 평가
  자체의 `status`(PENDING_REVIEW/REVIEWED)만 조건부 UPDATE로 바뀐다.
- **정책 위반이 점수로 상쇄되지 않음**: `POLICY_PROHIBITED_RISK` 축이
  임계값(40점) 미만이면 총점이 아무리 높아도 추천이
  `REVIEW_REQUIRED`로 강제 강등된다(`_decide_recommendation`).
- **Emergency Stop 최우선**: 평가 시작 시 가장 먼저 확인한다 —
  `automation_safety` 스키마 자체가 없어도(V2.4/V3 미적용 상태) 안전을
  확인할 수 없다는 이유로 fail-closed 차단한다(Coupang 정책 fail-closed
  와 동일한 철학). 차단 시에도 candidate_id 기준 감사 로그를 남긴다.
- **결정적 평가**: `evaluate_axes()`는 순수 함수 — 무작위성이 없고
  동일 입력은 항상 동일 점수를 반환한다(테스트로 검증, 실제 서비스
  경로 전체를 두 번 호출해 total_score/confidence/recommendation/
  input_fingerprint가 완전히 같음을 확인).
- **멱등성**: `idempotency_key` UNIQUE + IntegrityError 복구(기존
  Coupang/ProductCandidate와 동일 패턴). 동시 요청 실스레드 테스트로
  경쟁을 재현해 정확히 하나만 생성됨을 확인.
- **동시 승인/거절**: 조건부 UPDATE(PENDING_REVIEW→REVIEWED) + rowcount
  검증 — 실스레드 테스트로 정확히 하나만 성공함을 확인.
- **Decimal만 사용**: 모든 점수/가중치/총점 컬럼이 `Numeric`이며, AST
  검사로 `service.py`에 float 리터럴이 없음을 강제. NaN/Infinity는
  Pydantic 스키마 계층에서 이미 `finite_number` 검증으로 차단되고,
  나머지 숫자 변환도 `_q_score()`가 `is_finite()`를 재확인한다.
- **override 감사 완전성**: `DecisionReview.action="OVERRIDE"` 행에
  reviewer_id/previous_value/new_value/override_reason/decided_at이
  전부 기록되고, `DecisionAuditLog`에도 OVERRIDE_APPLIED 이벤트가
  남는다(테스트로 완전성 확인).

# AI 경계

`evaluator_kind="deterministic"`, `evaluator_version="1.0.0"`를 모든
평가 행에 고정 기록한다 — 실제 LLM/외부 AI 연동이 준비되지 않은
상태이므로, 이 필드가 **가짜 AI를 실제 AI로 보고하지 않기 위한
명시적 표식**이다. 외부 네트워크·유료 AI API 호출은 없다(패키지
설치도 없음).

# 진입 Gate에서 발견·해결한 선행 결함 (2026-07-30)

V4 진입 전 "Desktop 로그인 흐름의 현재 상태" 점검 중, Desktop 로그인
작업과 공유되는 더 근본적인 Critical/High 결함을 발견해 최소 범위로
해결했다(둘 다 로그인 자체를 막고 있어 V4의 "로그인 전에는 접근할 수
없어야 한다" 요구사항도 동시에 막고 있었다):

1. **`app/domains/user/model.py`가 실제 homez.db `users` 테이블
   스키마와 전혀 다름** — 모델은 `password_hash`/`is_active`/
   `nickname`/`login_count` 등을 선언했지만 실제 컬럼은 `password`/
   `active`/`company_id`/`role_id`뿐이었다. 단순 SELECT조차
   `OperationalError: no such column`으로 실패했다. → 실제 DB 컬럼에
   맞게 모델을 재작성(Python 속성명은 `mapped_column("password", ...)`
   같은 명시적 컬럼명 매핑으로 유지해 기존 호출부 변경을 최소화).
2. **`Company.users` 등 SQLAlchemy mapper 오류** — `Company`/`Role`/
   `RolePermission`/`Permission` 사이에 실제 FK가 없거나(Company의
   categories/brands/suppliers/products 관계 제거) 순환 import로
   `configure_mappers()`가 실패해(전체 SQLAlchemy 레지스트리가
   process-wide로 오염됨) 인증이 걸린 모든 API가 500을 반환했다. →
   `app/domains/permission/policy.py`의 지역 import, `app/domains/
   role/model.py`/`app/domains/role_permission/model.py`의 파일 하단
   import로 순환을 해소.
3. **`passlib` 1.7.4가 설치된 `bcrypt` 5.0.0과 비호환** —
   `hash_password`/`verify_password` 호출 자체가 항상 실패했다(자체
   진단 루틴에서 ValueError). → `app/core/security.py`가 `bcrypt`를
   직접 호출하도록 변경(새 패키지 설치·버전 변경 없음).
4. **`has_role()`이 대소문자를 구분** — 실제 시딩된 역할 코드(예:
   "ADMIN")는 대문자인데 `UserRole` enum 값은 소문자("admin")라 실제
   계정으로는 admin_guard를 절대 통과할 수 없었다. →
   `app/core/authorization.py::has_role()`을 대소문자 무시 비교로 수정.

이 네 가지를 모두 고친 뒤 임시 DB 복제본에 실제 계정을 만들어
`AuthService.login()`으로 성공/실패/비활성 계정 케이스를 전부 실제로
호출해 검증했다(`tests/test_homez_auth_login.py`).

# API

전부 `admin_guard` 필요(로그인 전 접근 불가):
`POST /decisions/evaluate`, `GET /decisions`, `GET /decisions/
pending-review`, `GET /decisions/{id}`, `GET /decisions/{id}/scores`,
`GET /decisions/{id}/reviews`, `POST /decisions/{id}/{approve|hold|
reject|override}`, `GET /decisions/candidates/{candidate_id}/
audit-log`, `POST|GET /decision-policies`.

자동 실행 엔드포인트는 없다 — 상품 등록/가격 변경/발주/광고/자금
이동/정산 처리 API는 이 Domain에 존재하지 않는다.

# Migration

`migrations/20260730_00_create_decision_ai_schema.sql` — 5개 테이블,
Model↔Migration DDL 자동 드리프트 테스트 포함 12개 테스트 전부 통과.
**임시 DB에서만 검증, 실제 homez.db에는 미적용.**

# 다음 단계 (이번 단계 범위 밖, 별도 승인 필요)

1. Coupang `CoupangProfitEstimate`/Funding `FundingAccount`을
   `supplementary_inputs`에 자동으로 채워주는 오케스트레이션 레이어
   (현재는 운영자/호출자가 직접 값을 제공해야 함).
2. `DecisionPolicy` 실제 가중치/임계값 데이터 시딩(현재 fail-closed —
   VERIFIED 정책이 없으면 평가 자체가 차단됨, 의도된 동작).
3. 실제 LLM 기반 evaluator 연동(현재는 `evaluator_kind="deterministic"`
   fixture 평가기만 존재).
4. Desktop Console UI에 평가 대기 목록/상세/승인·override 화면 연결
   (이번 단계는 서버 API까지만 — 이 세션에서 Desktop UI 자체는 구현하지
   않았다, 아래 "미실행 작업" 참고).

# Last Updated

2026-07-30

# Updated By

Claude
