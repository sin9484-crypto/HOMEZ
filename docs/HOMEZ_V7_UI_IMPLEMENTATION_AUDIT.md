# HOMEZ V7 UI 개선 작업 — 구현 감사 (신규 문서, 2026-09-10 시작)

이 문서는 `C:\Users\Daum pc\Desktop\HOMEZ V7 UI 시안`의 8개 화면
시안을 참고해 HOMEZ 실제 UI를 개선하는 작업의 감사 기록이다.
append-only — 기존 확정 기록은 삭제하지 않고, 판단이 바뀌면 정정
이력을 추가한다.

**원칙 재확인(작업 지시서 3절)**: 시안은 완성된 요구사항이 아니라
참고 자료다. "시안과 닮은 HOMEZ"가 목표가 아니라 "현재 HOMEZ 기능을
더 빠르고 정확하고 안전하게 다루는 UI"가 목표다.

## 0. 기준선 (Phase UI-0)

- HEAD: `34a9060bd436ba564f6205134b553b3150d29873`(보존 대상, 이전
  GPT 세션이 만든 중간 보존 커밋 — 삭제·수정·되돌리지 않음)
- `origin/main`: `0cfe239824ee0fb8a92067ef46ccf4e8c84b1b72`(세션
  시작부터 지금까지 미변경, 충돌 없음)
- 미커밋 변경(`git status --porcelain=v1 -uall` 기준):
  총 114개(수정 58 + 신규 56) — app 74, tests 30, migrations 8,
  docs 2. (이전 잘못된 보고 "58 수정 + 38 신규"를 이 수치로 정정 —
  자세한 경위는 `docs/HOMEZ_PROJECT_STATE.md`의 "정정 — git
  미커밋 변경 수치" 절 참고.)
- Phase 0~14(로그인 잠금/기능별 자동화/결제·환불·환율·공급처능력
  Domain/가격재고안전/민감정보권한/AI학습기반 등)는 그대로 유지 —
  이번 UI 작업으로 그 구현을 되돌리거나 수정하지 않는다.
- 현재 UI 코드 규모: `app/web/console.html`(2,517줄),
  `app/web/console.js`(19,558줄, SPA 방식 — 모든 화면이 DOM에
  공존하고 `display:none`으로 전환), `app/web/i18n/{ko-KR,en-US}.js`
  (각 2,850여 줄), `app/web/console.css`.
- 실제 사이드바 메뉴는 27개 항목(대시보드/AI업무제안/운영우선순위/
  주문예외검토/정산차이검토/구매실행연결/매입발주관리/상품후보/
  AI상품등록/상품등록/상품목록/등록현황/판매채널연동/채널별판매방식/
  주문현황/공급처발주/배송/반품교환/DecisionAI/트렌드탐색/신제품탐색/
  마진수익분석/자금정산/채널정산/자동화안전/사용자권한/시스템상태)
  +가이드 — 시안의 단순화된 8~9개 메뉴보다 훨씬 세분화돼 있다.

## 1. 전체 내비게이션 구조 판단 — 제외(시안의 단순화된 메뉴 구조는 그대로 적용하지 않는다)

**분류: 제외**

시안 8장은 모두 "상품발굴/상품등록/이미지제작/주문매입/매입설정/
배송/취소반품/정산" 8~9개 메뉴로 단순화된 사이드바를 보여준다.
실제 HOMEZ는 27개 메뉴로 세분화돼 있고, 그중 다수(AI 업무 제안,
운영 우선순위, 정산 차이 검토, 자동화 안전, 사용자·권한, 시스템
상태 등)는 시안에 아예 대응하는 화면이 없다 — 시안이 그 기능들을
다루지 않을 뿐 실제로는 존재하고 매일 쓰이는 기능이다.

이유(작업 지시서 6/10/11절 기준 판단):
- 시안대로 8~9개 메뉴로 통합하면 시안에 없는 기능(운영 우선순위,
  자동화 안전, 사용자·권한 등)의 접근성이 오히려 나빠진다 — "작업
  단계가 줄어드는가?"(10절)에 반한다.
- "트렌드 탐색/신제품 탐색/Decision AI/마진·수익 분석"을 시안의
  "상품 발굴·분석" 화면 하나로 합치는 것은 서로 다른 백엔드 도메인
  (Trend AI fixture 기반, Decision AI deterministic 규칙 엔진, 마진
  계산기)을 하나의 화면 계약으로 강제로 묶는 대규모 구조 변경이라
  "유지보수 복잡도만 증가"(4절 제외 기준)에 해당하고, 이번 UI
  개선 작업의 범위(기존 기능을 더 잘 다루게 하는 것)를 넘어선다.
- 11절 금지 사항 "시각적 유사성을 업무 흐름보다 우선"에 정면으로
  해당하는 사례다.

**대신 적용하는 것**: 메뉴 구조는 그대로 두고, 각 화면 *내부*의
정보 표시·상태 라벨·진행 흐름 시각화를 시안에서 선별적으로
가져온다(아래 화면별 절 참고). 시안이 특정 화면들(주문·매입/배송/
취소·반품)에서 공통으로 쓰는 "단계별 진행 표시(timeline)"와 "한국어
상태 라벨"은 여러 화면에 걸쳐 재사용 가능한 개선이라 판단해 공통
컴포넌트로 우선 구현한다.

## 2. 화면별 감사

### UI-7. 배송 관리 (시안 `06 배송 관리.png`) — 완료

**참고한 실제 코드**: `app/web/console.js`의 `shipRenderList()`/
`shipShowDetail()`(배송 목록·상세, `/shipments` API), `app/domains/
shipment/schema.py`(`ShipmentResponse` — 수령인 이름·전화번호·주소
필드 자체가 없음, order 도메인에만 있고 그쪽은 `mask_phone`/
`mask_address`로 이미 마스킹됨).

**발견한 기존 결함**:
1. 배송 상태가 `PENDING`/`READY`/`SHIPPED`/`IN_TRANSIT`/`DELIVERED`/
   `RETURN_REQUESTED` 등 영문 코드 그대로 화면에 노출됐다
   (`statusPillHtml()`이 상태 문자열을 번역 없이 그대로 표시 —
   공통 함수라 다른 여러 화면도 같은 구조를 공유한다). 작업
   지시서 7절 "상태·위험·다음 행동을 짧고 정확한 한국어로 표시한다"
   위반.
2. 배송 진행 상황이 평면적인 "상태 이력" 표(이전상태→새상태→사유→
   일시) 테이블 하나뿐이라, 지금 어느 단계인지 한눈에 파악하기
   어려웠다(시안이 이 지점을 잘 짚었다 — 단계별 시각화 부재).

**적용한 시안 요소**:
- **그대로 적용**: 단계별 진행 표시(step timeline) 개념 —
  접수→출고 준비→출고 완료→배송 중→배송 완료의 정상 경로를
  체크마크·현재 위치 강조로 시각화.
- **수정해서 적용**: 시안은 배송 목록·상세에 수령인 이름/전화번호를
  표시하고(부분 마스킹), 상세에는 주소까지 원문으로 노출한다.
  HOMEZ 실제 Shipment 도메인은 그 필드 자체를 갖지 않는다(의도적
  설계 — order 도메인만 PII를 다루고 항상 마스킹). **PII 미노출
  상태를 그대로 유지**하고, 시안의 UI 패턴(단계 표시·한국어 라벨)만
  차용했다 — 시안에 있다고 해서 없던 PII 노출 경로를 새로 만들지
  않는다(작업 지시서 11절 "권한·개인정보가 안전하게 유지되는가?").
  상품 썸네일·주문 수령인 요약을 배송 목록에 추가하는 것은 이번
  구현 범위에 포함하지 않았다(아래 "보류" 참고).
- **분기 상태 왜곡 방지**: 시안에 없는 케이스지만, 실제 배송은
  정상 경로를 벗어나 취소·반품·교환으로 분기할 수 있다. 현재 상태가
  정상 경로 밖이면 정상 경로로 억지로 끼워 맞추지 않고, 지나온
  단계까지만 완료로 표시한 뒤 실제 상태를 별도(경고색) 마지막
  단계로 덧붙인다 — "실제 상태와 화면 표시가 정확히 일치하는가?"
  (10절)를 만족시키기 위한 설계 판단.

**적용하지 않은 요소와 이유(보류)**:
- 배송 목록에 상품 이미지·이름 표시 — `ShipmentResponse`가 품목
  정보를 요약해 주지 않아(현재는 `order_item_id` 숫자만) 목록
  화면에서 N+1 조회 없이 보여주려면 백엔드 응답 스키마 확장이
  필요하다. Model/Schema 확장은 이번 프론트엔드 전용 작업 범위를
  넘어서므로 **보류** — 후속 작업으로 남긴다.
- 배송 상태 필터 드롭다운(`<select>`)의 옵션 라벨은 여전히 영문
  코드 그대로다(pill 라벨만 번역, select option은 별도 변경 필요) —
  이번 턴 범위를 좁게 유지하기 위해 **보류**, 다음 화면 작업과
  함께 일괄 정리 예정.

**구현 내용(변경 파일)**:
- `app/web/console.js` — `statusPillHtmlLabeled()`(번역 실패 시
  원문 fallback, 기존 `statusPillHtml()`은 다른 화면 회귀 방지를
  위해 그대로 유지), `statusTimelineHtml()`, `buildShipmentTimelineSteps()`
  추가. 배송 목록·상세의 상태 표시를 라벨 버전으로 교체, 상세에
  "배송 진행 현황" 섹션 신규 추가(기존 "상태 이력" 테이블은 그대로
  유지 — 상세 감사 이력을 잃지 않는다).
- `app/web/console.css` — `.status-timeline*` 클래스 신규(done/
  current/pending/branch 4개 상태, 기존 디자인 토큰 `--teal`/
  `--warning`/`--border` 재사용).
- `app/web/i18n/ko-KR.js`, `en-US.js` — `ship.timeline_heading`,
  `ship.status.*`(10개 배송 상태 코드) 키 추가.
- `tests/test_i18n.py` — `ShipmentTimelineTranslationTestCase`
  신규(동적 템플릿 리터럴 키라 기존 정적 스캔 테스트가 못 잡는
  누락을 별도로 고정 — `PrecheckContractMessageTestCase`와 동일한
  선례를 따름).

**실제 연결된 API**: `/shipments`, `/shipments/{id}`,
`/shipments/{id}/items`, `/shipments/{id}/status-events`(전부 기존
Gate 4 API, 신규 API 없음 — 이번 작업은 순수 프론트엔드).

**Fake 또는 미구현 기능**: 없음(이번 변경 자체에는 Fake Provider나
미구현 기능이 관여하지 않는다).

**테스트 결과**:
- `tests/test_i18n.py` 27/27 OK(신규 3개 테스트 포함, node 문법
  검사 포함).
- 격리 DB(`ui_shipment_verify.db`, 실 homez.db 아님) + 실제 서버
  기동 + 브라우저 클릭 검증: 정상 경로 5단계(PENDING→READY→
  SHIPPED→IN_TRANSIT→DELIVERED) 각각의 done/current/pending 색상,
  분기 상태(RETURN_REQUESTED) 별도 표시, 한국어 라벨 6종 전부
  확인.
- 데스크톱(1440×900)·태블릿(1280×720)·모바일(390×844) 3개
  뷰포트 확인 — **모바일에서 실제 결함 발견**: `.status-timeline`의
  단계 연결선(`::after`)이 flex-wrap으로 줄바꿈될 때 다음 형제가
  다음 줄로 넘어간 경우 컨테이너 우측 밖으로 삐져나가
  `document.documentElement.scrollWidth`가 390을 넘는 실제 가로
  스크롤을 만들었다(재현 확인: 404 vs 390). `overflow-x: hidden`을
  `.status-timeline`에 추가해 수정, 재검증으로 390=390 확인(점·
  라벨은 각 단계 자체 박스 안에 있어 잘리지 않음도 확인).
- 브라우저 콘솔 오류 없음(`read_console_messages` 확인).

**Whitelist 준수**: `app/web/console.js`, `console.css`,
`i18n/ko-KR.js`, `i18n/en-US.js`, `tests/test_i18n.py`,
`.claude/launch.json`(검증용 서버 설정 추가) — Model/Migration/
공용 설정 변경 없음.

**기존 WIP 보호**: 기존 배송 화면의 다른 기능(반품/교환 접수, 상태
변경, 상태 이력 표)은 전혀 수정하지 않고 그대로 유지.

**위험 요소**: 낮음 — 프론트엔드 표시 전용 변경, 신규 함수는
기존 `statusPillHtml()`을 대체하지 않고 옵트인 방식으로 추가해
다른 화면 회귀 위험 없음.

**다음 화면**: UI-2(상품 발굴 및 분석) 또는 UI-8(취소·반품 관리 —
배송과 같은 "단계 표시" 패턴을 바로 재사용할 수 있어 효율적).

**정정(UI-8 작업 중 발견, 같은 컴포넌트를 공유하므로 여기도 함께
수정)**: 정상 경로의 *마지막* 단계(DELIVERED)에 실제로 도달했을 때
"current"(진행 중, 속이 빈 링)로 표시되던 것을 "done"(체크)으로
수정했다 — 이미 끝난 배송을 "아직 진행 중"처럼 보이게 하는 것은
"실제 상태와 화면 표시가 정확히 일치하는가?"(10절) 원칙 위반이라
판단. `buildShipmentTimelineSteps()`의 상태 계산 로직만 수정,
데이터 구조·API 호출은 변경 없음.

### UI-8. 취소 및 반품 관리 (시안 `07 취소 및 반품 관리.png`) — 부분 완료(기존 화면 개선 완료, 환불 UI는 후속)

**참고한 실제 코드**: `app/web/console.js`의 `retRenderList()`/
`retShowDetail()`(반품·교환, `/return-orders` API),
`app/domains/return_order/schema.py`(`ReturnOrderResponse` —
requested_at/approved_at/received_at/completed_at을 이미 자체
필드로 갖고 있어 shipment처럼 이벤트에서 되짚을 필요가 없음).

**발견한 기존 결함**:
1. UI-7과 동일한 결함 — 반품 상태(`REQUESTED`/`APPROVED`/
   `RECEIVED`/`COMPLETED`/`REJECTED`)가 영문 코드 그대로 노출.
2. 처리 진행 상황도 평면적인 상태 이력 표만 있어 단계 파악이
   어려움(UI-7과 동일).
3. **중요한 구조적 발견**: 시안 07은 "취소 요청/반품 접수/회수 중/
   **환불 확인**" 4개 탭을 보여주는데, 실제 HOMEZ에는 물류 처리
   (`ReturnOrder` — 회수·검수)와 금전 처리(`Refund` Domain, 이번
   세션 Phase 8에서 신규 구현)가 완전히 분리된 별개 리소스로
   존재한다. **`Refund` Domain은 백엔드 API·테스트(`tests/
   test_refund_domain.py` 24개, `tests/test_refund_router_guard.py`
   3개)가 전부 갖춰져 있는데도 `app/web/console.js`에 대응하는
   화면이 단 한 줄도 없다**(`grep "refund" console.js` 결과 0건) —
   즉 운영자가 실제로 승인해야 하는 환불을 지금은 콘솔에서 전혀
   처리할 방법이 없다. 이는 "6. 기존 결함 우선 원칙"이 찾으려는
   종류의 결함(구현됐지만 화면에서 쓸 수 없음)에 정확히 해당한다.

**적용한 시안 요소**:
- **그대로 적용**: UI-7과 동일한 단계 표시(timeline) + 한국어 상태
  라벨을 기존 반품·교환 화면에 적용. `ReturnOrder`가 이미 단계별
  타임스탬프를 갖고 있어 shipment보다 더 정확한 시각을 보여줄 수
  있었다(이벤트 추정 불필요).
- **분기 상태 처리**: REJECTED는 정상 경로(REQUESTED→APPROVED→
  RECEIVED→COMPLETED) 밖 분기로 별도 표시 — REQUESTED만 지난 뒤
  거절된 경우 APPROVED/RECEIVED/COMPLETED를 전부 pending(미도달)
  그대로 두고 거절 단계만 경고색으로 덧붙인다(승인 이력이 없는데
  "지나온 것처럼" 보이지 않도록).

**적용하지 않은 요소 — 보류**:
- 환불 **신규 등록(생성)** 화면 — `RefundCreateRequest`(order_id/
  return_order_id/refund_type/amount/reason)를 채우는 폼은 만들지
  않았다. 반품·취소 처리 흐름에서 정확히 어느 시점에 어떤 조건으로
  Refund가 자동/수동 생성돼야 하는지는 이 UI 작업의 범위를 넘어서는
  별도 업무 설계가 필요하다고 판단(예: return-order COMPLETED 시
  자동 생성? 수동 생성 버튼? 금액을 누가 계산?) — 추측으로 만들지
  않는다. 이번에는 **이미 생성된 환불 요청을 승인/거부/실행확인하는
  화면**만 구축했다(아래 "그대로 적용" 참고) — Refund 도메인
  자체는 이미 있는데 화면이 전혀 없던 상태를 우선 해소하는 것이
  더 시급하고 명확한 범위라 판단했다.

**추가로 그대로 구축한 것 — Refund Domain UI 신규**:
백엔드는 이미 완성·검증됐고(`FakeRefundExecutor`만 존재 — 실제
환불 실행 없음, `approve_refund()`는 항상 `is_admin=True`+최근 비밀번호
재확인 필요), 새 기능이 아니라 "이미 있는데 못 쓰는 기능을 쓸 수
있게 만드는" 것이므로 원칙적으로 **적용 대상**이라 판단해 이번
턴에서 함께 완료했다 — 목록(상태 필터)+상세(단계 표시)+승인/거부/
실행확인 액션, 전부 기존 화면과 동일한 검증 수준(i18n 동적 키
고정 테스트, 격리 DB, 3개 뷰포트, 실제 API 왕복)으로 확인.

**구현 내용(변경 파일)**:
- `app/web/console.js` — `buildReturnOrderTimelineSteps()` 신규
  (ReturnOrder 전용, 자체 타임스탬프 필드 사용). 목록·상세 상태
  표시를 `statusPillHtmlLabeled()`로 교체, 상세에 "처리 진행 현황"
  섹션 추가(기존 "상태 이력" 표는 유지).
  `buildShipmentTimelineSteps()`의 마지막 단계 상태 계산 수정(위
  정정 항목).
  **Refund 화면 신규**: `rfRenderList()`/`rfShowDetail()`(목록+
  상태 필터+상세), `buildRefundTimelineSteps()`(AWAITING_APPROVAL→
  APPROVED→EXECUTED 정상 경로 + REJECTED 분기), 승인(`promptRecentAuthToken()`
  재사용 — 비밀번호 재확인)/거부(`confirmDialog({requireReason:true})`
  재사용)/실행확인 액션, `VIEW_LOADERS["refund"]` 등록.
- `app/web/console.html` — "주문 관리" 그룹에 "환불 관리" nav 버튼
  + `#view-refund` 섹션 신규(반품·교환 섹션과 동일한 구조).
- `app/web/i18n/ko-KR.js`, `en-US.js` — `ret.timeline_heading`,
  `ret.status.*`(5개), `refund.*`(약 35개 — 제목/설명/Fake 고지/
  컬럼/상태 4종/구분 2종/액션/성공메시지) 키 추가.
- `tests/test_i18n.py` — `ReturnOrderTimelineTranslationTestCase`,
  `RefundUiTranslationTestCase` 신규(동적 키 고정 패턴, Fake 고지
  문구가 실제로 존재하는지까지 별도 검증).

**실제 연결된 API**: `/return-orders`, `/return-orders/{id}`,
`/return-orders/{id}/status-events`, 그리고 신규로 화면과 연결된
`/refunds`(목록), `/refunds/{id}/approve`, `/refunds/{id}/reject`,
`/refunds/{id}/mark-executed`(전부 기존 Phase 8 API, 신규 API
없음 — 이번 작업은 순수 프론트엔드). `/refunds/{id}` 단건 조회
엔드포인트가 백엔드에 없어(설계상 목록만 제공) 상세 화면은 방금
불러온 목록 캐시에서 찾는다 — 승인/거부/실행확인 후 항상 목록을
다시 불러와 캐시가 오래된 채로 남지 않게 했다.

**Fake 또는 미구현 기능**: Refund의 승인·실행확인은
`FakeRefundExecutor`만 호출한다(실제 결제망 호출 없음) — 화면
상단에 상시 고지 문구를 두고 감췄다.

**테스트 결과**:
- `tests/test_i18n.py` 33/33 OK(UI-7 3개 + UI-8 2개 + Refund 4개,
  node 문법 검사 포함). `tests/test_route_authentication_contract.py`
  3/3 OK(신규 화면이지만 신규 백엔드 라우트는 없어 영향 없음
  재확인).
- 격리 DB(ReturnOrder 4건 + Refund 4건 — AWAITING_APPROVAL/
  APPROVED/EXECUTED/REJECTED) + 브라우저 검증:
  - ReturnOrder: REJECTED 케이스에서 미도달 단계가 pending으로
    정확히 남고 거절 단계만 분기 표시 확인, COMPLETED 케이스에서
    4단계 전부 done 확인(이 케이스에서 "마지막 단계" 버그 발견).
  - Refund: **실제 API 왕복까지 전부 실행** — 승인 버튼 클릭 →
    비밀번호 재확인 모달 → 비밀번호 입력 → "환불이 승인되었습니다"
    토스트 + 목록 상태 실시간 갱신(승인 대기→승인됨) 확인. 이어서
    실행확인 버튼 → "환불 실행이 기록되었습니다(Fake Provider —
    실제 이체 아님)" 토스트 + 상태 갱신(승인됨→처리 완료) 확인.
    거부(REJECTED) 흐름은 별도 클릭 검증 대신 코드 검토로 확인
    (return-order의 이미 검증된 동일 `confirmDialog` 패턴을 그대로
    재사용했기 때문 — 새로운 로직 없음).
- 모바일(390×844)·태블릿(1280×720) 가로 스크롤 없음 확인.
- 브라우저 콘솔 오류 없음(재시작 과정의 일시적
  `ERR_CONNECTION_REFUSED`는 최근 네트워크 요청 로그 전부 200 OK로
  현재 상태와 무관함을 확인 — `POST /refunds/1/mark-executed → 200
  OK`가 그 로그에 실제로 남아있어 액션 자체도 재확인됨).

**Whitelist 준수**: `app/web/console.js`, `console.html`,
`i18n/*.js`, `tests/test_i18n.py` — Model/Migration/공용 설정
변경 없음(Refund Model/Migration은 이미 Phase 8에서 존재, 이번
작업은 그것을 그대로 사용).

**기존 WIP 보호**: 반품·교환의 승인/입고/완료/거절 액션 로직,
Refund 백엔드 서비스 로직은 전혀 수정하지 않았다.

**위험 요소**: 낮음 — 신규 화면이지만 기존 도메인(shipment/
return-order)과 동일한 검증된 패턴(`promptRecentAuthToken`,
`confirmDialog`, `statusTimelineHtml`, `statusPillHtmlLabeled`)만
재사용했고 새로운 프론트엔드 로직 패턴을 만들지 않았다. 다만
Refund 승인은 실제 돈이 관련된 고위험 작업이라(현재는 Fake라 실제
피해는 없지만) 향후 실제 Provider가 연결되면 이 화면의 승인 흐름이
그대로 실제 자금 이동의 유일한 진입점이 된다는 점을 기록해 둔다.

**다음 화면**: UI-2(상품 발굴 및 분석) 또는 UI-5(주문 및 매입
관리, 시안 04) — Refund 신규 등록(생성) 화면은 위 "보류" 항목으로
별도 필요 시 착수.

### UI-2/UI-3. 상품 발굴 및 분석 / 상품 검토 (시안 `01`, `02` 부분) — 부분 완료

**중요한 사전 판단**: 시안 01(상품 발굴·분석)과 02(상품 검토·등록)는
서로 다른 화면이지만, 실제 HOMEZ는 이 둘을 깔끔하게 분리하지 않는다
— `candidates`(상품 후보) 화면 하나가 검색·필터·정렬·상세·운영자
결정(승인/보류/거절)까지 두 시안의 역할을 상당 부분 함께 수행하고,
`trend`(트렌드 탐색)·`new-product`(신제품 탐색)는 같은
`ProductCandidate` 데이터를 다른 정렬 기준으로 보여주는 보조
화면이다. 화면을 새로 쪼개거나 합치지 않고, 이 기존 3개 화면이
공유하는 표시 방식을 개선하는 데 집중했다.

**발견한 기존 결함(실제 버그)**: `candidates` 목록 테이블의 컬럼
헤더가 "필요 운영자금"(`candidates.col_funding_needed`)인데 실제
셀에는 `demand_score`(수요 AI 점수, 0~1)가 표시되고 있었다 —
`ProductCandidateResponse`에는애초에 "필요 자금" 개념의 필드 자체가
없다(스키마 확인). 버튼 이름과 실제 동작이 다른 것과 동일한 class의
결함(작업 지시서 6절). 데이터를 지어내지 않고, 실제로 표시되는
값(`demand_score`)에 맞게 라벨과 키 이름을 `candidates.col_demand`
("수요")로 바로잡았다.

**적용한 시안 요소**:
- **수정해서 적용**: 시안 01의 트렌드 화살표·색상 강조("상승↗"
  녹색)에서 착안해, `trend_score`/`margin_score`/`demand_score`/
  `novelty_score`/`confidence`/`risk_score`(전부 0~1 float) 6종
  AI 점수를 막대 시각화(`score-bar`)로 바꿨다. 시안처럼 %로
  재해석하지는 않았다 — 이 점수들이 실제 비율/확률을 의미한다는
  근거가 스키마·서비스 코드 어디에도 없어, %로 표기하면 실제보다
  더 정밀한 통계처럼 보이는 위험이 있다고 판단(정직성 원칙 우선).
  원래 소수점 값은 그대로 병기한다.
  `trend_score`/`margin_score`/`demand_score`/`novelty_score`/
  `confidence`는 "높을수록 좋음"(초록↔빨강), `risk_score`는
  "높을수록 나쁨"(기존 `riskClass()`와 동일 방향)으로 색상 의미를
  구분했다 — 방향을 반대로 칠하면 위험을 숨기는 결과가 되므로
  중요한 구분.
- **그대로 적용**: 상세 화면에 누락돼 있던 `demand_score`("수요")
  행을 AI 판단과 점수 섹션에 추가했다(데이터는 이미 API 응답에
  있었지만 화면 어디에도 노출되지 않고 있었다).

**적용하지 않은 요소 — 보류(데이터·계약 부재)**:
- 시안의 실시간 네이버 데이터랩/이미지 검색/URL 상품 분석/온채널
  검색 UI — 이런 라이브 검색 연동 자체가 현재 코드베이스에 없다
  (기존 `candidate_key`/`source_reference` 기반 정적 후보 목록만
  존재). 새 외부 API 연동은 이번 프론트엔드 개선 범위를 완전히
  넘어선다.
- 상품 이미지/썸네일 — `ProductCandidate` Model에 이미지 필드
  자체가 없다(Model 변경은 별도 승인 대상).
- 13주 관심도 추이 꺾은선 그래프 — `trend_score`는 한 시점의 단일
  값만 저장되고 주간 시계열 데이터 자체가 없다. 없는 이력을
  가짜로 만들어 보여주지 않는다.
- "예상 59,000원(29.6%)" 같은 금액 단위 마진 — `margin_score`는
  0~1 AI 점수이지 실제 원화 마진액이 아니다. 실제 예상 마진액
  계산은 매입가·배송비·수수료가 확정된 이후(별도 도메인,
  `margin-analysis` 화면)에나 가능하다 — 이 단계에서 금액을
  지어내지 않는다.

**구현 내용(변경 파일)**:
- `app/web/console.js` — `scoreBarHtml(score, {invert})` 신규
  공용 함수. `candidates`/`trend`/`new-product` 목록과 `candidates`
  상세 6곳의 점수 표시를 전부 교체. 컬럼 라벨 버그 수정
  (`colFundingNeeded`→`colDemand`).
- `app/web/console.css` — `.score-bar*` 클래스 신규(기존
  `risk-high/mid/low` 색상 팔레트 재사용 — 새 색상 도입 없음).
- `app/web/i18n/ko-KR.js`, `en-US.js` — `candidates.col_funding_needed`
  → `candidates.col_demand`(값도 "필요 운영자금"→"수요"로 수정).

**실제 연결된 API**: `/product-candidates`, `/product-candidates/{id}`,
`/product-candidates/{id}/evidence`, `/product-candidates/{id}/decisions`
(전부 기존 API, 신규 API 없음).

**Fake 또는 미구현 기능**: 없음(표시 방식 변경일 뿐 — 기존
"Decision AI는 deterministic 규칙 엔진" 등 정직 공개는 그대로 유지).

**테스트 결과**:
- `tests/test_i18n.py` 33/33 OK(키 rename 후에도 고아 참조 없음을
  `grep`으로 전수 확인 — app/, tests/ 어디에도 옛 키 참조 없음).
- 격리 DB에 점수가 다른 후보 2건(트렌드↑/위험↓ 1건, 트렌드↓/
  위험↑ 1건) + 점수 미분석 1건(전부 null) 시딩 후 브라우저 검증 —
  막대 폭(%)과 `getComputedStyle` 배경색이 값·방향(invert)에 맞게
  정확히 계산되는지 6개 셀 전수 확인(예: risk_score=0.75 →
  risk-high/빨강, trend_score=0.82 → risk-low/초록 — 반대 방향
  검증 포함). null 점수 행이 "—"로 안전하게 처리되는지도 확인.
- 데스크톱·태블릿(1280×720)·모바일(390×844) 3개 뷰포트에서
  `candidates`/`trend`/`new-product`/상세 화면 전부 가로 스크롤
  없음 확인.
- 브라우저 콘솔 오류 없음(네트워크 로그 `/product-candidates` 200
  OK 재확인).

**Whitelist 준수**: `app/web/console.js`, `console.css`,
`i18n/*.js` — Model/Migration 변경 없음.

**기존 WIP 보호**: 후보 승인/보류/거절, AI 자동 등록 파이프라인,
Trend/New-Product 분석 갱신 로직은 전혀 수정하지 않았다.

**위험 요소**: 낮음 — 순수 표시 방식 변경, `scoreBarHtml()`은
null/undefined를 안전하게 "—"로 처리해 기존 "점수 없음" 상태를
그대로 보존한다.

**다음 화면**: UI-5(주문 및 매입 관리, 시안 04) 또는 UI-9(정산 및
손익 관리, 시안 08).

### UI-5 조사 결과 — 이번 라운드에서는 보류

`매입·발주 관리`(purchase-task) 화면을 진행 단계 표시(timeline)
적용 대상으로 검토했으나, 실제 `PurchaseTaskStatus`는 17개 상태로
구성되고 그중 다수(BLOCKED/FAILED/UNCERTAIN/CANCEL_REQUIRED/
RETURN_REQUIRED/REFUND_PENDING/SKIPPED_INVENTORY_AVAILABLE/
SOURCE_ORDER_CANCELLED)가 예외/분기 상태라 shipment/return-order/
refund처럼 단순한 선형 경로로 요약할 수 없다 — 억지로 4단계
timeline에 끼워 맞추면 "실제 상태와 화면 표시가 정확히 일치하는가"
(작업 지시서 10절) 원칙을 어길 위험이 크다고 판단했다. 또한 이
화면은 이미 `ptStatusLabel()`로 상태를 한국어로 번역하고 있어(Gate
PT-1/PT-2/PT-3·Phase 4·Phase 10에서 이미 여러 차례 다듬어짐), UI-7/
UI-8에서 발견했던 "영문 코드 노출" 결함도 없었다. 화면 자체가 이미
성숙한 상태라 판단해 이번 라운드에서는 손대지 않고 다음 기회로
넘긴다(제외가 아니라 이번 라운드 범위 밖 — 후속 검토 시 재평가).

### UI-9. 정산 및 손익 관리 (시안 `08 정산 및 손익 관리.png`) — 부분 완료(채널 정산 화면 개선)

**참고한 실제 코드**: `loadChannelSettlement()`(`app/web/console.js`)
— `/pricing/reconciliations`(주문별 예상 vs 확정 정산액 대사) +
`/settlements`(마켓 정산 건 입금/보류/불일치 관리) 두 API를 이미
하나의 화면에서 함께 보여주고 있었다 — 시안 08의 "정산 예정/입금
확인/차이 확인" 3탭 개념과 상당히 가깝게 이미 구현돼 있었다.

**발견한 기존 결함**: 정산 대사 상태(`PENDING_SETTLEMENT`/
`MATCHED`/`MISMATCH`/`HELD`)와 정산 건 상태(`PENDING`/`HELD`/
`MISMATCH`/`DEPOSITED`/`CANCELLED`/`REVERSED`)가 UI-7/UI-8과 동일한
패턴으로 영문 코드 그대로 노출되고 있었다. 차이 금액(variance_amount)
도 색상 구분 없이 일반 텍스트로만 표시돼, 불일치 건을 훑어보기
어려웠다.

**적용한 시안 요소**:
- **수정해서 적용**: 시안의 "예상/확정 비교를 한눈에" 아이디어를
  차용해, 차이 금액이 정확히 0(일치)이면 초록, 0이 아니면(방향과
  무관하게) 빨강으로 강조했다. 시안은 "-3,000원"(감소) 한 사례만
  보여줘 양수 차이를 어떻게 다룰지 근거가 없었다 — "정산액이
  기대보다 많으면 좋음"이라고 임의로 해석해 색을 반대로 칠하면
  실제로는 확인이 필요한 불일치를 좋은 신호처럼 보이게 할 위험이
  있어, 0 여부만으로 판단했다(추측 기반 해석 회피).
- **그대로 적용**: 정산 대사·정산 건 상태 라벨을 한국어로 번역
  (UI-7/UI-8과 동일한 `statusPillHtmlLabeled()` 재사용).

**적용하지 않은 요소 — 보류**:
- 시안의 "상품별 정산 내역"(주문수/매입율/수수료율/배송비 건당/
  이익률까지 포함하는 표) — 이 상세 분해는 `finance`/
  `margin-analysis` 화면의 영역이라 이번에는 다루지 않았다.
- 예상 vs 확정 막대 차트 — 위 표 형태 개선으로 이미 예상/확정/
  차이가 한 행에 나란히 보이므로, 별도 차트 라이브러리 도입 없이도
  목적(차이를 한눈에)은 달성된다고 판단해 차트는 추가하지 않았다.

**구현 내용(변경 파일)**:
- `app/web/console.js` — 정산 대사·정산 건 상태를
  `statusPillHtmlLabeled(status, "stl.status.")`로 교체. 차이 금액에
  0/비0 조건부 `risk-low`/`risk-high` 색상 클래스 적용.
- `app/web/i18n/ko-KR.js`, `en-US.js` — `stl.status.*`(8개, 두 상태
  집합이 공유 — 겹치는 문자열 없음 확인).
- `tests/test_i18n.py` — `ChannelSettlementTranslationTestCase`
  신규(두 JS 배열 전체 값 고정).

**실제 연결된 API**: `/pricing/reconciliations`, `/settlements`,
관련 보류/해제/불일치플래그/해소 액션 — 전부 기존 API.

**Fake 또는 미구현 기능**: 없음.

**테스트 결과**: `tests/test_i18n.py` 35/35 OK. 격리 DB(대사 3건 —
불일치/일치/정산대기 + 정산 건 2건 — 보류/입금완료, `FundingAccount`
함께 시딩) + 브라우저 검증 — 상태 라벨 6종 한국어 확인, 차이 금액
색상(0→초록, -1,000→빨강) `getComputedStyle`로 확인. 모바일(390px)
가로 스크롤 없음, 콘솔 오류 없음(네트워크 로그 `/settlements` 200
OK 재확인).

**Whitelist 준수**: `app/web/console.js`, `i18n/*.js`,
`tests/test_i18n.py` — Model/Migration 변경 없음.

**기존 WIP 보호**: 실제 마진 반영/보류/보류해제/불일치 플래그/
불일치 해소/입금확인/환수 액션 로직은 전혀 수정하지 않았다.

**위험 요소**: 낮음 — UI-7/UI-8에서 이미 검증된 동일 패턴 재사용.

**다음 화면**: `finance`(자금·정산)/`margin-analysis`(마진·수익
분석) 화면의 상품별 정산 내역 상세화, 또는 UI-4(이미지 제작 및
편집 — 기존 Fabric.js MVP와의 관계 조사).

### UI-9 후속. 상품별 정산 상세 표 — `margin-analysis`(마진·수익 분석) 화면 완료

사용자 요청으로 "상품별 정산 상세 표"를 이어서 구현했다. 착수 전
조사에서 중요한 사실을 발견: 이 기능은 **새로 만들 필요가 없었다**
— 이미 존재하는 `margin-analysis`(마진·수익 분석) 화면이 시안
08의 "상품별 정산 내역" 요구를 거의 그대로 충족하는 API
(`/pricing`, `/pricing/{listing_id}/margin-snapshots`,
`/pricing/{listing_id}/margin-variance`)를 갖고 있었는데, 화면이
그중 상당 부분을 **쓰지 않고 버리고 있었을 뿐**이었다. 처음
추정했던 "cross-domain 집계가 필요한 새 리포팅 기능"이라는 판단은
틀렸다 — 실제로 필요한 건 기존 화면 개선이었다(교훈: 큰 기능을
"새로 만들어야 한다"고 성급히 단정하기 전에 기존 API 활용도를
먼저 조사해야 한다).

**발견한 기존 결함**:
1. `margin-analysis`의 원가율 입력 4개(채널 수수료율/결제
   수수료율/반품충당율/세율)가 Phase 10이 `purchase_task`/
   `retail_purchase` 화면에서 이미 고친 "0~1 소수점 직접 입력"
   결함 그대로였다(라벨도 "(0~1)"). 이 화면만 그 정리에서
   빠져 있었다.
2. `MarginType`(EXPECTED/ACTUAL)이 마진 스냅샷 표에서 영문 원문
   그대로 노출되고 있었다(UI-7/UI-8/UI-9에서 이미 여러 번 고친
   것과 동일한 패턴).
3. **가장 중요한 발견**: `/pricing/{listing_id}/margin-variance`
   API가 `expected`/`latest_actual` 두 완전한 스냅샷(매출/매입/
   수수료/배송비 등 12개 항목 + 마진율)을 이미 응답으로 주고
   있는데, 기존 화면 코드는 이 둘을 전부 버리고 미리 계산된 차이값
   2개(`margin_amount_variance`/`margin_rate_variance`)만 보여주고
   있었다 — 시안 08이 원하는 "예상/확정/차이 3열 비교표"를 만들 수
   있는 데이터가 이미 API에 있었는데 화면이 활용하지 않고 있었다.

**적용한 시안 요소**:
- **그대로 적용**: 시안의 "예상(예시 데이터)/확정(실제 데이터)/
  차이" 3열 비교 표 구조를 실제 API 데이터로 구현. `revenue`(매출)
  부터 `margin_rate`(마진율)까지 13개 항목 전부 표시 — 시안이
  보여준 매출/매입/수수료/배송비/이익보다 더 세분화된 실제 데이터
  (결제수수료·포장비·광고비·반품충당금·세금·환불조정을 별도
  행으로)를 그대로 노출했다(지어내지 않고 이미 있는 걸 다 보여줌).
- **수정해서 적용**: 차이 열 색상 강조는 UI-9(채널 정산)에서 이미
  내린 판단을 그대로 재사용 — 방향(플러스/마이너스)을 좋다/
  나쁘다로 해석하지 않고 "0=일치(초록)/0 아님=검토 필요(빨강)"로만
  구분했다(마진액 차이가 음수라고 마진율 차이까지 자동으로 나쁜
  방향이라 단정하지 않는다 — 단지 "차이가 있다"만 표시).
- **정직성**: 확정(ACTUAL) 스냅샷이 아직 없는 Listing(정산 반영
  전)은 "확정"/"차이" 열을 전부 "—"로 남기고 0으로 추측하지
  않는다 — 상단에 "아직 확정(실측) 마진 스냅샷이 없습니다 — 정산
  반영 전입니다" 고지도 추가.
- **그대로 적용(작은 개선)**: Listing ID만 보이던 상세 화면
  헤더에 상품명을 추가했다(`MarketplaceListing.product_candidate_id`
  →`ProductCandidate.product_name` 2단계 조회, 둘 중 하나라도
  실패하면 "상품명을 확인할 수 없습니다"로 안전하게 대체 — 상세
  화면 자체를 막지 않음). 목록 화면에는 N+1 API 호출을 피하려고
  적용하지 않았다(상세 화면 1회 조회에서만 수행).

**구현 내용(변경 파일)**:
- `app/web/console.js` — `PRC_RATE_FIELDS`(4개 비율 필드 집합),
  `PRC_COMPARISON_ITEMS`(13개 비교 항목), `prcComparisonTableHtml()`
  신규 함수. 초기화/원가 갱신 폼의 비율 입력을 %로 표시·저장 시
  ÷100. `margin_type`을 `statusPillHtmlLabeled()`로 교체. 상세
  화면에 상품명 2단계 조회 추가.
- `app/web/i18n/ko-KR.js`, `en-US.js` — 4개 라벨 수정("(0~1)"→
  "(%)"), `prc.comparison_*`/`prc.item.*`(13개)/`prc.margin_type.*`
  /`prc.product_name_*` 신규(약 25개 키).
- `tests/test_i18n.py` — `MarginAnalysisComparisonTableTranslationTestCase`
  신규 3개 테스트, 그중 하나는 백엔드 `MarginSnapshotResponse`의
  모든 금액 필드가 `PRC_COMPARISON_ITEMS`에 빠짐없이 포함되는지
  자동 대조(새 필드가 스키마에 추가되는데 화면이 놓치는 드리프트를
  원천 차단).

**실제 연결된 API**: `/pricing`, `/pricing/{id}`, `/pricing/init`,
`/pricing/{id}/economics`, `/pricing/{listing_id}/price-changes`,
`/pricing/{listing_id}/margin-snapshots`, `/pricing/{listing_id}/
margin-variance`, `/marketplace-listings/{id}`,
`/product-candidates/{id}`(신규로 화면과 연결 — 상품명 조회 전용,
전부 기존 API, 신규 API 없음).

**Fake 또는 미구현 기능**: 없음.

**테스트 결과**:
- `tests/test_i18n.py` 54/54 OK, `tests/
  test_route_authentication_contract.py` 3/3 OK.
- 격리 DB(실제 `PricingService.initialize_pricing()`를 그대로
  호출해 시딩 — 계산 로직을 재구현하지 않고 실제 서비스 로직
  재사용) + 브라우저 검증:
  - 신규 등록 폼: 수수료율 10%/2%/1%/0%로 입력 → 판매가 50,000
    기준 예상마진율 20.0%/마진액 10,000원으로 정확히 계산됨을
    수동 검산으로 확인(÷100 변환이 실제로 적용됨 — 안 됐다면
    1000%대 오류값이 나왔을 것).
  - 확정 스냅샷이 있는 Listing: "예상 대비 확정 비교" 표 13개
    행 전부 정확한 금액·차이 확인, 차이 3건(채널수수료+1,100/
    결제수수료+220/배송비+500)만 빨강, 나머지 0원 항목은 초록
    확인. 마진액 차이(-1,820원)와 마진율 차이(-0.9%p)도 부호까지
    정확히 일치.
  - 확정 스냅샷이 없는 Listing: "정산 반영 전" 고지 + 모든
    확정/차이 열이 "—"로 정직하게 표시됨을 확인(0으로 추측하지
    않음).
  - 두 Listing 모두 상품명("[UI검증] 무선 청소기 G10"/"[UI검증]
    보온 텀블러 500ml") 정상 표시, 네트워크 로그로 2단계 조회
    (`/marketplace-listings/{id}`→`/product-candidates/{id}`)
    성공 확인.
  - 모바일(390×844) 가로 스크롤 없음, 콘솔 오류 없음.

**Whitelist 준수**: `app/web/console.js`, `i18n/*.js`,
`tests/test_i18n.py` — Model/Migration 변경 없음.

**기존 WIP 보호**: 가격 변경 승인/거절/취소, 원가 갱신 저장 로직,
Pricing/Margin 백엔드 서비스는 전혀 수정하지 않았다 — 화면이
이미 있는 응답 필드를 더 쓰도록만 바꿨다.

**위험 요소**: 낮음 — 순수 표시 확장(기존에 버려지던 응답 필드를
추가로 렌더링) + 이미 검증된 패턴(`statusPillHtmlLabeled`, 0/비0
색상 규칙, %입력 변환) 재사용. 상품명 조회 실패 시 안전한 폴백
확인 완료.

**교훈(다음 유사 작업에 참고)**: "이 기능은 cross-domain 집계가
필요한 새 기능"이라고 미리 판단하기 전에, 관련 기존 화면들의
API 호출부를 먼저 훑어 "이미 있는데 화면이 안 쓰는 데이터"가
있는지부터 확인한다 — Refund/Payment처럼 화면 자체가 아예 없는
경우와, 이번처럼 화면은 있지만 응답의 일부만 쓰는 경우는 서로
다른 종류의 "저활용"이며, 후자가 훨씬 흔하고 훨씬 빠르게 고칠
수 있다.

### UI-4. 이미지 제작 및 편집 (시안 `03 이미지 제작 및 편집.png`) — 조사만 완료, 구현은 다음 라운드

시안 03을 실제 코드와 대조한 결과, HOMEZ에는 이미 **서로 다른 두
시스템**이 이 영역을 부분적으로 담당하고 있음을 확인했다:

1. **`listing-wizard`의 Fabric.js 수동 편집기**(2026-08-29 Phase 5,
   `lwOpenManualEditor()` 등) — 자르기/회전/밝기·대비/도형·화살표/
   되돌리기. 편집은 브라우저 캔버스에서만 일어나고 서버는 최종
   결과 이미지 1장만 받는다(`POST /media-assets/manual-edit`).
2. **`listing-package`(AI 상품 등록)의 이미지 재생성**
   (`lpRegenerateImages()`) — `POST /media-assets/image-jobs`,
   `provider_code: "FAKE"`로 항상 이미지 전체를 다시 만든다(부분
   재생성 개념 없음).

시안 03과 대조한 gap:
- **문구(텍스트) 편집** — 시안의 핵심 기능(상품명·수량 텍스트
  레이어, 글꼴·크기·색상·정렬·위치 조정, 레이어 패널)이 Fabric
  편집기에 전혀 없다(현재는 자르기/회전/밝기 대비/도형·화살표뿐).
  Fabric.js 자체는 텍스트 객체를 기본 지원하므로 기술적으로는
  추가 가능하지만, 레이어 패널 UI까지 포함하면 새 기능 추가에
  해당해 신중한 별도 설계가 필요하다.
- **"배경만 재생성"** — 시안에 있는 이 개념은 백엔드에 대응하는
  기능이 없다(`image-jobs`는 항상 전체 이미지, 부분 재생성 파라미터
  자체가 API에 없음). 데이터·계약 부재로 **보류** 대상.
- **다중 페이지(대표/상세1/상세2…) 썸네일 스트립 + 드래그 재정렬** —
  `purpose: "MAIN"` 같은 목적 구분은 API에 이미 있어(여러 이미지
  슬롯 개념 자체는 존재) 시안의 "페이지 추가" UI에 가까울 가능성이
  있으나, 실제 `listing-package` 화면이 이 슬롯들을 어떻게 노출하는지
  아직 전수 확인하지 못했다.

**판단**: 이 화면은 두 개의 서로 다른 기존 시스템에 걸쳐 있고
(수동 편집 vs AI 생성), 시안과의 격차 중 상당수가 "화면 개선"이
아니라 "새 기능 추가"(텍스트 레이어 편집)에 해당해, UI-7/UI-8/UI-9
처럼 한 턴 안에서 안전하게 끝낼 수 있는 범위를 넘어선다. 추측으로
서둘러 구현하는 대신, 다음 라운드에 `listing-package` 화면의 이미지
슬롯 구조를 먼저 전수 조사한 뒤 "그대로 적용/수정해서 적용/보류/
제외" 분류를 완료하고 나서 구현에 들어간다.

**다음 화면**: UI-6(매입처 및 결제 설정, 시안 05 — Payment/
SupplierCapability Domain이 이미 있어 UI-7/UI-8과 유사하게 빠르게
진행 가능할 것으로 예상) 우선 진행, UI-4는 별도 라운드에서 이어감.

### UI-6. 매입처 및 결제 설정 (시안 `05 매입처 및 결제 설정.png`) — 결제 수단 부분 완료(신규), 매입처 연결은 이미 존재

**참고한 실제 코드**: `app/domains/payment/router.py`(Phase 7,
이번 세션) — 결제수단 등록/조회/비활성화/기본설정, 자동결제 한도
설정/조회, "지금 자동결제 가능한가" 판정. `store-connection`(판매채널
연동)/`supplier-sourcing`(공급처·발주) 화면은 이미 존재해 시안의
"매입처 연결 상태" 부분을 별도 화면에서 이미 담당하고 있다.

**발견한 기존 결함**: Refund와 정확히 같은 패턴 — Payment 도메인
(백엔드·테스트 완비, `tests/test_payment_domain.py` 28개)에
대응하는 화면이 콘솔에 전혀 없었다(`grep "payments" console.js`
결과 0건). 운영자가 결제수단이나 자동결제 한도를 설정할 방법이
없는 상태.

**중요한 설계 판단 — 시안을 그대로 따르지 않은 부분**:
- 시안은 "결제 수단 정보" 입력란에 마스킹된 카드번호(`•••• ••••
  •••• ••••`)를 보여준다. 하지만 실제 백엔드
  (`app/domains/payment/schema.py`)는 `raw_details`를 **절대 DB에
  저장하지 않는다** — 요청 처리 도중 메모리에서만 존재하다가
  `FakePaymentProvider.tokenize()`가 무시하고 버린다(실제 카드
  유효성조차 검사하지 않음). 이 상태에서 사용자에게 실제 카드
  번호·CVC·계좌번호 입력을 요구하는 화면을 만들면, "이 결제수단이
  실제로 연결됐다"는 잘못된 인상을 주면서 실제로는 아무 의미
  없는 값을 받아 버리는 것이 된다 — 정직성 원칙 위반이자 불필요한
  민감정보 입력 요구. **등록 폼에 실제 카드/계좌 정보 입력 필드를
  아예 두지 않고**, 종류·구분용 이름만 받는다. 이 판단은
  `tests/test_i18n.py::PaymentMethodUiTranslationTestCase::
  test_register_request_never_sends_a_real_card_field`로 코드
  레벨에 고정했다(항상 `raw_details: {}` 전송).
- 시안의 "자동 매입" 토글은 만들지 않았다 — HOMEZ는 이미 "자동화
  안전" 화면에서 `FunctionCode.PAYMENT`를 포함한 기능별 자동화
  모드를 중앙에서 관리한다. 여기에 또 다른 토글을 만들면 같은
  상태를 두 곳에서 따로 제어하는 위험한 중복이 생긴다(작업
  지시서 11절 금지 사항 "같은 기능을 가리키는 버튼 중복 생성").
  대신 "이 설정은 자동화 안전 화면에서 관리합니다" 안내만 둔다.
- 시안의 "일일 예산" 필드는 그대로 두되(`daily_limit_amount`는
  실제로 저장·API에 존재하는 값), **실제로는 건당 한도만 적용되고
  일일 누적은 아직 집계되지 않는다는 사실을 화면에 상시 고지**했다
  (`PaymentService.verify_auto_payment_allowed()` 주석 확인 —
  "집계할 원장이 없다"). 값을 받아 저장만 하고 조용히 무시하는
  대신, 사용자가 오해하지 않도록 명시한다.

**구현 내용(변경 파일, 전부 신규)**:
- `app/web/console.js` — `loadPayment()`(목록+등록+한도 설정),
  `VIEW_LOADERS["payment"]` 등록. 결제수단 등록/기본설정/비활성화는
  `promptRecentAuthToken()`(비밀번호 재확인) 재사용, 자동결제 한도
  저장도 동일.
- `app/web/console.html` — "주문 관리" 그룹에 "결제 수단" nav 버튼
  + `#view-payment` 섹션.
- `app/web/i18n/ko-KR.js`, `en-US.js` — `pay.*`(약 45개).
- `tests/test_i18n.py` — `PaymentMethodUiTranslationTestCase`
  신규(동적 키 고정 + 위 두 정직성 판단을 코드 패턴으로 고정하는
  전용 테스트 2개 포함, 백엔드 `PaymentMethodType.ALL`과의 드리프트
  방지 테스트 포함).

**실제 연결된 API**: `POST/GET /payments/methods`,
`POST /payments/methods/{id}/deactivate`,
`POST /payments/methods/{id}/set-default`,
`PUT/GET /payments/auto-limit`(전부 기존 Phase 7 API, 신규 API
없음).

**Fake 또는 미구현 기능**: 결제수단 등록 자체는 Fake
Provider(`FakePaymentProvider`)만 거친다 — 화면 상단에 상시 고지.
`daily_limit_amount`는 저장되지만 미집계 — 별도 고지(위 참고).

**테스트 결과**:
- `tests/test_i18n.py` 40/40 OK, `tests/
  test_route_authentication_contract.py` 3/3 OK.
- 격리 DB + 브라우저에서 **전체 흐름을 실제 API 왕복으로 검증**:
  결제수단 등록(비밀번호 재확인 포함) → 목록에 반영 → 기본으로
  설정(즉시 반영, "기본" 열이 "아니오"→"예") → 비활성화(비밀번호
  재확인 포함, 상태 "사용 중"→"비활성화됨", 기본 여부 자동
  초기화, 작업 버튼 사라짐) → 자동결제 한도 저장(비밀번호 재확인
  포함, "현재 설정"에 즉시 반영). 네트워크 로그로 `POST /payments/
  methods/1/deactivate → 200 OK` 등 전부 재확인.
- 모바일(390×844) 가로 스크롤 없음, 콘솔 오류 없음.

**Whitelist 준수**: `app/web/console.js`, `console.html`,
`i18n/*.js`, `tests/test_i18n.py` — Model/Migration 변경 없음
(Payment Model/Migration은 이미 Phase 7에서 존재).

**기존 WIP 보호**: Payment 백엔드 서비스 로직, 자동화 안전 화면의
기능별 모드 제어 로직은 전혀 수정하지 않았다.

**위험 요소**: 낮음 — Refund 화면과 동일한, 이미 검증된 패턴만
재사용. 유일한 신규 설계 판단(카드 정보 미입력, 일일한도 미적용
고지, 자동모드 토글 미중복)은 전부 테스트로 고정해 향후 실수로
되돌아가지 않게 했다.

**다음**: `finance`/`margin-analysis` 상세화, UI-4(이미지 제작·
편집) 또는 남은 Phase 7~12 신규 Domain(Currency/SupplierCapability/
PriceStockSafety/AiLearning) UI 부재 여부 전수 점검 — Refund/
Payment와 같은 패턴이 더 있을 가능성.

## 4. 시스템 차원 발견 — 이번 세션 Phase 7~12 신규 Domain 6개 전부 UI 부재 확인

Refund(Phase 8)·Payment(Phase 7) 화면을 만들며 발견한 "백엔드는
완성됐는데 화면이 전혀 없다" 패턴이 우연이 아니라 **이번 세션에서
새로 만든 Domain 전체의 공통 특성**인지 `grep`으로 전수 확인했다.
`console.js`에서 각 Domain의 실제 라우터 경로를 검색한 결과:

| Domain(Phase) | 라우터 경로 | console.js 참조 |
|---|---|---|
| Payment(7) | `/payments` | 0건 → **UI-6에서 완료** |
| Refund(8) | `/refunds` | 0건 → **UI-8에서 완료** |
| Currency(9) | `/currency` | 0건 → 미구현 |
| SupplierCapability(9) | `/suppliers/{id}/capability` | 0건 → 미구현 |
| PriceStockSafety(10) | `/price-stock-safety` | 0건 → 미구현 |
| AiLearning(12) | `/ai-learning` | 0건 → 미구현 |

즉 이번 세션이 추가한 6개 신규 Domain 중 **아직 4개(Currency/
SupplierCapability/PriceStockSafety/AiLearning)가 콘솔 화면 없이
API만 존재하는 상태**다. 이는 각 Phase가 "구현·테스트는 이번
Phase 범위, UI는 별도"로 명시적으로 경계를 그은 결과이지 실수가
아니다(각 Phase 완료 보고에도 정직하게 기록돼 있었음) — 다만 이번
UI 개선 작업의 "기존 결함 우선 원칙"(작업 지시서 6절) 관점에서는
"실제로 쓸 수 없는 완성된 기능"이라는 동일한 종류의 gap이다.

이 4개는 시안 8장 어디에도 직접 대응하는 화면이 없다(시안은 이번
세션 신규 기능을 반영하지 않음 — 당연하다, 시안이 먼저 만들어졌기
때문). 따라서 이 4개를 만드는 것은 "시안 반영"이 아니라 독립적인
"기존 결함 우선 원칙" 작업이며, 규모도 각각 Refund/Payment급으로
추정된다(도메인당 목록+상세+액션 화면, i18n, 테스트, 브라우저
검증). 사용자에게 이 발견을 보고했고, 사용자가 "둘 다 순서 상관없이
계속 진행"으로 답해 아래 4개를 전부 완료했다.

## 5. 남은 신규 Domain 4개 UI 완료 — Currency / SupplierCapability / PriceStockSafety / AiLearning

사용자 승인(우선순위 무관 계속 진행)에 따라 나머지 4개 신규
Domain의 화면을 전부 구축했다. 각 도메인은 API 형태가 서로 달라
(전역 설정형 vs 공급처별 조회형 vs 목록+상세+승인 흐름형) 화면
패턴도 그에 맞게 달리 설계했다 — 억지로 하나의 템플릿에 끼워
맞추지 않았다.

### 5-1. 환율 관리 (Currency, Phase 9)

`/currency/rates`(기록), `/currency/rates/latest`(조회),
`/currency/tolerance`(허용률 조회/설정) — 전체 목록 조회 API가
없어(최신 1건 조회만 가능) "기록 + 조회 + 허용률 설정" 3-블록
구조로 만들었다.

**정직성 판단**: (1) 실제 외부 환율 API 연동이 없다는 사실
(`source`가 항상 `MANUAL_ADMIN_ENTRY`로 응답됨을 실제 확인)과
(2) 허용률 초과 판정 로직(`check_rate_within_tolerance`)이 아직
실제 매입 발주 흐름에 자동 연결되지 않았다는 사실(`rate_at_
baseline`을 어디서 채울지 이번 Phase가 연결하지 않음, 서비스
모듈 docstring 확인) — 둘 다 화면 상단에 상시 고지했다.
`tolerance_percent`는 서비스 코드에서 실제로 `×100` 스케일로
비교되는 진짜 백분율임을 확인 후(0~1 아님) "%" 단위로 그대로
표시했다(Phase 10의 "0~1 아닌 % 표시" 원칙과 일관).

통화 선택지는 백엔드 `KNOWN_CURRENCIES`(KRW/USD/CNY/JPY/EUR)를
그대로 반영, 드리프트 방지 테스트로 고정.

### 5-2. 공급처 능력 (SupplierCapability, Phase 9)

API가 공급처 1곳 단위(`/suppliers/{id}/capability/*`)라 전체
목록 화면 대신 "공급처 ID 조회" 방식으로 만들었다(Currency의
조회 패턴 재사용). 공급처 프로필(해외 여부/국가코드/기본통화/
위탁 직접배송)과 8개 능력 플래그(상품·옵션·가격·재고·배송비·
배송기간·발주가능·취소가능 정보, 각각 지원/미지원/미확인
3단계 + 메모)를 한 화면에서 조회·수정한다.

**정직성 판단**: "미확인"(UNKNOWN)을 절대 추측으로 채우지
않는다는 백엔드 설계(`CapabilitySupport` docstring)를 그대로
반영 — 기본값을 임의로 SUPPORTED/NOT_SUPPORTED로 미리 채우지
않고 서버가 실제로 응답한 값(신규 공급처는 전부 UNKNOWN)만
표시한다.

기존 `supplier-sourcing`(공급처·발주) 화면에 깊이 통합하는
대신 독립 화면으로 만든 이유: 그 화면은 이미 4개 탭·복잡한 상태
머신을 가진 대형 화면이라, 거기 새 로직을 끼워 넣는 것은 기존
동작에 회귀 위험을 더하는 반면, 독립 화면은 위험 없이 빠르게
검증 가능한 크기를 유지한다(작은 화면 우선 원칙).

### 5-3. 가격·재고 안전 설정 (PriceStockSafety, Phase 10)

가상재고 임계값 설정 + 수동 확인 도구, 가격 검토주기 설정 —
2개의 회사 단위 설정값과 1개의 판정 도구.

**정직성 판단(가장 중요한 부분)**: 서비스 모듈 docstring이
스스로 명시한 두 가지 미연결 사실을 빠짐없이 화면에 고지했다 —
(1) "가상재고 확인"은 사용자가 직접 입력한 숫자를 판정하는
수동 도구일 뿐, 판매채널의 실제 표시 재고를 자동으로 읽어오지
않는다(그런 필드 자체가 `marketplace_listing` Domain에 아직
없음). (2) 가격 검토주기는 저장되지만 실제 스케줄러 Job에
연결되지 않았다. 이 두 고지가 화면에서 빠지면, 사용자가 "설정만
하면 자동으로 동작한다"고 오해할 위험이 컸다.

### 5-4. AI 학습 기반 (AiLearning, Phase 12)

모델 후보 심사 워크플로우 — `ModelCandidateStatus`가 DRAFT→
OFFLINE_EVALUATED→REGRESSION_COMPARED→APPROVED(REJECTED는
어느 단계에서나 분기 가능)의 깨끗한 선형 경로라 Refund/
ReturnOrder와 같은 목록+상세+단계별 액션 패턴을 재사용했다.
다만 단계별 정확한 시각 필드가 `created_at`/`approved_at`뿐이라
(중간 두 단계는 타임스탬프 없음) 가짜로 만들어내지 않기 위해
UI-7/UI-8/UI-6에서 썼던 단계 표시(timeline) 컴포넌트는 이번엔
쓰지 않았다 — 상태 라벨 + 각 단계 요약 텍스트(오프라인 평가
요약/회귀 비교 요약)로 충분히 진행 상황을 전달할 수 있다고
판단했다.

**정직성 판단**: 백엔드 `ModelCandidateStatus` docstring이
"APPROVED조차 실제 라이브 적용을 의미하지 않는다"고 스스로
명시한다 — 이 사실이 화면에서 사라지지 않도록 상태 라벨 자체에
"승인됨(실제 적용은 별도)"를 그대로 반영하고, 화면 상단에도
별도 고지 문구를 뒀다. 둘 다 전용 테스트로 고정(라벨에 "별도"/
"separate" 문자열이 실제로 포함되는지까지 검사).

**적용하지 않은 것 — 보류**: 평가결과 기록(`POST /ai-learning/
outcomes`)과 데이터셋 export(`POST /ai-learning/dataset/export`)는
`evaluation_id`가 필요한데, 이 ID를 사용자가 어디서 자연스럽게
얻는지가 기존 `decision`/`decision-detail`(Decision AI) 화면과의
연결 설계 문제라 이번 범위에 포함하지 않았다. Decision AI 화면을
다시 열어보고 "이 평가에 대한 실제 결과를 기록" 같은 진입점을
추가하는 것이 옳은 방향으로 보이나, 그 화면 자체를 함께 고쳐야
해서 별도 작업으로 분리했다.

### 공통 사항 (5-1~5-4)

**변경 파일**: `app/web/console.js`(4개 화면 로직 + 관련 헬퍼),
`app/web/console.html`(4개 nav 버튼 + 4개 section), `app/web/i18n/
{ko-KR,en-US}.js`(약 150개 키 추가), `tests/test_i18n.py`(4개
TranslationTestCase 신규 — 전부 백엔드 상수와의 드리프트 방지 +
정직성 고지 문구 존재 검증 포함).

**실제 연결된 API**: 전부 이번 세션 Phase 9/10/12가 만든 기존
API만 사용, 신규 백엔드 코드 없음(순수 프론트엔드 작업).

**테스트 결과**: `tests/test_i18n.py` 48/48 OK,
`tests/test_route_authentication_contract.py` 3/3 OK(신규 화면
4개 전부 신규 백엔드 라우트 없음 재확인).

**브라우저 검증(격리 DB + 실제 서버, 실제 API 왕복)**:
- Currency: 환율 기록(1 USD = 1380.5 KRW, source=MANUAL_ADMIN_ENTRY
  확인) → 조회 → 허용률 변경(2%→3.5%) 전부 확인.
- SupplierCapability: 프로필 저장(해외/CN/CNY) + 능력 플래그 1건
  저장(PRODUCT_INFO=SUPPORTED) 후 재조회로 영속 확인.
- PriceStockSafety: 임계값 저장(10) → 확인 도구로 차단 케이스
  (재고 5 ≤ 임계값 10)와 허용 케이스(재고 50) 둘 다 확인 → 검토
  주기 저장(7일→14일) 확인.
- AiLearning: 후보 등록 → 오프라인평가완료 → 회귀비교완료 →
  승인(비밀번호 재확인 포함, "승인됨(실제 적용은 별도)" 라벨
  확인) — **전체 4단계 상태 전이를 실제 API로 완주**. 별도
  후보로 반려 흐름도 확인. 데이터셋 준비도 확인 도구도 확인
  (0건/필요 50건 — 정직한 응답).
- 4개 화면 전부 모바일(390×844) 가로 스크롤 없음, 콘솔 오류 없음.

**Whitelist 준수**: 전부 `app/web/console.js`, `console.html`,
`i18n/*.js`, `tests/test_i18n.py`만 — Model/Migration 변경 없음
(4개 도메인 모두 Phase 9/10/12에서 이미 존재).

**기존 WIP 보호**: 4개 도메인의 백엔드 서비스 로직은 전혀 수정
하지 않았다. 기존 `supplier-sourcing`/`decision` 등 화면의 기존
로직도 수정하지 않았다(SupplierCapability를 독립 화면으로 분리한
이유가 바로 이 보호를 위해서였다).

**위험 요소**: 낮음 — 전부 이미 검증된 컴포넌트(`statusPillHtmlLabeled`,
`promptRecentAuthToken`, `confirmDialog`, `withButtonGuard`)만
재사용, 새로운 프론트엔드 패턴 없음. 정직성 고지 5개 전부(Currency
2개, PriceStockSafety 2개, AiLearning 1개 상태라벨+1개 배너)를
전용 테스트로 고정해 향후 실수로 삭제되지 않게 했다.

### 5-5. 부록 — `finance`(자금·정산) 화면에서 발견한 별개 결함 2건

4개 신규 화면을 만들며 기존 `finance` 화면을 참고하다가 이번
Phase와 무관한 기존 결함 2건을 추가로 발견·수정했다:

1. **정산 상태 미번역**: `loadFinance()`의 Marketplace Settlement
   카드가 `stl.status.*`(UI-9에서 이미 만든 키)를 쓰지 않고
   `settlement.status`를 그대로 노출하고 있었다 — 같은 데이터를
   다루는 두 화면(`finance`/`channel-settlement`) 중 한쪽만 라벨을
   번역해 둔 상태였다. 이미 검증된 `stl.status.*` 키를 그대로
   재사용해 수정(신규 키 없음, 신규 테스트 불필요 — 기존 8개
   상태 키가 이미 이 데이터의 전체 상태 집합을 커버).
2. **`ko-KR.js`에 원문 영어가 그대로 남아있던 4개 제목**:
   `finance.available_funding_title`/`finance.hold_title`/
   `finance.supplier_payment_title`/`finance.marketplace_
   settlement_title`이 `en-US.js`와 완전히 동일한 영문이었다 —
   같은 화면의 다른 모든 문구는 정상적으로 한국어인데 이 4개
   제목만 번역된 적이 없었다(단순 실수로 판단, en-US.js와 값이
   1바이트도 다르지 않게 완전 일치하는 것이 근거). "가용
   운영자금(Funding Account)"/"예약(Hold) 금액"/"공급처 지급"/
   "판매채널 정산"으로 수정 — 이미 같은 화면의 다른 문구가 쓰는
   어휘(가용/운영자금/공급처 지급/정산)와 일관되게 맞췄다.

   **참고로 판단해 고치지 않은 것**: `decision_detail.override_
   confirm_ok`("Override")와 `prc.col_listing_id`/`prc.listing_
   id_label`("Listing ID")도 ko/en 값이 동일했으나, 각각 같은
   기능 안의 다른 여러 문구("평가 Override", "Override
   처리되었습니다" 등)에서 이미 일관되게 영어 loanword로 쓰이고
   있어 단순 누락이 아니라 의도된 용어 선택으로 판단해 손대지
   않았다. `finance.*`처럼 "주변은 전부 한국어인데 이것만 영어"인
   명백한 불일치 패턴만 결함으로 취급했다 — 확신 없는 스타일
   판단으로 임의 변경하지 않는다는 원칙.

브라우저 재확인: "판매채널 정산" 카드가 "입금 완료: 1 / 보류: 1"로
정확히 표시됨(기존 "DEPOSITED: 1 / HELD: 1"에서 개선), 4개 제목
전부 한국어로 렌더링 확인. `tests/test_i18n.py` 48/48 OK(신규
테스트 불필요 — 기존 카탈로그 무결성 테스트가 이미 커버).

## 6. 이번 라운드 종합 — 신규 도메인 UI 6개 전부 완료

Payment/Refund/Currency/SupplierCapability/PriceStockSafety/
AiLearning — 이번 세션이 만든 신규 Domain 6개 전부 화면을 갖추게
됐다. 남은 작업은 원래 시안 8장 기반 작업(UI-2~UI-3 일부 완료,
UI-4 조사만 완료, UI-5 재평가 대상, UI-6은 Payment 부분만 완료
(매입처 연결 자체는 기존 화면), UI-7·UI-8 완료, UI-9 채널정산
부분만 완료)이다.

## 3. 다음 계획

이번 세션은 UI-7(배송 관리)·UI-8(취소·반품 관리, 기존 화면 개선분)
을 완료했다. 공통 단계 표시(status timeline) 컴포넌트를 만들어
두 화면이 재사용했다 — 다음 화면에도 계속 재사용 가능하다.

남은 작업(우선순위순):
1. **Refund Domain UI 신규 구축** — 백엔드는 이미 완성됐는데 화면이
   전혀 없는 상태를 해소한다(UI-8에서 발견).
2. UI-2(상품 발굴 및 분석), UI-3(상품 검토 및 등록), UI-4(이미지
   제작 및 편집 — 기존 Fabric.js MVP와의 관계 먼저 조사 필요),
   UI-5(주문 및 매입 관리), UI-6(매입처 및 결제 설정), UI-9(정산
   및 손익 관리) — 이 문서에 동일한 형식으로 계속 append한다.
