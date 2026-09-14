# HOMEZ Identity

- 프로젝트: HOMEZ
- 사업명: EVERY HOMEZ
- HOMEZ는 쇼핑몰이 아닌 AI Commerce OS (무재고 중개·주문대행 기반)
- 기본 언어: 한국어 (모든 응답·보고서·커밋 설명은 한국어)

# Source Of Truth

작업 시작 시 반드시 이 순서로 읽는다.

1. CLAUDE.md
2. .cursor/rules/*.mdc
3. docs/HOMEZ_PROJECT_STATE.md
4. docs/HOMEZ_DECISIONS.md
5. 관련 `.claude/skills/**`
6. git status와 실제 코드·Migration·테스트

대화 내용과 저장소 상태가 충돌하면:
- 실제 코드와 Git 상태를 먼저 확인한다
- 자동으로 맞추지 않는다
- 차이를 그대로 보고한다
- 둘 중 금융 정합성에 더 안전한 쪽을 선택한다

# Domain Boundaries

```
Customer Payment
  ≠ Marketplace Settlement
  ≠ Funding Account
  ≠ Funding Hold
  ≠ Supplier Payment
  ≠ Refund
```

Funding Account는 은행 잔액이 아니라 사업자가 관리하는 사업 운영자금이다.
은행 API를 사용하지 않는다.

# Mandatory Workflow

분석 → 문제점 → 구현 계획 → Whitelist → 구현 → 검증 → 완료 보고 → 프로젝트 상태 문서 갱신

# Safety

다음은 사용자의 별도 명시적 승인 없이 하지 않는다.

- 기존 WIP reset / clean / revert
- Whitelist 밖 파일 수정
- 비밀정보·`.env` 출력 또는 수정
- 운영 DB 접근, 은행 API, 외부 네트워크
- 무단 Migration 적용 (임시 DB 검증은 항상 허용)
- 무단 commit / push / branch / deploy
- 실패 테스트 삭제, assertion 약화
- 금융 오류 자동 보정 (net_amount 불일치 등은 항상 생성 차단)

# Required Completion Report

- 변경 파일 / 신규 파일 생성
- 테스트 결과 (실행하지 못한 테스트는 사유 명시)
- Transaction / rollback 검증
- 멱등성과 동시성 검증
- Whitelist 준수 여부
- 기존 WIP 보호 여부
- 위험 요소
- 다음 단계
- `docs/HOMEZ_PROJECT_STATE.md` 갱신 여부

# Skill Invocation

상황에 따라 관련 Skill을 사용한다.

- HOMEZ 일반 설계·구현·상태 복원: `homez-core`
- V2.3 Settlement/Funding 작업: `homez-v23`
- 금융 코드 검토·감사: `homez-finance-audit`
- DB/Migration 작업: `homez-migration-safety`
- 완료·릴리스 판단: `homez-release-gate`
- 장시간 자율 작업 중 상태 알림: `homez-self-monitor`
- HOMEZ 콘솔을 브라우저로 직접 조작해 실사용 검증·E2E를 진행: `homez-console-e2e`
