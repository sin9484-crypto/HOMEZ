---
name: homez-core
description: HOMEZ 또는 에브리홈즈 프로젝트의 설계, 구현, 분석, 상태 복원 작업에서 사용한다. HOMEZ의 Architecture, Domain 경계, Whitelist, AI 협업 규칙을 적용해야 할 때 자동 호출한다.
---

# HOMEZ Core

## 1. HOMEZ 목표 Flow

```
상품 분석 → 판매 판단 → 공급처 판단 → 주문 자동화
→ 재고 → 배송 → 운영자금 → 정산 → AI Commerce 운영
```

HOMEZ는 무재고 중개·주문대행 기반의 AI Commerce OS다. 단순 쇼핑몰이 아니다.

## 2. Modular Monolith 원칙

V4까지 FastAPI 기반 Modular Monolith를 유지한다. 별도 서비스 분리, 메시지 브로커,
마이크로서비스 전환은 시도하지 않는다.

## 3. 기존 구조 우선

- 기존 Domain 구조, 파일 배치, 네이밍 패턴을 그대로 따른다.
- 불필요한 대규모 리팩터링을 하지 않는다.
- 이미 검증된 패턴(Repository/Service/Router 3계층)을 재사용한다.
- 새 Domain을 만들기 전에 기존 Domain에 재사용 가능한 구현이 있는지 먼저 검색한다.

## 4. Domain 분리

```
Customer Payment ≠ Marketplace Settlement ≠ Funding Account
≠ Funding Hold ≠ Supplier Payment ≠ Refund
```

각 Domain의 상세 책임과 금지된 의존 관계는 `domain-contracts.md` 참고.

## 5. Whitelist

- 사용자 승인 없이 파일을 생성·수정하지 않는다.
- 신규 파일은 반드시 "신규 파일 생성"으로 표시한다.
- Whitelist 밖 변경이 필요하면 즉시 멈추고 사유·대상 파일을 보고한 뒤 추가 승인을 받는다.
- Model, Migration, 공용 설정, 의존성 추가, 파일 삭제, 대규모 리팩터링은 별도 승인 대상이다.

## 6. 상태 복원 절차

작업 시작 시 항상:

1. `CLAUDE.md` 확인
2. `.cursor/rules/*.mdc` 확인
3. `docs/HOMEZ_PROJECT_STATE.md` 확인
4. `docs/HOMEZ_DECISIONS.md` 확인
5. 관련 Skill 호출
6. `git status` 확인
7. 실제 코드·Migration·테스트를 상태 문서와 대조

대화 기록이나 이전 세션 요약을 그대로 신뢰하지 않는다. 항상 실제 저장소 상태로 재확인한다.

## 7. 사용자 결정과 기술 결정 구분

- **사용자/ChatGPT 결정**: Architecture, Phase 순서, 신제품 기준일, 마켓 우선순위 등
  `docs/HOMEZ_DECISIONS.md`에 기록된 사항 — 임의로 변경하지 않는다.
- **기술 결정**: 변수명, 내부 함수 분리, 테스트 케이스 구성 등 — Claude가 최소 변경·
  기존 패턴 기준으로 자율 결정한다.

## 8. 완료 후 상태 문서 갱신

작업이 끝나면 `docs/HOMEZ_PROJECT_STATE.md`를 사실 기준으로만 갱신한다.
계획이나 미실행 항목을 완료로 적지 않는다.

## 9. architecture.md / domain-contracts.md를 읽는 시점

- 버전 로드맵, Phase 순서, AI 연결 방식이 궁금할 때 → `architecture.md`
- 특정 Domain의 책임 범위나 다른 Domain과의 금지된 의존 관계를 확인할 때 → `domain-contracts.md`
