# ADR 0004 — 공급처·발주 UI 역할별 권한 매트릭스 (V7 핵심 사용자 Workflow)

- 상태: **계획만 채택 — 신규 Permission 코드는 승인 대기(미구현)**
- 날짜: 2026-08-21
- 작성 배경: "HOMEZ V7 핵심 사용자 Workflow의 마지막 기능 구현" 지시
  작업 4 — 공급처 검색·연결·발주 UI의 역할별 접근을 설계하되,
  "기존 Permission 시스템을 먼저 확인하고 새 Permission 코드를
  임의로 만들지 않는다. 정말 새 Permission이 필요하면 코드/Migration을
  작성하지 말고 계획·매트릭스만 작성해 승인 대기로 둔다"는 명시적
  제약을 지킨다.

## Context

공급처 검색·연결·상품 연결·발주(발주안 생성/승인/전송/재시도/입고)
UI(`app/web/console.js`의 `view-supplier-sourcing`, `sps-*` 함수군)를
새로 추가하면서, 지시문은 다음 5개 역할이 각자 다른 수준으로
접근해야 한다고 요구했다.

- **SUPER_ADMIN/ADMIN** — 공급처 연결 승인, 발주 승인·전송·재시도.
- **MANAGER** — 발주안을 검토한다("기존 Permission 계약 범위 안에서").
- **STAFF** — 허용된 조회·입력만.
- **VIEWER** — 읽기 전용.

이 저장소의 기존 권한 체계는 두 층으로 나뉜다.

1. `app/core/authorization.py::UserRole`(역할 계층 — SUPER_ADMIN/
   ADMIN/MANAGER/STAFF/SELLER/CUSTOMER/GUEST) 기반의 `admin_guard`
   등 — **"VIEWER"라는 UserRole 멤버 자체가 없다.**
2. `app/domains/role_permission`(세부 Permission grant — `Role.code`
   로 시딩된 실제 DB 역할, `has_permission()`으로 조회) — 여기의
   `seed_role_permission.py::ROLE_PERMISSION_MAP`에는 실제로
   `"VIEWER"` 코드의 역할 행이 있고, MANAGER/STAFF/VIEWER 전부
   `"SUPPLIER_VIEW"` Permission을 이미 부여받고 있다(2026-08-21
   이전부터 존재 — 단, 이번 라운드 전까지 이 코드를 실제로 소비하는
   Guard가 어디에도 없어 "고아 상태"였다).

즉 **"VIEWER 역할이 읽기 접근한다"는 요구사항은 UserRole 계층
경로로는 원천적으로 불가능하고(그런 역할이 없다), `has_permission()`
grant 경로로만 가능하다** — 이것이 이번 설계의 핵심 제약이다.

## Decision — 지금 구현한 것 (코드 변경 완료)

`SourcingViewGuard`/`PurchaseViewGuard`(둘 다
`ListingWizardPermissionGuard("SUPPLIER_VIEW")` — 기존 코드 재사용,
신규 Permission 없음)로 아래 **조회(읽기) 계열** 엔드포인트를
admin_guard에서 SUPPLIER_VIEW grant 기반으로 넓혔다.

| 엔드포인트 | 이전 | 이후 |
|---|---|---|
| `POST /sourcing/search` | (신규) | SourcingViewGuard |
| `GET /sourcing/suppliers/public` | (신규) | SourcingViewGuard |
| `GET /sourcing/order-items/out-of-stock` | (신규) | SourcingViewGuard |
| `GET /sourcing/relations`(레닥션 포함) | admin_guard | SourcingViewGuard |
| `GET /sourcing/candidates/{id}/links` | admin_guard | SourcingViewGuard |
| `GET /sourcing/candidates/{id}/best-link` | admin_guard | SourcingViewGuard |
| `GET /purchases`, `GET /purchases/{id}`, `GET /purchases/{id}/items` | admin_guard | PurchaseViewGuard |

`ListingWizardPermissionGuard`는 ADMIN/SUPER_ADMIN을 무조건 통과시키고
(has_permission() 조회 결과와 무관), 그 외 역할은 SUPPLIER_VIEW
grant가 있어야만 통과한다 — `ROLE_PERMISSION_MAP`에 이미 MANAGER/
STAFF/VIEWER 전부 SUPPLIER_VIEW가 시딩돼 있으므로, **코드 한 줄도
추가하지 않고 세 역할 모두 읽기 접근이 즉시 동작한다**(실제
`tests/test_sourcing_ui_backend_gates.py::SourcingViewGuardTestCase`
로 검증 완료).

`app/domains/source/router.py::_redact_relation_for_viewer()`가
ADMIN/SUPER_ADMIN이 아닌 조회자에게는 `CompanySupplierRelation`의
계약 연락처(`contact_name/contact_phone/contact_email`)·
`payment_terms`·`credential_reference`·`notes`를 응답에서 null로
가린다 — MANAGER/STAFF/VIEWER는 "공급처가 연결돼 있고 승인 상태가
무엇인지"는 보되, 계약 조건·Credential 참조는 보지 못한다.

## Decision — 승인 대기(미구현, 이번 라운드에 코드/Migration 작성 안 함)

**쓰기(승인/전송/재시도) 계열 엔드포인트는 전부 여전히 `admin_guard`
(ADMIN/SUPER_ADMIN 하드 역할 계층)로만 막혀 있다** — 이번 라운드는
이 부분을 바꾸지 않았다.

| 엔드포인트 | 현재 Guard | MANAGER "검토" 요구사항 충족? |
|---|---|---|
| `POST /sourcing/relations`(연결 생성) | admin_guard | 아니오 |
| `PATCH /sourcing/relations/{id}/approval` | admin_guard | 아니오 |
| `POST /sourcing/links`(상품 연결 생성) | admin_guard | 아니오 |
| `PATCH /sourcing/links/{id}/deactivate` | admin_guard | 아니오 |
| `POST /purchases`(발주안 생성) | admin_guard | 아니오 |
| `POST /purchases/{id}/confirm`(발주 확정) | admin_guard | 아니오 |
| `POST /purchases/{id}/submit`(공급처 전송) | admin_guard | 아니오 |
| `POST /purchases/{id}/submit/retry`(재시도) | admin_guard | 아니오 |
| `POST /purchases/{id}/receive`(입고) | admin_guard | 아니오 |
| `POST /purchases/{id}/cancel`(취소) | admin_guard | 아니오 |
| `POST /sourcing/order-items/{id}/purchase-proposal`(발주안 자동생성) | admin_guard | 아니오 |

**왜 이번 라운드에 바꾸지 않았는가**: `permission_catalog.py`에는
`SUPPLIER_VIEW/CREATE/UPDATE/DELETE`만 존재한다 — 이들은 전역
`Supplier` 카탈로그(이름/연락처 등 마스터 데이터)에 대한 CRUD
권한이지, "이 회사의 발주안을 검토·승인할 수 있다"는 의미를 가진
Permission이 아니다. `SUPPLIER_VIEW`를 승인/전송에도 재사용하면
"조회만 가능해야 할 STAFF/VIEWER까지 발주를 승인·전송할 수 있게
되는" 권한 상승 결함을 만든다 — 절대 하지 않는다. 즉 "MANAGER가
발주안을 검토할 수 있게 하려면" 최소한 다음 중 하나가 필요하다.

1. 새 Permission 코드 `PURCHASE_APPROVE`(가칭) — `permission_catalog.py`
   에 추가 + `role_permissions` 시딩(MANAGER에게만 부여, STAFF/VIEWER
   에는 부여하지 않음) + 관련 Migration.
2. 또는 `PurchaseApprovalGuard = ListingWizardPermissionGuard(
   "PURCHASE_APPROVE")`를 만들어 `confirm_purchase`/`submit_purchase_
   to_supplier` 등 승인·전송 액션에만 적용(입고/취소/공급처 관계
   승인은 여전히 admin_guard 유지 — "검토"와 "실제 자금 지출을
   수반하는 최종 전송/입고"는 다른 위험도로 별도 취급하는 편이
   안전하다는 것이 이 설계의 권고).

둘 다 코드·Migration 작성이 필요해 이번 라운드의 "무단 Permission
코드 생성 금지" 제약에 걸린다 — **그래서 여기까지만 계획으로
남기고, 실제 반영은 별도 승인 이후로 미룬다.**

## 제안 매트릭스(승인 시 반영할 목표 상태)

| 역할 | 공급처 검색·조회 | 공급처 연결 승인 | 상품 연결 생성 | 발주안 생성·검토 | 발주 확정 | 공급처 전송·재시도 | 입고·취소 |
|---|---|---|---|---|---|---|---|
| SUPER_ADMIN/ADMIN | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| MANAGER | ✅(기존) | ❌ | ❌ | ✅(신규 `PURCHASE_APPROVE` 필요) | ✅(신규 `PURCHASE_APPROVE` 필요) | ❌ | ❌ |
| STAFF | ✅(기존) | ❌ | ❌ | ❌(읽기만) | ❌ | ❌ | ❌ |
| VIEWER | ✅(기존) | ❌ | ❌ | ❌(읽기만) | ❌ | ❌ | ❌ |

이 표의 "✅(기존)" 칸은 이미 이번 라운드에 구현·테스트 완료된 것이고,
"신규 `PURCHASE_APPROVE` 필요" 칸은 위 결정에 따라 **코드를 작성하지
않은 계획 상태**다.

## 승인 후 예상 작업 범위(참고용 — 승인 전에는 착수하지 않음)

1. `permission_catalog.py`에 `PURCHASE_APPROVE` 추가.
2. `seed_role_permission.py::ROLE_PERMISSION_MAP["MANAGER"]`에 추가
   (STAFF/VIEWER에는 추가하지 않음).
3. 신규 Migration(additive, `role_permissions`/`permissions` INSERT만
   — 기존 Gate 방식과 동일하게 SQL 파일로 작성).
4. `app/domains/purchase/router.py::confirm_purchase`/
   `submit_purchase_to_supplier`의 Guard를
   `PurchaseApprovalGuard = ListingWizardPermissionGuard(
   "PURCHASE_APPROVE")`로 교체(단, `receive_purchase`/`cancel_purchase`
   /`retry_purchase_submission`/공급처 관계 승인은 이 ADR의 권고에
   따라 admin_guard 유지 여부를 승인 시점에 재확인).
5. 신규 테스트: MANAGER가 PURCHASE_APPROVE grant로 confirm 가능,
   STAFF/VIEWER는 grant가 있어도 UserRole 계층상 다른 문제가 없는지
   재검증, 회사 격리 재확인.

## 결론

이번 라운드는 **읽기(SUPPLIER_VIEW 재사용) 범위만 실제로 반영**했고,
MANAGER의 "발주안 검토" 요구사항 중 승인 액션 부분은 신규 Permission
코드 생성 없이는 안전하게 충족할 수 없음을 확인해 계획으로만
남긴다. MANAGER는 현재 STAFF/VIEWER와 동일하게 "조회만" 가능하다 —
이는 기능 축소가 아니라, 지시문의 "새 Permission 코드는 임의로
만들지 않는다"는 제약을 지키기 위한 의도된 보수적 선택이다.

## 추가 — 2026-08-21 5차 지시(역할별 상품 후보 조회 결함 수정)

앞선 라운드 완료 후 독립 재검증에서 "상품 연결·비교" 탭의 상품 후보
드롭다운이 기존(이 ADR의 범위 밖) `GET /product-candidates`
admin_guard 엔드포인트에 의존해, VIEWER/STAFF/MANAGER가 SUPPLIER_VIEW
grant가 있어도 그 탭에서 "권한 없음" 오류를 만나는 결함이 실제로
확인됐다(잔존 위험으로 이미 기록돼 있던 항목). 이번 라운드에서
`app/domains/product_candidate/router.py`의 admin_guard 자체는
그대로 두고(그 파일은 여전히 이 ADR/이번 작업의 Whitelist 밖), 대신
**sourcing 경계 전용의 신규 최소 조회 엔드포인트**
`GET /sourcing/product-candidates`(`SourcingViewGuard` 적용,
`app/domains/source/router.py::list_sourcing_product_candidates`)를
추가해 해결했다 — 기존 admin 전용 엔드포인트의 권한을 낮추지 않고,
이 화면이 실제로 필요로 하는 `id/product_name/brand/category/status/
is_approved` 6개 필드만 새로 노출한다(원가·마진·내부 점수·타 회사
정보는 이 응답 모델 자체에 필드가 없어 노출할 수 없다).

같은 라운드에서 `SourceService.create_link()`가 "승인된 후보만
공급처 상품 연결 대상으로 선택할 수 있게 한다"는 요구사항을 실제로
강제하도록 변경됐다 — `ProductCandidateService.require_approved_
for_company()`(coupang/marketplace_listing 등이 이미 공유하는 단일
승인 게이트)를 재사용해, 이 회사가 아직 결정하지 않았거나 HELD/
REJECTED인 후보는 400으로, 다른 회사의 PRIVATE 후보는 404로 차단한다.
프런트엔드 드롭다운은 `GET /sourcing/product-candidates?approved_
only=true`를 호출해 애초에 승인된 후보만 목록에 노출한다.

이 추가는 위 매트릭스의 "MANAGER 발주 승인" 칸(신규 `PURCHASE_APPROVE`
Permission 필요, 여전히 계획만)과는 무관한 별도 결함 수정이다 — 신규
Permission 코드는 이번에도 만들지 않았다(기존 SUPPLIER_VIEW만 재사용).
