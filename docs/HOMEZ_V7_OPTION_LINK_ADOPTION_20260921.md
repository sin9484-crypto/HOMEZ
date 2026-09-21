# HOMEZ V7 — 옵션 연결 채택 준비 · 화면 · 식별자 연결 (2026-09-21, 2차)

> 상태: **채택 후보 완성 · 격리 검증 완료 · Model·Migration·코드 채택은 사용자 승인 대기.**
> 이 문서와 함께 커밋되는 것은 문서와 패치 파일(`docs/proposals/20260921_supplier_option_link.patch`)뿐이다.
> `app/`·`migrations/`의 운영 코드에는 아직 아무것도 반영하지 않았고, 실제 `homez.db`는 열지 않았다.
> 이전 라운드 설계는 `docs/HOMEZ_V7_OPTION_LINK_DESIGN_20260921.md` — 이 문서의 패치는 그 패치(v1)를
> **대체**한다(둘 다 HEAD `f6fb44c`에 단독 적용되며 함께 쓰지 않는다).

## 1. 기준선·승인 확인

| 항목 | 값 |
|---|---|
| 원격 / 로컬 HEAD | `f6fb44c` / `f6fb44c` (동일, 작업 트리 깨끗) |
| 고정 기준선 전체 회귀 | a32623e 기준 4,563 실행 / 4,556 성공 / 실패·오류 0 / skip 7 (이후 SKU 수정 테스트 6건만 추가) |
| Model·Migration 채택 승인 | **확보되지 않음** — `docs/HOMEZ_DECISIONS.md`와 대화 기록에 승인 문구 없음. 이번 지시는 "채택 준비"이며 승인 여부를 확인하라고만 했다 |
| 처리 | 실제 저장소에는 스키마·운영 코드를 반영하지 않고, 실제 작업 트리 바이트를 복사한 격리 작업본(`w6`)에서 구현·검증했다. 코드 채택 승인과 실제 업무 DB 적용 승인은 §9에서 분리해 요청한다 |

## 2. 두 기능 판정 (근거 연결)

**A. 옵션 등록(판매 옵션을 쿠팡에 등록)** — 등록 자체는 이미 구현이었고, 이번에 "등록 결과를 연결에 붙이는" 마지막 조각을 채웠다.

| 조각 | 판정 | 근거 |
|---|---|---|
| 옵션 입력·SKU 검증·쿠팡 전송·결과불명 보존 | 구현 | `71f2b0e`(SKU 검증), `marketplace_submissions` 상태기계 |
| 등록 후 옵션별 쿠팡 식별자(`vendorItemId`) 읽기 | **구현(채택 후보)** | `CoupangLiveProductProvider.get_product_option_identifiers()` — 공식 문서 계약 기준, 계약 테스트 9건 |
| 읽은 식별자를 판매 옵션 연결에 저장 | **구현(채택 후보)** | `SupplierOptionLinkService.attach_coupang_identifiers()` — SKU로만 매칭 |
| 화면에서 "쿠팡 옵션번호 확인" 실행 | **구현(채택 후보)** | 상품 준비 10단계 결과 → "옵션 연결" 패널 → 확인창(GET 1회 명시) → `POST .../option-links/sync-identifiers` |
| 실제 쿠팡 응답으로 검증 | **미검증** | 실제 호출은 하지 않았다(§9 승인 항목). 계약은 공식 문서로만 확인 |

**B. 옵션 연결(주문된 옵션 ↔ 온채널 상품코드·옵션ID 영구 저장·재사용)** — 채택 후보로 완성.

| 조각 | 판정 | 근거 |
|---|---|---|
| 저장 구조 | 구현(채택 후보) | 신규 테이블 `supplier_option_links`(UNIQUE 2개: SKU, 쿠팡 옵션번호) |
| 두 식별자(판매자 SKU / 옵션번호) 분리 해석 | 구현(채택 후보) | `_resolve_link()` — §4 결정표 |
| 준비 완료 판정 | 구현(채택 후보) | 서버가 확정한 선택 옵션 전체 기준 — §5 |
| 화면 | 구현(채택 후보) | 매입 검토 패널 + 상품 준비 결과 패널 — §6 |
| 실제 업무 DB 적용 | **미적용** | 별도 승인 대상 |

화면 → API → 저장 → 주문 → 검토 → 승인의 연결:

```
[상품 준비 10단계 결과] 옵션 연결 패널 ─ GET  /listing-wizards/{w}/submissions/{s}/option-links   (읽기, 서버가 옵션 범위 확정)
      ├ "연결 만들기/변경" ─ PUT  …/option-links            → SupplierOptionLinkService.save_link  (공급 상품 실조회 후 저장)
      └ "쿠팡 옵션번호 확인" ─ POST …/option-links/sync-identifiers → provider.get_product_option_identifiers (GET 1회) → attach (SKU 매칭)
                                                   │ supplier_option_links (회사·판매계정·SKU / 쿠팡 옵션번호 / 매입계정·공급 상품·옵션 / 구성수량)
[주문 수집] externalVendorSkuCode 또는 vendorItemId → UnresolvedOrderItem(원본 옵션번호 보존) → OrderItem.channel_sku(합쳐진 값)
      ↓
[매입 검토 화면] 옵션 연결 패널 ─ GET/PUT /purchase-tasks/{id}/supplier-option-link  (서버가 판매계정·SKU를 주문에서 결정)
      ↓ 저장된 연결로 검토 입력칸 미리 채움
[발주 검토] build_order_submission_review → supplier_link 반영(기대 구성 = 판매 수량 × 구성 수량, 차단 사유)
      ↓
[최종 승인] finalize_approval → approval_block_reason 서버 재대조(옵션 구성 생략도 불가) → 승인 스냅샷 동결
```

연결은 발주·결제 승인이 아니다. 가격·재고·배송비·한도·판매신청·최종 승인은 기존 정책 그대로 매번 확인한다
(E2E에서 저장된 연결이 있어도 "판매신청 미확인"·"배송비 직접 확인 전 차단"이 그대로 남는 것을 확인).

## 3. 공식 문서 확인 결과 (2026-09-21 조회, 쿠팡 개발자센터)

- 상품 조회 [`GET /v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sellerProductId}`](https://developers.coupang.com/ko/api/products/querying-product):
  응답 `code`/`message`/`data`, `data.items[]`에 `sellerProductItemId`·`vendorItemId`·`itemName`·`externalVendorSku`.
  **`vendorItemId`는 "임시저장 상태일 경우 null, 상품 승인완료 시 값이 표시된다."** → 생성 직후·승인 전·부분 승인 상태에서는
  옵션 번호가 없을 수 있다. 문서 표기가 `vendorItemId`를 number로도, string으로도 적어 두 타입을 모두 받아 문자열로 통일한다.
  `statusName`: 심사중/임시저장/승인대기중/승인완료/부분승인완료/승인반려/상품삭제. 호출 제한(rate limit)은 문서에 없음.
- [ID 정의](https://developers.coupang.com/ko/faq/i-dont-understand-what-each-id-means): `sellerProductId`(상품 생성 시 부여, 불변),
  `vendorItemId`(가장 작은 단위, 불변, 주로 key), `sellerProductItemId`(상품 조회 API로 얻음), `externalVendorSku`("상품 생성 시 업체가
  넣는 관리용 코드 — 발주서 조회 시 포함").
- [발주서 조회](https://developers.coupang.com/ko/api/shipments/po-list-query-paging-by-day): `orderItems[].vendorItemId`(number), `externalVendorSkuCode`(string, **optional**),
  `sellerProductId`, `sequenceNo`(string).
- 결론: 생성 응답에는 `sellerProductId`뿐이라 옵션 번호는 상세조회로만 얻고, 주문의 판매자 SKU는 비어 올 수 있으므로 `vendorItemId`
  대응 경로가 필요하다. 이 문서 조회는 웹 페이지 읽기이며 쿠팡 API 호출이 아니다. 요약 도구로 읽은 내용이라
  **필드 타입 표기가 엇갈린 부분은 양쪽을 허용**하도록 구현했고, 최종 응답 형태는 실제 조회에서만 확정된다.

## 4. 식별자 해석 — 두 식별자를 같은 문자열 공간에서 비교하지 않는다

실제 코드: 주문 수집은 `channel_sku = externalVendorSkuCode.strip() or vendorItemId`로 둘을 **합쳐** `OrderItem.channel_sku`에
넣는다(`coupang_normalizer.py`). 원본 `vendorItemId`는 `UnresolvedOrderItem.vendor_item_id`에 따로 남고
`resolved_order_item_id`로 `OrderItem`과 이어진다 — 여기서 되찾아 SKU 경로와 옵션번호 경로를 각각 조회한다.

| 주문 상황 | 조회 결과 | 판정 |
|---|---|---|
| SKU 있음, SKU 연결만 있고 옵션번호 미확인 | SKU 경로 | 사용(ACTIVE) |
| SKU 있음, SKU 연결의 저장 옵션번호 = 주문 옵션번호 | 둘 다 같은 연결 | 사용(둘 다 일치) |
| SKU 없음(또는 옵션번호와 같은 문자열), 옵션번호로 확인된 연결 있음 | 옵션번호 경로 | 사용(옵션번호로 찾음) |
| SKU 없음, 확인된 옵션번호 연결 없음 | 없음 | 연결 없음 — 임의 매칭 안 함, 새 연결도 여기서 만들지 않음 |
| SKU 연결과 옵션번호 연결이 **서로 다른** 연결 | — | **식별자 충돌 — 차단** |
| SKU 연결에 저장된 옵션번호 ≠ 주문 옵션번호 | — | **식별자 충돌 — 차단** |
| 옵션번호로 찾은 연결의 SKU ≠ 주문 SKU | — | **식별자 충돌 — 차단** |
| SKU 없음인데 옵션번호와 같은 문자열의 SKU 연결이 있음 | — | **출처 불명 — 차단** |
| 수집 원본을 못 찾음(옛 주문) | SKU 경로 | 사용(옵션번호 교차검증 불가) |

DB 보장: `UNIQUE(company_id, store_connection_id, channel_sku)`와 `UNIQUE(company_id, store_connection_id, coupang_vendor_item_id)` —
같은 회사·같은 판매 계정 안에서 옵션번호 하나는 정확히 한 연결에만 붙는다(NULL=미확인은 여러 개 허용).
상품명·옵션 배열 순서로 매칭하는 코드는 없다.

## 5. 등록 결과 연결과 준비 완료 판정

**식별자 부착(SKU로만)**: 조회 결과의 `externalVendorSku`로만 기존 연결에 붙인다(응답 순서·이름 무시). 옵션별 결과:
`ATTACHED`/`ALREADY_ATTACHED`(성공), `ID_NOT_ISSUED`(승인 전 번호 없음), `ID_NOT_RETURNED`(이미 확인된 번호가 있는데 이번 조회에는 없음 —
정보 없음이지 충돌이 아니므로 저장값 유지), `NO_LINK`, `MISSING_IN_RESPONSE`, `CONFLICT`(서로 다른 두 값 — 덮어쓰지 않고 연결을 재확인 필요로 내림),
응답 자체가 모호하면(SKU·옵션번호 중복) `AMBIGUOUS_RESPONSE`로 아무것도 저장하지 않는다. 하나라도 성공이 아니면 `complete=false`.

**준비 완료(`all_ready`)**: 다음이 모두 참일 때만 참이다 — ① 서버가 확정한 선택 옵션 전체 범위, ② 그 옵션 전부가 ACTIVE 연결,
③ 그 옵션 전부의 쿠팡 옵션번호 확인, ④ 쿠팡 등록이 확인됨(SUBMITTED + 상품 ID).
- 범위의 서버 근거: 제출에 묶인 동결된 판매 방식(`MarketplaceFulfillmentSelection.required_fields_json.items[]`, 스키마 검증·fingerprint 고정).
  위저드 입력이 그 뒤 달라졌거나 판매 계정을 특정하지 못하면 "범위 미확정"으로 내려 준비 완료가 되지 않는다.
- 클라이언트가 SKU 목록을 보내는 경로(`POST /supplier-option-links/readiness`)는 "선택 범위 확인"만 하며 `all_ready`는 항상 false다.
- 옵션 하나라도 연결 누락·충돌·재확인 필요·번호 미확인이면 전체 준비 완료로 표시하지 않는다.

**외부 등록 성공 후 내부 저장 실패**: 기존 설계대로 제출은 `SUBMITTING`에 남아 자동 재시도하지 않는다(재등록 없음). 상품 ID가 없으면
옵션 식별자 조회를 거부하고("결과를 모르는 등록은 다시 등록하지 말고 정합화로 먼저 확인"), 상품 ID가 확인된 뒤(기존 정합화 경로)에는
조회를 몇 번이든 안전하게 다시 실행할 수 있다(멱등). 식별자 부착 중 저장이 실패하면 전부 롤백되고 재실행으로 복구된다.

## 6. 콘솔 화면

- **매입 검토(주문 단위)**: 판매 옵션(SKU·주문 옵션번호) / 연결 상태 / 공급 상품·옵션·구성 수량 / 쿠팡 옵션번호 확인 여부 + 찾은 근거.
  연결이 있으면 검토 입력칸(상품코드·옵션ID·수량 = 판매 수량 × 구성 수량)을 **비어 있을 때만** 미리 채운다. 없으면 "매입 계정 → 공급 상품코드 →
  옵션 조회 → **조회 결과에서 옵션 선택** → 구성 수량 → 저장" 순서의 폼. 옵션은 자유 입력이 아니라 조회 결과 `<select>`이며 상품코드·매입 계정이
  바뀌면 이전 조회 결과가 무효화된다. 연결 변경(ACTIVE 교체는 확인창)·해제·재확인은 명시적 버튼이다. 저장 요청에는 판매 계정·SKU·매입 계정이 없다(서버가 정함).
- **상품 준비 결과(등록 제출 단위)**: 서버가 확정한 옵션 전체 표(판매 옵션 / 공급 상품·옵션 / 구성 수량 / 연결 상태 / 쿠팡 옵션번호 / 동작), "자동 매입 준비
  완료/미완료 + 사유", "쿠팡 옵션번호 확인"(확인창에 "읽기 전용 1회, 등록은 다시 하지 않음" 명시), 충돌 옵션의 "쿠팡 번호 초기화".
- 모바일: 표는 카드형으로 바뀌고 입력 폼은 화면 안에 들어온다(E2E에서 두 결함을 찾아 수정 — §7).

## 7. 검증 (격리 — 실제 쿠팡·온채널·Credential Manager·`homez.db` 미접촉)

**테스트**(채택 후보 `dc97d1f`): 신규/재작성 모듈 6개 105건 —
`test_supplier_option_link_service`(46: 저장·재사용·격리·잘못된 연결 방지·부착·준비·동시성·재시작·미적용 폴백·Migration),
`test_supplier_option_identifier_paths`(23: 식별자 결정표 전부, 주문 단위 저장, 동시 부착),
`test_listing_wizard_option_links`(15: 서버 확정 범위, 등록 결과 부착, 부분·미발급·충돌·결과불명·내부 저장 실패·회사 격리·권한),
`test_coupang_option_identifiers_provider`(9: 공식 계약), `test_option_link_console_ui`(6: 화면↔서버 코드 문구 계약), `test_coupang_option_sku_contract`(6: 이전 커밋).
- **변이 검증 10건**(핵심 가드를 각각 망가뜨림): 전부 해당 테스트가 실패로 검출, 원본은 바이트 동일 복원.
- 옵션 연결 관련 163건(위 + `test_i18n`·`test_permissions_timestamps_migration`) 전부 통과 — 이전에 새 복제본에서 실패하던 체크섬 2건은
  **바이트 복사본에서는 통과**(아래 §8 원인).

**브라우저 E2E**(임시 DB + 실제 Migration Runner 신규 설치 + 가짜 쿠팡·온채널, 소켓 가드 — 외부 접속 시도 0건):

| 시나리오 | 결과 |
|---|---|
| 첫 주문: 연결 없음 → 조회 → 옵션 선택 → 저장 → 검토 입력칸 미리 채움(수량 2) → 검토(금액 2×5,000) | 통과. 판매신청·배송비 기존 차단은 그대로 |
| 같은 옵션 두 번째 주문(수량 3): 재입력 없이 자동 채움 | 통과 |
| 판매자 SKU 없이 들어온 주문: 확인된 옵션번호로 찾음 | 통과("쿠팡 옵션번호로 찾음") |
| 공급 옵션 이름 변경 / 옵션 삭제 | 검토 차단 + "재확인 필요", 대체 없음. 재확인 저장 후 복귀 |
| 등록 결과: 승인 전(번호 null) → 승인 후(번호 발급, **응답 순서 뒤집힘**) | 미발급/저장값 유지 구분 → SKU로만 정확히 부착 |
| 저장된 번호와 다른 번호가 조회됨 | 충돌 → 재확인 필요, 덮어쓰기 없음 → "쿠팡 번호 초기화" → 재조회 → 재확인 → **자동 매입 준비 완료** |
| 확인된 번호와 주문 옵션번호 불일치 | 주문 화면 "식별자 충돌" 차단, 저장 폼 없음 |
| 서버 재시작 후 | 연결·상태·사유 유지 |
| 모바일 375×812 | 저장→재조회→검토 통과, 가로 넘침 없음 |

**E2E가 찾아 고친 결함 4건**: ① 이미 확인된 옵션번호가 있는 옵션에 대해 이번 조회가 번호를 안 돌려준 것(정보 없음)을 충돌로 오판해 연결을 막던 것 →
저장값 유지·"이번 조회에는 없음"으로 보고. ② 옵션 연결 패널이 결과 표 셀 안에 있어 모바일에서 화면 밖(x=451)으로 밀림 → 표 아래 전체 폭으로 이동.
③ 모바일 입력칸 6px 넘침 → 범위 한정 CSS. ④ SKU 없는 주문에서 같은 값이 두 번 표시 → "판매자 SKU 없음"으로 표시.

## 8. Migration

신규 1개: `migrations/20260921_00_create_supplier_option_link_schema.sql`(SHA-256 `aebbd3fd…9884`) — 테이블 1개 추가, 기존 데이터 무변경. 이전(v1) 대비
`UNIQUE(company_id, store_connection_id, coupang_vendor_item_id)` 1개 추가(미적용·미승인 파일이라 수정 가능했음 — 적용된 Migration 바이트는 건드리지 않았다).

| 리허설 | 결과 (임시 DB, 실제 `homez.db` 미접촉) |
|---|---|
| **A. 빈 DB 신규 설치** | 75개 적용, `integrity_check` ok, FK 위반 없음, 이력 75행, 모델↔SQL 컬럼·인덱스 동일 |
| **B. 데이터 있는 기존 DB 업그레이드**(신규 파일 제외 74개 + 합성 업무 데이터: 회사·사용자·구매작업 4·주문 4·제출 1·위저드 1 등) → 사본에 신규 파일만 적용 | 대기 목록 1개뿐, dry-run 무변경, 기존 159개 테이블 행 수·행 해시·스키마 해시 **전부 불변**, 추가된 테이블은 1개뿐, checksum 일치, 재적용 차단(원시 실행은 `table already exists`), 롤백(DROP+이력 삭제)이 사전 상태와 동일, 사전 상태 DB 파일 SHA-256 불변 |
| **C. 최신 코드 + 미적용 DB** | 검토 정상 조립, `supplier_link.state=UNAVAILABLE`, 기존 수량 비교·금액 그대로, 연결 저장은 409로 거부, 준비 판정 `STORE_UNAVAILABLE`, 외부 접속 0건 |

관찰: 사전 상태 DB의 `foreign_key_check` 2건(합성 시드 감사로그)은 적용 전후 동일 — Migration이 만든 것이 아니다. 신규 설치 DB에 ORM 테이블 `inventories`가
없다 — 이 Migration과 무관한 기존 공백(migrations/에 해당 CREATE TABLE 없음).
**주의(설계 영향)**: 신규 Migration 파일이 저장소 `migrations/`에 들어가면 앱은 적용 승인 전까지 제한 모드(쓰기 차단·위저드 사전검사
`SYSTEM_MIGRATION_RESTRICTED_MODE`)로 동작한다 — 기존 설계이며, 그래서 채택(파일 추가)과 실제 적용을 같은 승인 창구에서 다룬다.

**기존 체크섬 실패의 정확한 원인**: `test_gate9_migration_file_unchanged`·`test_newest_migration_files_checksum_locked`는 옛 Migration의 바이트 checksum을 고정한다.
실제 작업 트리의 해당 파일은 LF(CRLF 0)인데 `core.autocrlf=true`로 새로 체크아웃하면 CRLF로 바뀌어 checksum이 달라진다 — 패치 유무와 무관하고,
바이트가 같은 적용 환경(실제 작업 트리 복사본 + 패치)에서는 13건 전부 통과한다. 테스트·기대 checksum은 바꾸지 않았다. 근본 해결은 `.gitattributes`로
`migrations/*.sql`의 줄바꿈을 고정하는 것(별도 작업으로 분리).

## 9. 승인 요청과 실제 호출 계획

이번 작업에서 **실제 저장소 코드·Model·Migration 채택, 실제 `homez.db` 적용, 실제 쿠팡·온채널 호출, 실제 등록·판매신청·주문·발주·결제는 하지 않았다.**
아래는 전부 사용자 승인 대기 항목이며, 서로 독립이다(하나의 승인이 다음 항목을 승인하지 않는다).

| # | 승인 요청 | 범위·횟수 | 이 승인으로 되지 않는 것 |
|---|---|---|---|
| 1 | **코드 채택** — `docs/proposals/20260921_supplier_option_link.patch`(25개 파일: Model 1·Migration 1·서비스/라우터/화면/문구/테스트)를 저장소에 적용 | 저장소 파일 변경과 커밋만 | 실제 DB 적용. 채택 즉시 앱은 미적용 Migration 때문에 제한 모드(쓰기 차단)가 된다 |
| 2 | **실제 `homez.db` Migration 적용** — 신규 1개(`20260921_00_…`, SHA-256 `aebbd3fd…9884`) + 기존 미적용 1개(`20260918_00_…`) 순서 확인 | 사전 백업 + SHA-256 + `integrity_check` → 사본 리허설 재실행 → 적용 → 사후 검증. 롤백은 신규 테이블 DROP + 이력 1행 삭제 | 실제 등록·주문 |
| 3 | **쿠팡 상품 상세 조회(읽기 전용)** — 등록된 상품의 옵션번호 확인 | `GET https://api-gateway.coupang.com/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{sellerProductId}`, 사용자가 화면에서 "쿠팡 옵션번호 확인"을 누를 때마다 1회, 자동 재시도 없음, 상품당 약 2회(승인 전·후). **실제 등록이 존재한 뒤에만** 의미가 있다 | 상품 등록·수정·판매신청 |
| 4 | **실제 DB 사본 접근** — 실제 데이터로 업그레이드 리허설 | 이번에는 승인 범위가 확인되지 않아 **접근하지 않았다**(합성 데이터 리허설만 수행) | — |
| 5 | 시험상품 선정·실제 등록·판매신청·시험 주문·결제 / 온채널 상품 조회(`GET /openapi/seller/product/{code}`, 연결 id=4, 3회 이하) / 쿠팡 시험 주문 정책 문의 발송 | 이전 라운드부터 계속 미승인 | — |
| 6 | 줄바꿈 고정(`.gitattributes`로 `migrations/*.sql`을 LF 고정) — 체크섬 테스트 2건이 새 체크아웃에서 실패하는 원인 해소 | 별도 작업 | 적용된 Migration·기대 체크섬 변경 없음 |

**등록 결과 조회의 실제 응답 형태는 공식 문서의 계약으로만 확인했다.** 실제 응답으로 확인하기 전에는 옵션번호 부착 경로가 "문서 기준 구현"이며, 형식이 다르면
`ITEMS_MISSING`/`SELLER_PRODUCT_ID_MISMATCH`/`AMBIGUOUS_RESPONSE`로 아무것도 저장하지 않고 "준비 미완료"로 남는다(추측 저장 없음).

## 10. 전체 회귀 결과 (동결본 `dc97d1f`, 코드 무변경 상태)

**4,668 실행 / 실패 2 / 오류 0 / skip 16 / 성공 4,650**(성공 = 실행 − 실패 − 오류 − skip). 소요 7,276초. 외부 접속 시도 0건(가드 로그 `GUARD_ACTIVE`만),
실제 `homez.db`(3,702,784바이트, 2026-09-17, SHA-256 앞 16자리 `f3aafca1bf1e68af`)는 전후 동일.
이 수치는 **"전체 통과"가 아니다.** 실패 2건과 예상보다 많은 skip 9건은 아래처럼 원인을 분리했다.

| 항목 | 내용 | 패치 관련성 |
|---|---|---|
| 실패 `test_launcher_refuses_when_port_is_other_service` | 복제본에 `venv\Scripts\python.exe`가 없어 런처가 포트 검사 전에 종료 | 무관 |
| 실패 `test_real_homez_db_has_store_connections_table_applied` | 복제본에는 실제 DB가 없고(gitignore), 실행 중 생긴 **0바이트** `homez.db`를 읽어 표 목록이 비어 있음 | 무관 |
| skip 16 (이전 기준선 7) | 추가 9건은 실제 DB가 없을 때 건너뛰는 실제 DB 대조 테스트로 **추정**한다(실행 초반 `homez.db`가 없었고 중간에 0바이트 파일이 생겨 뒤쪽 모듈은 skip이 아니라 실패로 바뀜). 건별 skip 사유는 이번 실행 출력에서 개별 확인하지 않았다 | 무관(기준선 대조로 확인, 아래) |

**패치 무관 확인**: 패치 없는 기준선 `f6fb44c`의 새 복제본에 같은 조건(venv 없음, 0바이트 `homez.db`)을 만들고 skip 조건이 있는 21개 모듈(418건)을 돌리면
동결본과 **실패 5·오류 2·skip 6이 이름까지 동일**했다(둘 다 런처 포트 바인딩 오류 로그 2줄이 섞여 나온다). 이번 패치가 만든 실패는 없다.

**한계(실제로 검증하지 못한 것)**: 실제 `homez.db`를 읽는 것으로 추정되는 테스트 약 9건(위 skip)과 위 실패 2건은 **패치가 적용된 상태에서 실제 DB·실제 venv가 있는 환경으로는 실행하지 못했다.**
그 테스트들은 각각 기존 Migration 하나가 실제 DB에 적용됐는지를 읽기 전용으로 확인하는 것이라 신규 Migration 파일과 직접 관계는 없어 보이나,
이는 정적 판단이다. 채택 승인 후 실제 저장소(venv·실제 DB 있음)에서 이 테스트들을 읽기 전용으로 다시 돌려 확인해야 한다(승인 항목 1의 사후 검증).
