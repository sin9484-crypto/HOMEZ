# HOMEZ V6.5 — 전체 기능 인벤토리 (Gate Z-1, 2026-08-12)

## 방법론 (정직하게 명시)

`app/domains/` 아래 **107개** 디렉터리 전체를 다음 3가지 구조적
증거로 자동 분류했다:

1. 실제 코드 줄 수(공백 줄 제외, 파일별 합산) — 0줄이면
   `EMPTY_SCAFFOLD`(디렉터리와 빈 `.py` 파일만 존재).
2. `app/main.py`가 그 도메인의 `router`를 **정확히 그 모듈 경로로**
   import하는지(`app.domains.<name>` 또는 `app.domains.<name>.*`,
   접두사 충돌 방지를 위해 정확한 경계로 매칭 — 첫 시도에서
   `media`가 `media_asset`에, `store`가 `store_connection`에 잘못
   매칭되는 버그를 발견해 수정했다).
3. 다른 도메인이 `from app.domains.<name>.` 형태로 실제 import하는지
   (자기 자신 제외) — router가 없어도 내부적으로 쓰이는 도메인을
   "죽은 코드"로 오분류하지 않기 위함.

**이것은 107개 도메인 각각의 비즈니스 로직을 한 줄씩 재검토하는
전면 코드 리뷰가 아니다** — 이번 세션(Gate X~Z)이 직접 다루지 않은
도메인(예: `brand`, `category`, `funding` 등 이미 이전 세션에서
구현된 것들)은 구조적 사실(코드 존재 여부·마운트 여부·참조 여부)
까지만 확인했고, 그 내부 로직의 정확성 자체는 이번 감사 범위가
아니다.

## 1. MOUNTED — 실제 API로 노출되고 있다 (28개)

`app/main.py`에 router가 등록되어 실제 HTTP 엔드포인트로 살아있는
도메인. 괄호는 `tests/` 아래 전용 테스트 파일 존재 여부(파일명
패턴 매칭 — 다른 파일에서 간접적으로 import해 테스트하는 경우는
별도 표로 아래에 정리).

| 도메인 | 코드 줄 수 | 전용 테스트 파일 |
|---|---:|:---:|
| marketplace_listing | 8,952 | ✅ |
| coupang | 2,682 | ✅ |
| store_connection | 2,646 | ✅ |
| media_asset | 2,006 | ✅ |
| decision | 1,842 | ✅ |
| role_permission | 1,194 | ✅ |
| account_registration | 1,179 | ✅ |
| listing_package | 1,131 | ✅ |
| product_candidate | 1,080 | ✅ |
| product | 1,024 | ✅ |
| account_recovery | 1,031 | ✅ |
| order | 1,054 | — |
| supplier | 1,050 | — |
| funding | 1,313 | (간접, 아래 참고) |
| category | 947 | — |
| brand | 941 | — |
| purchase | 909 | (간접, 아래 참고) |
| auth | 797 | ✅ |
| settlement | 796 | ✅ |
| shipment | 863 | — |
| user | 811 | (간접 45개 파일, 아래 참고) |
| company | 418 | ✅ |
| update | 375 | ✅ |
| backup | 339 | ✅ |
| marketplace | 329 | ✅ |
| diagnostics | 289 | ✅ |
| notification_center | 646 | ✅ |
| restore | 429 | ✅ |

**직접 확인된 커버리지 갭(전용 테스트 파일 없음, 간접 참조도 0건)**:
`order`, `supplier`, `category`, `brand`, `shipment` — 5개 도메인.
이번 세션에서 새로 만든 것도 아니고 이번 Gate의 범위도 아니므로
재구현하지 않았다 — **V7 이전에 이 5개 도메인의 테스트 공백을
메우는 별도 작업이 필요하다는 사실만 기록한다.**

`funding`(2개 파일에서 간접 참조 — 주로 Migration 스키마 검증),
`purchase`(1개 파일), `user`(45개 파일 — 거의 모든 다른 도메인
테스트가 인증 principal로 사용)는 전용 테스트 파일은 없지만
간접적으로 실행 경로에 포함된다.

## 2. 내부적으로 쓰이지만 자체 API 라우터는 없다 (5개)

| 도메인 | 코드 줄 수 | 외부에서 import하는 파일 수 | 비고 |
|---|---:|---:|---|
| role | 310 | 13 | `has_role()` 등 권한 판정 전반에서 모델로 사용 |
| permission | 437 | 7 | `role_permission` 도메인의 관리 대상 모델 |
| session | 264 | 7 | 인증 세션 관리에 내부적으로 사용 |
| inventory | 1,092 | 5 | 다른 도메인 재고 로직에서 참조 |
| automation_safety | 891 | 5 | `marketplace_listing.submission_service`의 `SafetyService` |

정상 — 굳이 자체 REST API가 필요 없는(다른 도메인의 내부 로직으로만
쓰이는) 설계다. 결함이 아니다.

## 3. 코드는 있지만 어디서도 참조되지 않는다 (5개) — 정리 후보

| 도메인 | 코드 줄 수 | 외부 참조 |
|---|---:|---:|
| activity_log | 619 | 0 |
| audit | 32 | 1(`app/api/router.py`, 그 자체가 `app/main.py`에서 미마운트 — 죽은 트리) |
| new_product_discovery | 107 | 0 |
| settings | 216 | 0 |
| trend_discovery | 196 | 0 |

`app/domains/audit`는 유일한 참조자인 `app/api/router.py`조차
`app/main.py`가 import하지 않는다 — 이 세션 이전부터 존재해온
레거시 트리(`app/marketplace`, `app/service` 등과 동일 계열, ADR
0001 참고)의 일부로 판단된다. **삭제하지 않았다** — 삭제는 별도
승인 대상이며, 이번 Gate의 범위는 "기록"까지다.

## 4. EMPTY_SCAFFOLD — 코드가 전혀 없다 (69개)

디렉터리와 빈 `.py` 파일만 존재(0바이트 또는 공백만). 대부분
향후 확장을 위해 미리 만들어둔 자리로 보인다. Gate Y에서 이 중
6개(`backup`, `restore`, `notification_center`, `update`,
`diagnostics`는 신규 디렉터리, `notification`/`version`은 여전히
빈 상태로 남음 — 실제 구현은 별도 도메인명(`notification_center`,
`update`)으로 했다)를 실제 구현으로 옮겼다. 나머지 69개는 여전히
빈 상태다:

```
ai, ai_memory, analytics, api_client, api_key, Automation, banner,
brand_mapping, cache, category_mapping, config, coupon, crawler,
currency, customer, dashboard, email, error_log, event, event_bus,
exchange, extension, favorite, feature_flag, file, job, keyword,
license, login_history, market_mapping, media, Message Center,
monitor, notification, oauth, ocr, order_item, organization,
payment, plugin, price_history, prompt, Push, queue, recent_view,
refund, report, return_order, review, rule engine, scheduler,
search, search_history, shipping, sms, source, statistics, store,
system, tag, task, tenant, tracking, translation, version, webhook,
websocket, workflow, workspace
```

이 목록 중 `email`, `sms`, `payment`, `webhook`, `oauth`는 V7 Live
Gate(실제 외부 API/실제 발송/실제 결제)와 직접 연결될 항목이므로
`docs/V7_LIVE_GATE_PLAN.md`에서 별도로 다룬다.

## 5. 이번 Gate Y에서 새로 채워진 6개 (재확인)

| 도메인 | 이전 상태 | 현재 상태 |
|---|---|---|
| backup | EMPTY_SCAFFOLD | MOUNTED, 10 tests |
| restore | EMPTY_SCAFFOLD | MOUNTED, 11 tests(실행 엔드포인트는 의도적 미노출) |
| notification_center | EMPTY_SCAFFOLD | MOUNTED, 17 tests |
| update | 디렉터리 자체가 없었음 | MOUNTED, 17 tests(신규 도메인) |
| diagnostics | 디렉터리 자체가 없었음 | MOUNTED, 14 tests(신규 도메인) |
| (packaging, 도메인 아님) | 없음 | `homez.spec` + 11 tests |
