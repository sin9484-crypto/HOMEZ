# HOMEZ V7 — Inventory 편입 계획 (Gate R-8, 계획 전용)

이 문서는 계약·설계·Gate 승인 조건만 정의한다. **이번 작업에서 실제
외부 재고 동기화, 실제 Migration 적용, 실제 라우터 마운트는 하지
않는다.** `app/domains/inventory`(현재 1092 loc, endpoint 7개, main.py
미등록, 참조하는 곳은 마운트 해제된 레거시 `order`/`product`뿐 —
Phase C 독립 재감사에서 확인됨)를 V7 핵심 범위로 편입하기 위한
착수 전 계약서다.

## 1. 편입 배경

HOMEZ 목표 Flow(`CLAUDE.md`): "상품 분석 → 판매 판단 → 공급처 판단
→ 주문 자동화 → **재고** → 배송 → 운영자금 → 정산". 재고는 로드맵상
필수 개념이지만, 현재 `inventory` 도메인은 pivot 이전(order/product
레거시) 구조에 종속돼 있어 그대로 재사용할 수 없다. V7은 이 도메인을
**기존 legacy order/product와 분리된 새 이벤트 계약**으로 재설계한다.

## 2. 재고 원장(Inventory Ledger) 계약

append-only 원장 패턴(이 코드베이스의 `MarketplaceListingStatusEvent`,
`DecisionAuditLog` 등과 동일 철학 — 상태를 직접 덮어쓰지 않고 이벤트를
쌓아 현재 상태를 파생시킨다).

핵심 필드(안):
- `id`, `company_id`(격리 필수), `product_candidate_id` 또는 향후
  정식 SKU 식별자
- `event_type`: `RESERVED` / `RELEASED`(예약 취소) / `CONSUMED`(주문
  확정으로 소모) / `RESTOCKED` / `ADJUSTED`(수동 조정, 사유 필수) /
  `CHANNEL_SYNC`(외부 채널 재고 반영)
- `quantity_delta`(정수, 방향 포함)
- `available_after`, `reserved_after`(이벤트 적용 후 스냅샷 — 조회
  성능용, 소스오브트루스는 이벤트 누적)
- `safety_stock_threshold`(회사별 설정 가능한 안전재고선)
- `channel_code`(nullable — 채널별 재고인 경우)
- `idempotency_key`(UNIQUE, company 범위 — 이 코드베이스 전역 관례)
- `triggered_by`, `created_at`, `reason`(ADJUSTED일 때 필수)

## 3. 상태 파생

`available = 최초 입고 - RESERVED 누적 + RELEASED 누적 - CONSUMED 누적
+ RESTOCKED 누적 + ADJUSTED 누적`. `reserved`는 현재 유효한 RESERVED
이벤트 중 아직 RELEASED/CONSUMED로 정산되지 않은 합. 이미 이 코드베이스
전역에서 검증된 append-only+조건부 상태전이 패턴(`marketplace_listing`
도메인 참고)을 그대로 재사용한다 — 새 패턴을 발명하지 않는다.

## 4. 주문 예약·취소·반품 복구 계약

- 주문 생성 시도 → `RESERVED` 이벤트(가용재고 부족 시 fail-closed로
  차단, 0으로 자동 보정하지 않음 — `CLAUDE.md`의 "금융 오류 자동
  보정 금지"와 동일 철학을 재고에도 적용).
- 주문 취소 → `RELEASED` 이벤트(원래 RESERVED와 idempotency로 짝지어
  중복 해제 방지).
- 반품 승인 → `RESTOCKED` 이벤트(반품 도메인이 실제로 생기기 전까지는
  이 경로 자체가 비활성 — 현재 `refund`/`return_order`는 완전 빈
  스캐폴딩이므로 이 계약은 그 도메인들이 실제 구현될 때 함께
  연결된다).

## 5. 동시성·idempotency

- `RESERVED` 이벤트 생성은 가용재고 확인 + 이벤트 삽입을 단일
  트랜잭션 안에서 수행(이 코드베이스의 conditional-update 낙관적
  동시성 패턴 재사용).
- 같은 주문 시도가 재시도돼도 idempotency_key로 중복 예약 차단.
- 여러 채널에서 동시에 같은 SKU를 판매 시도할 때(멀티채널 동시
  판매) 경쟁 조건이 발생할 수 있음 — `marketplace_listing_status_sync`
  도메인에서 이미 검증된 동시성 테스트 패턴(barrier 기반 2-스레드
  경쟁 시나리오)을 그대로 재사용해 검증한다.

## 6. 품절·채널별 실패·재시도

- 가용재고 0 도달 시 해당 채널 리스팅을 자동으로 "품절" 상태로
  전환할지, 수동 개입을 요구할지는 **제품 정책 결정 필요**(옵션:
  A. 자동 품절 처리 후 재입고 시 자동 재개, B. 자동 품절만 하고
  재개는 수동, C. 완전 수동).
- 채널별 재고 동기화 실패(외부 API 오류)는 이미 확립된
  `listing_status_sync`의 오류 분류·재시도·Rate Limit 패턴
  (`retry_after` 정규화, 429 처리 등)을 그대로 재사용한다 — 새로
  설계하지 않는다.

## 7. EStop·안전장치

`app/domains/automation_safety`(SafetyService, Emergency Stop)가
이미 marketplace_listing 제출 경로에 연결돼 있다 — Inventory의
자동 재고 조정/자동 채널 동기화도 동일한 EStop 게이트를 거치도록
설계한다(자금 한도와 마찬가지로 "재고 급변" 한도를 ExecutionLimit에
추가하는 방식 검토, 실제 추가는 별도 승인 후).

## 8. 회사 격리·감사 로그

모든 이벤트에 `company_id` 필수, 조회 API도 company 범위로 스코프.
`ADJUSTED`(수동 조정) 이벤트는 `audit_logs`에도 함께 기록(재고
직접 조정은 금융 조정과 유사한 민감도로 취급).

## 9. 외부 API 경계

쿠팡/네이버 등 채널의 재고 동기화는 `store_connection`의 기존
어댑터 패턴(Fake Provider로 먼저 검증 후 실제 Provider 연결)을
재사용한다. **실제 외부 재고 동기화 API 호출은 이번 Gate뿐 아니라
V7 착수 초기 단계에서도 실행하지 않는다** — Fake Provider 기반
계약 검증부터 시작한다.

## 10. Migration 계획

신규 테이블 1개(`inventory_ledger_events`, 위 2절 필드) + 회사별
안전재고 설정을 위한 컬럼 확장(선택). **이번 Gate에서 작성만 하고
임시 SQLite DB에서만 리허설한다. 실제 homez.db 적용은 하지 않는다**
(별도 Live Gate 승인 필요).

## 11. 임시 DB 테스트 계획

- 재고 원장 append-only 불변성(과거 이벤트 수정 불가)
- RESERVED 시 가용재고 부족 시 차단(fail-closed)
- RELEASED/CONSUMED 짝짓기 무결성
- 동시성(barrier 기반 경쟁 시나리오)
- idempotency 중복 차단
- 회사 격리(회사 A/B 교차 접근 차단)
- ADJUSTED 이벤트의 audit_logs 연동

## 12. V7 Live Gate 승인 조건

다음이 모두 충족돼야 실제 homez.db 적용 승인을 요청할 수 있다:
1. 위 임시 DB 테스트 전부 통과
2. `refund`/`return_order`/`shipping` 중 최소 하나가 실제로 구현되어
   반품 복구 경로가 실제로 연결 가능한 상태(Inventory 단독으로는
   반품 복구 시나리오를 완결할 수 없음 — Section 14 판정과 일관)
3. 품절 처리 정책(위 6절 A/B/C) 사용자 결정 완료
4. 외부 채널 재고 동기화는 Fake Provider 검증만으로 우선 출시,
   실제 Provider 연결은 별도 승인
5. 전체 회귀 0 실패
