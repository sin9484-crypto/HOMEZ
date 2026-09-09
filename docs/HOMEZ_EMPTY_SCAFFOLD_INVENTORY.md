# HOMEZ 빈 스캐폴드 인벤토리 (Gate R-10, 문서 전용 — 삭제 없음)

Phase C 독립 재감사에서 확인된 69개 완전 빈 파일(디렉터리 내 모든
`.py` 파일이 0바이트) 스캐폴딩 도메인의 문서화다. **이번 작업에서
어느 것도 삭제하지 않는다.** 기능 구현으로도 계산하지 않는다(전부
0줄 코드). 분류는 디렉터리명에서 추정한 1차 의견이며, 최종 로드맵
편입 여부는 사용자 정책 결정 사항이다 — 별도 삭제 승인 프롬프트만
남기고 실행하지 않는다.

## 공통 사실

- 파일 수: 도메인당 대개 6~8개(`__init__.py`, `model.py`, `schema.py`,
  `repository.py`, `service.py`, `router.py`, 일부는 `gateway.py`/
  `adapters/` 등 추가)
- 전부 0바이트 확인(2026-08-13/14 재확인)
- 전부 main.py에 전혀 import되지 않음(등록도 주석도 없음)
- 삭제해도 아무 것도 깨지지 않음(참조 0건, 재확인 완료) — 단
  이번 Gate에서 삭제하지 않음

## V7 후보 (`inventory` 별도 문서 — 이미 `HOMEZ_V7_INVENTORY_PLAN.md`로 분리)

이 69개 중 V7(재고) 직접 관련 항목 없음 — inventory 자체는 이미
실코드가 있어(1092 loc) 이 빈 스캐폴드 목록에 포함되지 않는다.

## V8 이후 후보 (주문·결제·배송 워크플로우 — Section 14 판정에 따라 현재 범위 밖)

`order_item, payment, refund, return_order, shipping, webhook`

주문/결제/환불/배송 자체 처리 워크플로우가 실제로 제품 방향으로
확정되기 전까지는 착수하지 않는 것을 권고. inventory V7 계획의
"Live Gate 승인 조건"에도 이 그룹 중 최소 1개(반품 복구용)가
실제 구현돼야 한다는 의존관계가 명시돼 있다.

## V8 이후 후보 (외부 연동 인프라)

`oauth, api_key, sms, email` — 향후 외부 서비스(SMS/이메일 발송,
OAuth 로그인, 외부 API 키 관리)가 실제로 필요해질 때 착수. 현재
`notification_center`가 앱 내부 알림은 이미 담당하고 있어 외부
발송 채널만 추가되는 구조.

## V8 이후 후보 (자동화·백그라운드 처리 인프라)

`Automation, rule engine, scheduler, task, queue, job, event, event_bus`

"AI Commerce 운영" 단계(HOMEZ 목표 Flow의 마지막 단계)에서 자동화
규칙 엔진과 백그라운드 작업 큐가 실제로 필요해질 가능성이 높다.
다만 현재 `app/domains/automation_safety`(EStop/ExecutionLimit)가
이미 안전장치 역할을 하고 있어, 이 그룹을 실제로 만들 때는
`automation_safety`와의 경계를 명확히 설계해야 한다(중복 위험).

## V10 회계 연동 후보

`currency, exchange, statistics, report, price_history`

환율·통계·리포트·가격이력은 회계/재무 리포팅과 밀접하다. V10
회계 연동 시점에 함께 설계 권고.

## AI 기능 확장 이름 예약 추정 (착수 시점 미정)

`ai, ai_memory, prompt, keyword`

디렉터리명으로 보아 향후 AI 기능(추천/생성/기억) 확장을 위해
미리 이름만 예약해둔 것으로 추정된다. 실제 목적은 코드에서 확인
불가 — 사용자 확인 필요.

## 채널 매핑 후보 (멀티채널 카탈로그 표준화, V8 이후)

`category_mapping, brand_mapping, market_mapping`

여러 판매채널의 카테고리/브랜드 체계를 표준화하는 매핑 테이블로
추정. 현재 `coupang`/`marketplace_listing` 도메인이 채널별 필드
검증을 각자 스키마로 처리하고 있어, 이 그룹이 실제로 필요한지는
멀티채널 확장 시점에 재평가 권고.

## 제거 후보 (기존 기능과 명백히 중복되거나 현재 제품 방향에 없음)

- `dashboard` — 실제 대시보드는 이미 Desktop Console UI로 구현되어
  라이브 운영 중(A그룹). 이 스캐폴딩은 중복.
- `audit` — `audit_logs`(실제 운영 중)와 중복 가능성 높음, Gate R-7에서
  별도 심층 비교.
- `store` — `store_connection`(실제 운영 중, A그룹)과 이름 충돌.
- `notification` — `notification_center`(실제 운영 중, A그룹)와 이름
  충돌·부분일치(Phase C에서 substring 오탐으로 재확인된 바 있음 —
  실제 참조 관계는 없음).
- `customer, organization, tenant` — `user`/`company`(이미 존재하는
  핵심 엔티티)와 개념 중복 가능성 높음. 별도 필요성이 확인되지
  않는 한 제거 권고.
- `config, cache, monitor, system` — `app/core/config.py` 등 이미
  존재하는 코어 인프라와 중복.
- `file, media` — `media_asset`(실제 운영 중, A그룹)과 이름·역할
  중복 가능성.
- `Message Center, Push` — `notification_center`와 역할 중복 가능성.
- `translation` — 이미 별도 i18n 카탈로그 시스템(`app/web/i18n/`)이
  구축돼 있어 중복.
- `login_history, search_history, recent_view, favorite, tag, review,
  coupon, banner, source, version, websocket, workspace, license,
  crawler, ocr, extension, plugin` — 현재 HOMEZ 제품 방향(무재고
  중개·주문대행 AI Commerce OS)과 직접 연관성이 낮은 일반 쇼핑몰
  기능 세트로 추정. 삭제 검토 권고.

## 다음 단계 (실행하지 않음, 승인 프롬프트만)

이번 Gate에서 실제 삭제는 하지 않는다. 사용자가 위 "제거 후보"
목록을 검토·승인하면, 별도 세션에서 다음을 수행하는 정리 Gate를
제안한다:
1. 각 디렉터리의 최종 무참조 재확인(삭제 직전 재검증)
2. `git rm`으로 일괄 삭제(이번 세션 금지 사항 — 별도 승인 후)
3. `permission_catalog.py`의 대응 죽은 permission(예: `customer` 관련이
   있다면)도 함께 정리 검토
4. 전체 회귀 재실행으로 부작용 없음 확인
