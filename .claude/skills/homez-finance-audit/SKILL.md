---
name: homez-finance-audit
description: HOMEZ의 Funding, Settlement, Supplier Payment, Customer Payment, Refund 코드와 설계를 금융 정합성 관점에서 감사할 때 사용한다.
---

# HOMEZ Finance Audit

## 감사 순서 (고정)

1. Domain 혼합 여부 (Customer Payment/Settlement/Funding/Supplier Payment/Refund가
   서로 직접 침범하지 않는지)
2. 금액 불변식 (`net_amount == gross_amount - fee_amount` 등, 자동 보정 여부)
3. Transaction 경계 (단일 commit, 부분 성공 가능성)
4. rollback (예외 발생 시 실제로 전체가 되돌려지는지)
5. 멱등성 (동일 요청 재호출 시 중복 반영 없는지)
6. 동시성 (경쟁 조건에서 중복 Ledger/이중 반영 가능성)
7. DB UNIQUE 제약 (앱 레벨 검증만으로 끝나지 않고 DB 레벨 차단이 있는지)
8. 상태 전이 (허용된 전이만 가능한지, 조건부 UPDATE + rowcount 검증)
9. Ledger 감사 추적 (모든 자금 변경이 append-only 기록으로 남는지)
10. 권한 (관리자 전용 처리인지)
11. 재호출 (DEPOSITED/REVERSED 등 종단 상태 재호출 시 부작용 없는지)
12. 부분 성공 (한쪽만 반영되고 다른 쪽은 반영되지 않는 상태가 가능한지)
13. 테스트 신뢰성 (mock이 핵심 검증까지 대체하지 않는지, 임시 DB만 사용하는지)
14. Migration 일치 (실제 스키마가 Model과 어긋나지 않는지)

## 심각도

- **Critical** — 실제 자금 손실/이중 반영/데이터 손상 가능
- **High** — 특정 조건에서 정합성이 깨질 수 있음
- **Medium** — 현재는 안전하지만 회귀 가능성이 있거나 검증 커버리지 공백
- **Low** — 코드 품질/문서화/향후 유지보수 이슈

## 규칙

- 근거 없는 추측은 Finding으로 확정하지 않는다. 모든 Critical/High에는 파일 경로와
  코드 라인 근거를 제시한다.
- 재현 불가능한 "느낌"은 Low 이하로 강등하거나 Finding에서 제외한다.
- Finding을 고치기 위해 assertion을 약화하거나 실패 테스트를 삭제하지 않는다.
- 감사 결과는 항상 `[Critical] [High] [Medium] [Low]` 섹션으로 구분해 보고한다.
