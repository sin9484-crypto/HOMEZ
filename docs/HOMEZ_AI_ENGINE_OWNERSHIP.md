# HOMEZ AI 엔진 소유권

- 상태: 채택됨
- 적용일: 2026-08-30
- 적용 범위: HOMEZ AI Commerce OS의 상거래 지능 엔진 16개
- 목적: GPT와 Claude의 동시 작업 충돌을 막고 설계, 구현, 통합, 검증
  책임을 분리한다.

## 기본 원칙

1. GPT는 상거래 지능의 데이터 계약, 검색, 분석, 멀티모달, 추천 및
   평가 구조를 주도한다.
2. Claude는 HOMEZ 본체의 DB, 권한, 회사 격리, 감사로그, 상태기계,
   채널, 매입, 정산, UI, 패키징 및 Live 통합을 책임진다.
3. 수수료, 마진, 세금, 정책 필수값, 결제 승인, 중복 매입 차단 및
   정산 원장은 생성형 AI의 판단으로 확정하지 않는다.
4. 주 개발자와 실제 Runtime Model Provider는 별개다. GPT가 설계한
   엔진도 Anthropic Provider를 사용할 수 있고, Claude가 통합한 엔진도
   OpenAI Provider를 사용할 수 있다.
5. 같은 파일을 GPT와 Claude가 동시에 수정하지 않는다. 공동 엔진은
   계약을 먼저 고정한 뒤 구현과 통합을 순차 수행한다.
6. 사용자 승인 없는 운영 DB Migration, 배포, 실제 결제, 실제 상품
   등록 및 외부 변경 API 호출은 금지한다.

## 책임 배치

| 번호 | 엔진 | 주 개발 | HOMEZ 통합 | 독립 검토 | Runtime 원칙 |
|---:|---|---|---|---|---|
| 1 | 상거래 근거 관리 | Claude | Claude | GPT | 출처와 fingerprint를 가진 결정론적 데이터 계층 |
| 2 | AI 모델 연결 | GPT | Claude | 상호 검토 | 벤더 중립 Provider |
| 3 | 상거래 통합검색 | GPT | Claude | Claude | FTS와 승인된 공식 API Provider |
| 4 | 동일상품 판별 | GPT | Claude | 공동 | 정확 식별자 우선, AI는 후보 제시 |
| 5 | 시장 수요·트렌드 분석 | GPT | Claude | Claude | 시계열·통계 우선, AI는 설명 보조 |
| 6 | 경쟁·가격 분석 | GPT | Claude | Claude | 확인 가격과 추정값 분리 |
| 7 | 상품 기회 평가·추천 | 공동 | Claude | 상호 검토 | 규칙 기준선과 근거 기반 추천 |
| 8 | 수익성·판매가격 분석 | Claude | Claude | GPT | Decimal 고정 계산 |
| 9 | 판매채널 등록·정책 검증 | Claude | Claude | GPT | 공식 채널 계약 기반 fail-closed 검사 |
| 10 | 상품 콘텐츠 생성 | GPT | Claude | 공동 | 확인된 상품 사실만 사용하는 LLM |
| 11 | 상품 이미지 제작 | GPT | Claude | 공동 | 이미지 모델과 결정론적 규격 검사 |
| 12 | 주문·매입 상품 매칭 | 공동 | Claude | 상호 검토 | 동일성 점수와 거래 가드 분리 |
| 13 | 자동 매입 조정 | Claude | Claude | GPT | 상태기계 중심, 결제 전 재검증 |
| 14 | 운영 이상 탐지 | Claude | Claude | GPT | 규칙·통계 우선, AI는 원인 설명 보조 |
| 15 | 정산·손익 이상 탐지 | Claude | Claude | GPT | 원장 대사와 고정 계산 |
| 16 | AI 평가·학습 | GPT | Claude | 공동 | 고정 평가셋과 실제 판매성과 비교 |

## 역할 요약

### GPT 주 개발

엔진 2, 3, 4, 5, 6, 10, 11, 16을 주도한다. 엔진 7과 12에서는 분석
계약, 알고리즘, 신뢰도 및 평가를 담당한다.

### Claude 주 개발

엔진 1, 8, 9, 13, 14, 15를 주도한다. 모든 엔진의 HOMEZ DB, API,
권한, 회사 격리, 감사로그, UI, 패키징 및 Live 통합을 담당한다.

### 공동 엔진

- 엔진 7: GPT가 추천 알고리즘과 평가를, Claude가 정책·수익성·승인 및
  상품등록 연결을 담당한다.
- 엔진 12: GPT가 동일상품·옵션 일치 판별을, Claude가 가격·재고·주소·
  결제·중복방지 가드를 담당한다.

## 코드 변경 경계

GPT의 초기 구현은 별도 worktree와 `feature/commerce-intelligence`
계열 브랜치를 사용한다. 초기 허용 범위는 다음과 같다.

```text
app/domains/commerce_intelligence/
tests/commerce_intelligence/
docs/AI_ENGINE_*.md
```

Claude 검토와 통합 전 GPT가 직접 변경하지 않는 공유 경로는 다음과 같다.

```text
app/web/console.js
app/main.py
app/core/auth.py
app/core/security.py
app/core/token.py
app/database/
app/domains/marketplace_listing/
app/domains/retail_purchase/
app/domains/settlement/
migrations/
installer/
homez.spec
```

예외는 사용자가 해당 파일을 명시적으로 승인하고, 현재 작업자의 변경이
없음을 확인했으며, 변경 whitelist를 먼저 기록한 경우뿐이다.

## 통합과 검토

1. GPT는 계약, 구현, Fake Provider, 집중 테스트 및 평가 결과를 인계한다.
2. Claude는 기존 canonical 구현과 중복 여부를 확인한 뒤 HOMEZ에 통합한다.
3. Claude는 DB, 권한, 회사 격리, 감사로그, UI 및 운영 안전을 검증한다.
4. GPT는 통합 결과의 Evidence 손실, 데이터 누수, 평가 오염, 모델 종속,
   허위 추천 및 완료 과장을 독립 감사한다.
5. 전체 회귀, 패키징 및 Live 검증은 통합 책임자인 Claude가 수행한다.
6. 실제 외부 변경은 사용자의 최종 승인을 받아야 한다.

## 완료 상태

`완료`라는 단독 표현을 사용하지 않는다.

| 상태 | 의미 |
|---|---|
| `DESIGNED` | 입력·출력·근거·금지동작 계약 확정 |
| `FAKE_VERIFIED` | Fake Provider와 집중 테스트 통과 |
| `INTEGRATED` | HOMEZ DB·API·권한·UI 연결 완료 |
| `PACKAGED_VERIFIED` | 공식 설치 패키지 반영과 설치 검증 완료 |
| `LIVE_VERIFIED` | 승인된 실제 Provider와 운영 흐름 검증 완료 |

모든 보고에는 현재 상태와 함께 실 Provider, 공식 설치본, 운영 DB 및 Live
검증 여부를 각각 표시한다.

## 충돌 해결

1. 실행 중 코드와 테스트가 대화나 보고서보다 우선한다.
2. 기존 canonical owner가 있는 기능은 새 도메인에 중복 구현하지 않는다.
3. 금융·정책·권한 안전성이 충돌하면 더 보수적인 fail-closed 계약을
   선택한다.
4. 모델별 성능 주장은 동일 평가셋의 정확도, 오류율, 비용 및 지연시간으로
   검증한다.
5. 주 개발자 단독으로 `RELEASE_READY` 또는 `V7_COMPLETE`를 선언하지
   않는다.

