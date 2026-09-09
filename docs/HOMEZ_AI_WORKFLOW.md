# HOMEZ AI 협업 매뉴얼

## AI 엔진 소유권

상거래 AI 엔진의 주 개발자, HOMEZ 통합 책임자, 독립 검토자 및 코드
변경 경계는 다음 문서를 단일 기준으로 사용한다.

- `docs/HOMEZ_AI_ENGINE_OWNERSHIP.md`
- `docs/HOMEZ_AI_ENGINE_CODE_CONTRACTS.md`

이 문서의 기존 `ChatGPT 설계 → Claude 구현`이라는 일반 흐름보다 위 두
문서의 엔진별 소유 계약이 더 구체적인 규칙으로 우선한다. 특히 GPT 주도
엔진은 별도 worktree에서 격리 구현한 뒤 Claude가 HOMEZ 본체에 통합하며,
금융·정책·결제·정산 값은 생성형 AI가 확정하지 않는다.

## 역할

**ChatGPT** — CTO 역할. Architecture, Phase 순서, 최종 판단(무엇을 다음 버전에
넣을지, 어떤 트레이드오프를 받아들일지)을 담당한다.

**Claude** — 구현, 테스트, Migration 초안 작성, 오류 수정, 완료 증거(테스트 로그,
diff, 검증 결과) 생성을 담당한다.

**Cursor** — 읽기 전용 독립 감사. 보안, 금융 Flow, 구조적 정합성을 별도 시각에서
검증한다. Claude가 스스로 낸 결론을 그대로 신뢰하지 않고 다시 확인하는 역할.

## 기본 순서

```
ChatGPT 설계 → Claude 구현 → Cursor 감사 → ChatGPT 최종 판단
```

## Claude 단독 모드에서

사용자가 특정 세션에서 Claude에게 "질문하지 말고 계속 진행"을 명시적으로 승인한
경우:

- 승인된 Whitelist 범위 안에서 구현과 테스트를 계속 수행한다.
- 실제 DB 추가 변경, git 조작(add/commit/push/branch), 배포는 별도 명시적 승인
  없이 자동 실행하지 않는다.
- 독립 감사(Cursor 또는 별도 세션의 재감사)를 거치기 전에는 "운영 배포 준비 완료"를
  단독으로 최종 선언하지 않는다 — `*_READY_WITH_LIMITATIONS`처럼 조건부 판정을
  사용하고, 최종 확정은 별도 감사/사용자 승인에 맡긴다.
- 테스트하지 않은 것을 통과로, 적용하지 않은 Migration을 완료로 기록하지 않는다.
