# HOMEZ AI 엔진 코드 소유 계약

- 상태: 채택됨
- 상위 정책: `docs/HOMEZ_AI_ENGINE_OWNERSHIP.md`
- 목적: 16개 엔진의 canonical 코드, 신규 코드 위치 및 수정 책임을 고정한다.

## 공통 계약

모든 엔진은 구현 전에 다음 항목을 문서 또는 타입으로 정의한다.

```text
목적
입력
출력
Evidence 출처
누락값 처리
신뢰도 계산
금지 동작
외부 부작용
회사 격리
감사로그
Fake Provider
평가 지표
현재 완료 상태
```

확인되지 않은 값을 0, 빈 문자열, 기본 브랜드, 기본 인증 또는 고신뢰도로
대체하지 않는다. 실제 Provider가 없으면 Fake 또는 Disabled 상태로
명확하게 표시한다.

## 엔진별 계약

| 번호 | 엔진 | 현재 canonical 코드 | 신규 코드 기본 위치 | 코드 소유 계약 |
|---:|---|---|---|---|
| 1 | 상거래 근거 관리 | `app/domains/ai_governance`, 각 도메인의 evidence·fingerprint | `app/domains/commerce_intelligence/evidence/` | Claude가 저장·격리·감사를 소유하고 GPT가 근거 손실을 감사한다. |
| 2 | AI 모델 연결 | `app/domains/decision/evaluator_protocol.py`, 기능별 Provider Protocol | `app/domains/commerce_intelligence/model_provider/` | GPT가 벤더 중립 계약과 Evals를 소유하고 Claude가 Credential·권한·수명주기를 통합한다. |
| 3 | 상거래 통합검색 | `app/domains/search`, `app/domains/source`, `app/domains/trend_discovery` | `app/domains/commerce_intelligence/search/` | GPT가 Query Planner·정규화·Provider 계약을 소유하고 Claude가 라우터·회사 격리·UI를 통합한다. |
| 4 | 동일상품 판별 | `app/domains/retail_purchase`, `app/domains/purchase_task`의 매칭 계약 | `app/domains/commerce_intelligence/identity/` | GPT가 식별·유사도·신뢰도를 소유하고 Claude가 자동거래 허용 임계값과 사용자 확인 흐름을 소유한다. |
| 5 | 시장 수요·트렌드 분석 | `app/domains/trend_discovery`, `app/domains/new_product_discovery` | `app/domains/commerce_intelligence/trend/` | GPT가 시계열 분석과 데이터 한계를 소유하고 Claude가 Provider 상태·캐시·UI를 통합한다. |
| 6 | 경쟁·가격 분석 | `app/domains/price_history`, `app/domains/pricing` | `app/domains/commerce_intelligence/competition/` | GPT가 가격 비교·경쟁 특징값을 소유하고 Claude가 실제 비용·채널 데이터 연결을 소유한다. |
| 7 | 상품 기회 평가·추천 | `app/domains/decision`, `app/domains/product_selection`, `app/domains/product_candidate` | `app/domains/commerce_intelligence/opportunity/` | GPT가 순위·신뢰도·평가를, Claude가 정책·승인·상품등록 상태 전이를 소유한다. |
| 8 | 수익성·판매가격 분석 | `app/domains/pricing`, `app/domains/marketplace_listing/margin_calculator.py` | 기존 canonical 경로 확장 | Claude만 계산식을 변경할 수 있고 GPT는 수식·누락비용·평가 오류를 독립 감사한다. LLM 산출값을 회계값으로 저장하지 않는다. |
| 9 | 판매채널 등록·정책 검증 | `app/domains/marketplace_listing`, `app/domains/channel_policy` | 기존 canonical 경로 확장 | Claude가 공식 계약·Payload·fail-closed 검증을 소유하고 GPT가 공식 문서 대조와 검증 누락을 감사한다. |
| 10 | 상품 콘텐츠 생성 | `app/domains/marketplace_listing/draft_content_provider.py` | `app/domains/commerce_intelligence/content/` | GPT가 사실 기반 생성·모델 Evals를 소유하고 Claude가 채널 정책·승인·UI를 통합한다. 생성물을 자동 확정하지 않는다. |
| 11 | 상품 이미지 제작 | `app/domains/media_asset` | `app/domains/commerce_intelligence/image/` 또는 기존 Provider 확장 | GPT가 멀티모달·생성 품질을 소유하고 Claude가 asset 계보·권리 경고·저장·UI를 통합한다. |
| 12 | 주문·매입 상품 매칭 | `app/domains/retail_purchase`, `app/domains/purchase_task` | `app/domains/commerce_intelligence/purchase_matching/` | GPT가 후보·일치 신뢰도를, Claude가 가격·옵션·수량·주소·결제 차단을 소유한다. |
| 13 | 자동 매입 조정 | `app/domains/retail_purchase`, `app/domains/purchase_task`, `app/domains/orchestration` | 기존 canonical 경로 확장 | Claude가 상태기계·멱등성·결제·예외 큐를 소유한다. GPT는 위험 시나리오와 오탐을 감사한다. |
| 14 | 운영 이상 탐지 | `app/domains/order`, `app/domains/orchestration` | `app/domains/commerce_intelligence/operations/` | Claude가 결정론적 이상 규칙과 조치 상태를 소유하고 GPT가 분류·설명·평가를 보조한다. |
| 15 | 정산·손익 이상 탐지 | `app/domains/settlement`, `app/domains/pricing` | 기존 canonical 경로 확장 | Claude가 원장·대사·Decimal 계산을 소유한다. GPT는 이상 후보만 제시하며 자동 보정할 수 없다. |
| 16 | AI 평가·학습 | `app/domains/ai_governance` | `app/domains/commerce_intelligence/evals/` | GPT가 고정 평가셋·지표·모델 비교를 소유하고 Claude가 운영성과 연결·보존·권한을 통합한다. |

## 공유 코드 변경 절차

다음 경로를 변경하려면 주 개발자가 먼저 인계 문서를 작성하고 Claude가
통합한다.

```text
app/main.py
app/web/
app/database/
app/core/auth.py
app/core/security.py
app/core/token.py
app/domains/marketplace_listing/
app/domains/retail_purchase/
app/domains/pricing/
app/domains/settlement/
migrations/
installer/
```

인계 문서에는 다음을 포함한다.

```text
기준 commit
변경 whitelist
입력·출력 계약
외부 부작용
DB·Migration 영향
보안·회사 격리 영향
Fake Provider 결과
집중 테스트 결과
미확인 항목
통합 시 필요한 변경
```

## 검토 승인 기준

- GPT 검토는 AI 품질, Evidence, 데이터 누수, 모델 종속, 평가 오염,
  환각 및 신뢰도 과장을 다룬다.
- Claude 검토는 HOMEZ canonical 경로, DB, 권한, 회사 격리, 감사로그,
  트랜잭션, 멱등성, UI 및 설치 안전을 다룬다.
- 공동 엔진은 양쪽 검토가 끝나기 전 `INTEGRATED`로 판정하지 않는다.
- 실제 Provider 호출이 없으면 `LIVE_VERIFIED`를 사용할 수 없다.
- 전체 회귀가 실패하면 `PACKAGED_VERIFIED` 또는 `RELEASE_READY`를
  선언할 수 없다.

