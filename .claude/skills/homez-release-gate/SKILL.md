---
name: homez-release-gate
description: HOMEZ 버전의 구현 완료, RC 준비, Migration 준비, 운영 배포 가능 여부를 최종 판정할 때 사용한다.
disable-model-invocation: false
---

# HOMEZ Release Gate

## 판정 종류 (범용)

- `*_READY` — Critical/High 없음, 계획된 검증 전부 통과
- `*_READY_WITH_LIMITATIONS` — Critical/High 없음, Low~Medium 잔여 위험이 명시적으로
  기록되어 있고 그것이 지금 진행을 막을 이유가 아님
- `*_BLOCKED` / `*_BLOCKED_BY_CRITICAL_DEFECT` — Critical 또는 미해결 High 존재
- `*_FAILED` — 검증 자체가 실패했거나 완료할 수 없음

## 완료 판정 전 확인 목록

- [ ] 코드가 실제로 존재하고 문법 검사를 통과하는가
- [ ] 관련 테스트가 실제로 실행되어 통과했는가(주장이 아니라 실행 로그 기준)
- [ ] Migration이 있다면 Model과 일치하고 임시 DB에서 검증됐는가
- [ ] 실제 DB 적용 여부와 그 결과가 명확히 기록됐는가
- [ ] 동시성 검증 수준(스레드/프로세스/Staging)이 명확히 구분되어 있는가
- [ ] Whitelist를 벗어난 변경이 없는가
- [ ] 기존 WIP(untracked 파일 등)가 보호됐는가(삭제/reset 없음)
- [ ] rollback 절차가 문서화되어 있는가
- [ ] 잔여 위험이 심각도별로 분류되어 있는가

## V2.3 판정 문자열 (기록된 이력, 참고용)

- `V2_3_RELEASE_CANDIDATE_READY`
- `V2_3_RELEASE_CANDIDATE_READY_WITH_LIMITATIONS`
- `V2_3_FULL_SCHEMA_MIGRATION_DRAFT_READY`
- `V2_3_SCHEMA_AUDIT_PASS_WITH_CONDITIONS` → Medium 2건 해결 →
  `V2_3_SCHEMA_REAUDIT_PASS_WITH_CONDITIONS`
- `V2_3_BACKUP_GATE_PASSED`
- `V2_3_MIGRATION_APPLIED_AND_VERIFIED`
- `V2_3_OPERATIONAL_READY_WITH_LIMITATIONS` (V2.3 종료 검증 Gate)
- `V2_3_BLOCKED_BY_CRITICAL_DEFECT` (미발생)

Critical 결함이 없으면 Low 기술부채만으로 전체 완료 판정을 막지 않는다. 단, Medium이
2건 이상 미해결 상태로 남아있다면 `*_READY_WITH_LIMITATIONS`까지만 판정하고
`*_READY` 단독으로는 판정하지 않는다.
