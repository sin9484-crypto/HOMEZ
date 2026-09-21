# HOMEZ V7 — 상품 옵션 등록·공급처 옵션 연결 (2026-09-21)

> 상태: **조사·설계·격리 검증 완료 / Model·Migration 채택은 사용자 승인 대기.**
> 이 문서와 함께 커밋되는 것은 문서·패치 파일뿐이며, `app/`·`migrations/`의 운영 코드에는
> 아직 옵션 연결 구현이 들어가 있지 않다(패치는 `docs/proposals/`에 보존). 실제 `homez.db`에는
> 어떤 변경도 하지 않았다. 실제 쿠팡 등록·온채널 판매신청·주문·발주·결제·외부 API 호출도 없었다.

## 1. 기준선 대조

| 항목 | 값 |
|---|---|
| 원격 HEAD (`origin/main`) 확인 시점 | `088a8e9` (fetch 후 로컬과 동일) |
| 이번 작업 커밋 | `71f2b0e` 쿠팡 옵션 SKU 검증 수정(아래 §3) |
| 고정 기준선 전체 회귀 | a32623e 기준 4,563 실행 / 4,556 성공 / 실패·오류 0 / skip 7 (이후 문서 커밋만 추가됨) |
| 동일 코드 전체 회귀 재실행 | **하지 않음**(지시대로) |
| 이번 변경의 회귀 | 인접·집중 회귀만 실행 — §8. 공용 계약 변경 후보(패치)는 채택 시점에 모아서 전체 회귀 1회 예정 |

"집중 회귀 통과"를 "전체 회귀 통과"로 합쳐 말하지 않는다.

## 2. A(옵션 등록)와 B(옵션 연결) 구분 — 실제 코드 추적

경로: 상품 준비 화면(콘솔 옵션 조합 빌더) → `listing_wizards.channel_selections_json[].required_fields.items[]`
→ `marketplace_submissions`(PENDING→SUBMITTING 선점, 멱등키·지문, 결과불명은 자동 재시도 금지)
→ `CoupangLiveProductProvider.create_product` → 쿠팡 응답 → 등록번호 저장 → (주문 수집) →
`UnresolvedOrderItem.channel_sku` → `OrderSkuResolution` → `OrderItem` → `PurchaseTask` → 발주 검토.

### A. 옵션 등록 (공급처에서 확인한 옵션 중 사용자가 고른 것을 쿠팡 상품 옵션으로 등록)

| 항목 | 판정 | 근거 |
|---|---|---|
| 옵션 조합 빌더 → `items[]`(itemName·externalVendorSku·가격) 입력·저장 | 구현 | `listing_wizard_service.py:650-662`(저장 시 SKU 중복 차단), `required_fields_schemas.py` |
| 쿠팡 페이로드에 옵션별 `externalVendorSku` 전송 | 구현 | `coupang_live_payload.py:124` |
| 등록 요청 멱등·결과불명 보존(무조건 재등록 금지) | 구현 | `marketplace_submissions` 상태기계, `coupang_live_provider.py`(응답에서 `sellerProductId`만 추출) |
| **SKU가 이후 주문 조인 키로 안전한 값인지** | **결함 → 수정함(`71f2b0e`)** | 스키마 주석은 "빈 값 불허·최대 150자"였으나 실제 필드에 제약이 없어 `''`·`'   '`·`' SKU'`가 통과(수정 전 재현). 주문 수집은 `strip()`한 값을 `channel_sku`로 쓰므로(`coupang_normalizer.py:112`) 공백 포함 SKU는 영원히 매칭되지 않는다 |
| 등록 후 쿠팡이 부여한 옵션별 식별자(`vendorItemId` 등) 저장 | **미구현** | 응답에서 상품 단위 `sellerProductId`만 저장. 옵션별 식별자를 읽는 코드 없음 |
| 등록 결과 조회(`GET seller-products/{id}`) 응답 형식 | **검증필요** | 저장소에 실제 응답 형식의 증거 없음 — 추측하지 않는다. 실제 등록 시험(승인 필요) 때 확인 |
| 위저드 `draft.sku_list` ↔ `items[].externalVendorSku` 연결 | 미구현 | `listing_wizard_precheck.py:89`는 `sku_list`만 검사, `items[]`와 교차 검증 없음 |
| SKU로 쿠팡 상품 역조회 | 미구현 | 등록 결과불명 시 안전한 재조회 수단 없음 → 현행대로 UNKNOWN 보존이 맞음 |
| 공급처 옵션 조회(온채널) 후 판매 옵션 선택 UI | 구현(입력) | 기존 콘솔 흐름. 단 공급 옵션ID는 화면 입력에 저장되지 않음(B 참고) |

### B. 옵션 연결 (쿠팡 주문 옵션 → 온채널 상품코드·옵션ID 저장·재사용)

| 항목 | 판정 | 근거 |
|---|---|---|
| 쿠팡 주문 품목 → HOMEZ 재고 SKU | 구현 | `OrderSkuResolution`(회사·스토어 연결·`channel_sku` → `inventory_sku_id`, `collection_model.py:90`), `InventorySku`, `InventoryChannelMapping` |
| **HOMEZ SKU/`channel_sku` → 온채널 상품코드·옵션ID 영구 저장** | **미구현(명칭이 달라서 못 찾은 것이 아님)** | 기존 구조 전수 확인: 발주 검토 입력 `OrderSubmissionReviewRequest(external_product_id, options)`는 "사람이 직접 골라서 넘긴다(자동 매칭 없음)"(`schema.py:509-514`), `PurchaseTask`는 이름·수량만 보유. 저장·재사용 구조가 없어 주문마다 재입력 |
| 판매 1세트 = 공급 낱개 N개 수량 단위 | 미구현 | 검토는 판매 수량과 선택 옵션 합계를 직접 비교(단위 개념 없음) |
| 공급 옵션 삭제·품절·이름 변경 감지 후 조용한 대체 방지 | 미구현 | 검토는 매번 실조회하지만 저장된 기대값이 없어 비교 대상이 없음 |
| 공급처 옵션ID가 상품·계정 범위에 종속되는지 | **검증필요** | 어댑터·화면은 ID를 문자열로만 다룸(`ChannelProductOption.option_id: str`). 스펙만으로 전역 유일성을 확정할 수 없어 (계정, 상품코드, 옵션ID)를 항상 함께 저장하고 검토마다 그 계정으로 실조회해 확인하도록 설계 |

## 3. 이번에 커밋한 수정 — SKU 검증 (`71f2b0e`)

- `required_fields_schemas.py`: `externalVendorSku` `min_length=1, max_length=150` + 앞뒤 공백·공백만 있는 값 거부.
- `coupang_submission_contract.py`: 스키마를 거치지 않은 옛 초안 JSON도 `SKU_REQUIRED` 차단 코드로 등록 전에 막음.
- 신규 `tests/test_coupang_option_sku_contract.py` 6건(수정 전 재현: 스키마 5 + 계약 4 실패).
- 인접 회귀(`test_coupang_*` 399 / `test_listing_*` 273 / `test_marketplace_*` 239 / `test_submission_*` 32 = 943건) 전부 OK.
- Migration 불필요, 외부 호출 없음.

## 4. 옵션 연결 설계 (승인 대기 — Model·Migration 포함)

### 4-1. 조인 키와 테이블

조인 키는 이름·배열 순서가 아니라 **쿠팡 `externalVendorSku` = 주문 수집의 `channel_sku`** 다.
등록 때 옵션마다 보낸 값이 주문에서 그대로 돌아오고, 기존 `OrderSkuResolution`도 같은 키를 쓴다.
HOMEZ 재고 SKU와의 관계는 그 기존 테이블로 이어지므로 새 테이블에 중복 저장하지 않는다.

신규 테이블 1개 `supplier_option_links`(추가형, 기존 테이블·데이터 무변경):

| 컬럼 | 용도 |
|---|---|
| `company_id` | 회사 격리 |
| `store_connection_id` | 쿠팡 판매 계정(논리 참조) |
| `channel_sku` (150) | 쿠팡 externalVendorSku |
| `purchase_connection_id` | 실제 매입 계정(논리 참조) |
| `supplier_product_code` (50), `supplier_option_id` (50, 문자열) | 온채널 상품코드·옵션ID |
| `supplier_option_name_snapshot` | 화면 표시·변경 감지 전용(매칭에 쓰지 않음) |
| `units_per_sale` (CHECK ≥ 1) | 판매 1개 = 공급 옵션 몇 개 |
| `status` (ACTIVE / NEEDS_REVIEW / DISABLED), `status_reason` | 재확인 필요·해제 |
| `coupang_seller_product_id`, `coupang_vendor_item_id` | 등록 결과에서 확인된 쿠팡 식별자(선택, NULL 가능) |
| `version`, `confirmed_by`, `confirmed_at`, `created_at`, `updated_at` | 낙관적 동시성·감사 |

`UNIQUE(company_id, store_connection_id, channel_sku)` — 한 판매 옵션 = 하나의 공급 옵션 연결.
비밀값·고객 개인정보는 저장하지 않는다. 정확한 DDL은 패치의
`migrations/20260921_00_create_supplier_option_link_schema.sql`(SHA-256 `e8214dc6…611b59`).

### 4-2. 사용자 흐름과 코드 배선

| 단계 | 구현 |
|---|---|
| ①② 공급 상품·옵션 조회, 판매 옵션 선택 | 기존 흐름 |
| ③ 상품명·옵션명·가격·구성 수량 확인 | `units_per_sale`을 함께 입력 |
| ④ 사용자 승인 후 쿠팡 등록 | 기존 등록 흐름(외부 쓰기라 별도 승인) |
| ⑤ 쿠팡 반환 식별자 저장 | `attach_coupang_identifiers()` — **SKU로만** 매칭, 응답 순서·이름 미사용, 이미 다른 값이 있으면 덮어쓰지 않고 충돌, 새 연결을 만들지 않음. **호출부(등록 결과 조회)는 응답 형식 미확인이라 미배선** |
| ⑥ 대응 저장 | `PUT /supplier-option-links` — 저장 전 그 매입 계정으로 공급 상품을 **실조회해 옵션ID가 그 상품에 속함을 ID로 확인**, 확인 못 하면 저장 안 함. 같은 값 재저장은 멱등, 다른 값은 `replace=true` 명시 필요(버전 +1) |
| ⑦ 다음 주문 발주 검토안 | `build_order_submission_review()`가 저장된 연결로 기대 구성(판매 수량 × 구성 수량)을 계산·대조. 응답에 `supplier_link` 포함 |
| ⑧ 사용자 승인 후 발주 | `finalize_approval()`이 서버에서 다시 대조(연결이 있으면 옵션 구성 생략도 불가). 연결은 발주·결제 승인이 아님 — 가격·재고·배송비·한도·최종 승인은 기존 정책대로 매번 확인 |
| 자동 매입 준비 판정 | `POST /supplier-option-links/readiness` — 등록한 SKU 전부가 ACTIVE일 때만 `ready=true`. 등록 성공과 연결 저장 성공은 별도 기록이며 하나라도 없으면 "자동 매입 준비 완료"가 아님 |
| 화면(콘솔) | **미구현.** API·검토 응답까지만 격리 검증됨. 채택 승인 후 구현하고 브라우저로 검증 |

추가 API: `GET /supplier-option-links`, `POST /supplier-option-links/{id}/disable`,
`GET /purchase-tasks/{id}/supplier-option-link`(읽기 전용 미리보기). 모두 `AdminGuard`.

### 4-3. 잘못된 연결 방지 (지시 §5·§6 대응표)

| 상황 | 동작 | 검증 테스트 |
|---|---|---|
| 공급 옵션 삭제·다른 상품으로 이동 | 대체하지 않고 `NEEDS_REVIEW`로 내리고 검토 차단. 옵션이 돌아와도 자동 복귀 없음 — 사용자가 다시 저장해야 ACTIVE | `test_deleted_supplier_option_needs_review_and_is_not_substituted` |
| 공급 옵션명 변경(구성 변경 가능성) | `NEEDS_REVIEW` + 차단 | `test_relabelled_supplier_option_needs_review` |
| 공급 옵션 품절 | 차단(연결은 유지) | `test_out_of_stock_supplier_option_blocks_but_keeps_the_link` |
| 검토 시점 공급처 조회 실패 | 연결을 신뢰하지 않고 차단 | `test_unverifiable_live_lookup_blocks_...` |
| 매입 계정 변경 | 조용히 대체하지 않고 차단 | `test_changed_purchase_account_blocks_...` |
| 다른 회사·다른 스토어 | 저장·조회·해제 모두 회사·스토어 범위로 서버 검증 | `test_link_is_scoped_to_the_store_and_company` |
| 수량 단위 불일치 | 판매 수량 × 구성 수량과 다르면 차단 | `test_units_per_sale_multiplies_...` |
| 복잡한 묶음(여러 공급 옵션) | 저장 거부 + 이유 표시 | `test_complex_bundles_and_bad_units_are_blocked_with_a_reason` |
| 연결 없는 주문 | 자동 대체 없음 — 기존 수동 확인 경로(사용자가 상품·옵션을 직접 지정) | `test_manual_path_without_a_link_is_unchanged` |
| 주문의 스토어를 하나로 특정 못 함 | 자동 적용 안 함 | `test_order_with_ambiguous_store_is_not_auto_linked` |
| 이후 매핑 수정 | 이미 승인·발주된 건은 `PurchaseOrderApproval.options_snapshot_json`·`PurchaseOrderSubmissionAttempt`에 동결돼 불변 | `test_later_link_edit_does_not_change_an_already_approved_snapshot` |
| 등록 응답 순서 변경·동명 옵션 | 옵션ID·SKU로만 매칭 | `test_same_label_options_and_reordered_response_...`, `test_attach_matches_by_sku_only_...` |
| 등록 결과 중복 SKU·vendorItemId 누락·기존 식별자 충돌 | 원자적으로 거부(부분 부착 없음), 덮어쓰기 없음 | `test_attach_refuses_ambiguous_...`, `test_attach_never_overwrites_...` |
| 결과불명(UNKNOWN) 재등록 | 이 설계는 등록 경로를 건드리지 않는다 — 기존 "결과불명은 자동 재시도 금지"가 유지됨 | 기존 `marketplace_submissions` 테스트 |

### 4-4. Migration 미적용 DB에서의 안전성

`is_store_available()`이 테이블 존재를 확인해, 미적용 DB + 최신 코드에서는 이 기능만 "사용 불가"로
물러나고 기존 수동 경로가 그대로 동작한다(검토 `supplier_link.state = UNAVAILABLE`, 저장은 409로 거부).
테스트: `UnmigratedDatabaseFallbackTestCase`.
주의: 신규 Migration 파일이 저장소에 들어가면 기존 설계대로 앱은 승인 전까지 제한 모드(스키마 업데이트
필요)로 시작한다 — 채택(=파일 추가)과 실제 DB 적용을 같은 승인 창구에서 다루는 이유다.

## 5. 격리 검증 결과 (스크래치 저장소 `w3`, 기준 `088a8e9` + 패치)

- 신규 `tests/test_supplier_option_link_service.py` **39건** — 3회 반복 전부 OK. 실제 네트워크 없음
  (가짜 Adapter, InMemoryCredentialStore).
- 지시 §7 항목: 단일·복수·일부 선택 / 동명 옵션·응답 순서 변경 / 회사·계정 격리 / 중복 저장·동시 처리
  (UNIQUE 경합 2종 + 버전 CAS + 4스레드 병렬) / 재시작 유지(파일 DB) / 내부 저장 실패(커밋 직전 실패 →
  연결 없음·준비 미완료) / 공급 옵션 삭제·이름 변경·품절 / 구성 수량 불일치 / 재주문 재사용 / 수동 경로 회귀 /
  Migration 미적용 폴백.
- 변이 검증 4건(구성 수량 곱셈 제거, 삭제 옵션 감지 제거, 식별자 덮어쓰기 허용, 준비 판정 완화) — 각각
  해당 테스트가 실패로 검출됨. 원본 복원 후 바이트 동일 확인.
- 동시성 테스트의 스레드는 다른 Session이 소유한 ORM 객체를 읽지 않는다(과거 결함 재발 방지).

## 6. Migration 복사본 검증 리허설 (임시 DB, 실제 `homez.db` 미접촉)

사전 상태 DB = 저장소 `migrations/` 74개를 Runner로 적용한 임시 DB(158 테이블). 이것은 **실제 운영 DB의
복사본이 아니다** — 운영 DB는 열지 않았다. 사전 상태 DB를 파일 복사한 뒤 신규 파일만 Runner로 적용:

| 확인 | 결과 |
|---|---|
| 대기 목록 | 신규 1개만 |
| dry-run | 신규 1개만 나열, DB 무변경 |
| 적용 후 `PRAGMA integrity_check` / `foreign_key_check` | ok / 빈 목록 |
| 기존 테이블 행 수·행 해시·스키마 해시 | 전부 동일(`schema_migrations` 제외), 추가된 테이블은 `supplier_option_links` 1개뿐 |
| 이력 checksum | 파일과 일치, `APPLIED` |
| 재적용 | Runner는 이력에 있어 재실행하지 않음, 원시 재실행은 `table already exists`로 실패 |
| 모델 ↔ SQL 컬럼 | 동일 |
| 롤백(`DROP TABLE` + 이력 행 삭제) | 사전 상태와 동일 복원, 무결성 ok |
| 사전 상태 DB 파일 | 리허설 후 SHA-256 동일 |

한계: 사전 상태 DB에는 데이터 행이 없어 "기존 데이터 보존"은 빈 테이블 기준의 확인이다. **실제 적용 때는**
백업 생성 → 백업 SHA-256·`integrity_check` → 적용 → 적용 후 같은 검증(행 수·해시 대조 포함)을 수행한다.

## 7. 승인 요청 — 별도 승인 대상

1. **Model·Migration·코드 채택**: 패치 `docs/proposals/20260921_supplier_option_link.patch`
   (12개 파일, +1,958/−2; `git apply --check`로 HEAD `71f2b0e`에 적용 가능 확인) — `app/domains/purchase_task/`
   (model·constants·schema·service·order_approval_service·router·신규 service·router), `app/main.py`,
   Migration 1개, 테스트 2개. Windows에서 CRLF로 체크아웃되면 `git apply --ignore-whitespace`.
2. **실제 `homez.db` Migration 적용**(테이블 1개 추가): 백업·검증 절차는 §6. 채택과는 별도 승인.
3. 채택 후 남는 작업(승인 범위 안에서 이어서 진행): 콘솔 화면(연결 미리채움·저장 버튼·상태 표시), 등록 결과
   조회 → `attach_coupang_identifiers` 배선(응답 형식은 실제 등록 시험에서 확인), 전체 회귀 1회.

이 문서·패치는 원본 DB 변경·실제 상품등록·판매신청·주문·발주·결제를 승인한 것이 아니다.

## 8. 집중 회귀 결과 (스크래치 `w3` = `088a8e9` + 패치, 전체 회귀 아님)

| 묶음 | 실행 | 결과 |
|---|---|---|
| `test_purchase_*` | 470 | OK |
| `test_migration_*` | 89 | OK (skip 2) |
| `test_permissions_timestamps_migration` | 13 | **2 실패**(아래) / 11 성공 |
| `test_onchannel_*` | 36 | OK |
| `test_order_collection_*` | 16 | OK |
| 신규 `test_supplier_option_link_service` | 39 | OK (3회 반복) |

합계 663건 실행 / 실패 2 / 오류 0 / skip 2. **이 숫자는 고정 기준선 전체 회귀(4,563건)와 합산하지 않으며
전체 통과를 뜻하지 않는다.** 그 외 이번 세션의 인접 회귀(SKU 수정, 실제 저장소): 943건 OK.

**실패 2건(`test_gate9_migration_file_unchanged`, `test_newest_migration_files_checksum_locked`)의 원인 —
이 패치와 무관한 기존 환경 특성**: 두 테스트는 옛 Migration(20260816_00, 2026-08-28 신규 3개)의 바이트
checksum을 고정한다. 이 파일들은 패치가 건드리지 않는다. 패치 없는 `HEAD` 새 복제본(`git clone`)에서도
**같은 2건이 똑같이 실패**하고(대조군), 실제 저장소 작업 트리에서는 13건 전부 통과한다 — 즉 checksum이
체크아웃 결과의 줄바꿈 바이트에 의존한다. 패치를 적용한 복제본에서도 나머지 11건(신규 Migration을 반영한
"예상 파일 목록" 테스트 포함)은 통과했다. 이 테스트들을 고치거나 약화하지 않았다. 별도 정리 대상(위험
요소): 새 환경에서 저장소를 체크아웃해 회귀를 돌리면 이 2건이 실패한다.

미확인: 패치를 **실제 저장소 작업 트리에 적용한 뒤**의 전체 회귀는 실행하지 않았다(채택 승인 후 1회 예정).
