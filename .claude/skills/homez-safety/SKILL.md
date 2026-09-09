---
name: homez-safety
description: HOMEZ V2.4 Commerce Safety Layer(SafetyService, Emergency Stop, Automation Mode, Execution Limit)를 구현, 검증, 감사할 때 사용한다.
---

# HOMEZ Safety Layer

## 계약

- `AutomationMode`: RECOMMEND_ONLY(기본값) / OPERATOR_APPROVAL /
  LIMITED_AUTOMATION / DISABLED
- `SafetyDecision`: ALLOW / REQUIRE_APPROVAL / DENY
- `SafetyReason`: EMERGENCY_STOP_ACTIVE / MODE_NOT_ALLOWED /
  FUNDING_LIMIT_EXCEEDED / QUANTITY_LIMIT_EXCEEDED /
  OPERATOR_APPROVAL_REQUIRED / POLICY_BLOCKED / INVALID_REQUEST /
  DUPLICATE_REQUEST

## evaluate() 판단 순서 (고정, 변경 금지)

1. 입력 검증(음수 금지) → 위반 시 DENY(INVALID_REQUEST)
2. **Emergency Stop 검사 — idempotency 재조회보다 반드시 먼저.** 활성이면
   그 무엇보다 우선 DENY(EMERGENCY_STOP_ACTIVE). 과거에 이미 ALLOW로
   처리된 idempotency_key라도 예외 없이 DENY된다.
3. idempotency 재조회 — 이미 처리된 키면 **ALLOW로 반환하지 않고**
   명시적 DENY(DUPLICATE_REQUEST)
4. Automation Mode 판단(DISABLED/RECOMMEND_ONLY → DENY,
   OPERATOR_APPROVAL 미승인 → REQUIRE_APPROVAL)
5. 한도 조회·판단·소비 기록을 하나의 Transaction으로 처리

## 실행 한도 원자성 (감사 대응 핵심)

- `ExecutionUsage`(요청 단위 append-only 감사 로그)와
  `ExecutionPeriodUsage`(scope × KST 날짜별 누적 카운터)를 분리한다.
- 한도 판단은 **단일 조건부 UPDATE**로 원자화한다:
  `UPDATE execution_period_usages SET consumed = consumed + :amt
  WHERE ... AND consumed + :amt <= :cap` 후 rowcount로 성공 여부 확인.
  별도 SELECT 후 INSERT 방식은 TOCTOU 경쟁에 취약해 사용하지 않는다.
- 최초 행 생성은 `INSERT ... ON CONFLICT DO NOTHING`으로 경쟁 안전하게
  처리한다.
- idempotency_key UNIQUE 제약 위반(IntegrityError)은 동시 요청 경쟁으로
  간주해 rollback 후 DUPLICATE_REQUEST 처리한다.
- 일일 기간은 Asia/Seoul(KST, UTC+9 고정 offset — Windows에 tzdata
  패키지가 없어 zoneinfo 대신 사용) 자정 기준으로 "오늘" 날짜를 계산해
  `period_start`로 저장한다.

## Repository 규칙

쓰기 메서드는 `*_no_commit`(flush만)이다. commit/rollback은 항상
SafetyService가 소유한다.

## 검증

`tests/test_automation_safety.py`에 실제 스레드 + 별도 커넥션 기반
동시성 테스트(`test_concurrent_requests_cannot_jointly_exceed_funding_limit`)
포함 — 한도를 동시 요청이 같이 통과할 수 없음을 실측으로 확인.
