# HOMEZ V7 쿠팡 SELLER_FULFILLED 공식 필드 대조표 (2026-08-30)

V7 쿠팡 상품등록 파이프라인 안정화 지시 Phase 4 산출물. 로켓그로스
(`CoupangRocketGrowthFields`)·네이버는 이 표에 포함하지 않는다(이번
안정화 범위가 SELLER_FULFILLED 하나이므로).

**확인 상태 표기 기준**
- `CONFIRMED(문서)` — 이번 세션 또는 이전 세션에서 developers.coupang.com
  공식 문서를 직접 fetch해 확인.
- `CONFIRMED(Live)` — 실제 성공한 Live 제출(상품ID는 제한된 감사
  증거 문서에만 보존, 이 문서에는 미기재) 또는 그 직전 3회 실패
  응답에서 직접 관측.
- `UNCONFIRMED` — 코드에는 있으나 공식 문서·실제 Live 어느 쪽으로도
  이번 세션에서 재확인하지 못함. 값을 임의로 필수로 승격하지 않는다.
- `HOMEZ 내부` — 쿠팡 payload로 전송되지 않는 HOMEZ 전용 상태값.

| 필드 경로 | HOMEZ 저장 위치 | UI 입력 위치 | Payload 위치 | 필수/선택 | 근거 | Fake fixture | 검증 코드 | 테스트 | 확인 상태 |
|---|---|---|---|---|---|---|---|---|---|
| `vendorId` | `WindowsCredentialStore`(자격증명, `required_fields`에 없음) | 데스크톱 앱 자격증명 설정(콘솔 UI 아님) | payload 루트 `vendorId` | 필수 | CONFIRMED(문서) | `_provider()`의 `vendor_id` | `coupang_live_provider.py::create_product()` | `CoupangLiveProviderTestCase` | CONFIRMED(Live) |
| `vendorUserId` | `required_fields.vendorUserId` | `console.js` `.lw-vendor-user-id`(존재) | payload 루트 | 필수(감사 F-01, 이번 세션 신규 강제) | CONFIRMED(Live, 실패 재현) | `_valid_required_fields()` | `coupang_submission_contract.py` `VENDOR_USER_ID_REQUIRED` | `test_missing_vendor_user_id_blocks` | CONFIRMED(Live) |
| `deliveryCompanyCode` | `required_fields.deliveryCompanyCode` | `console.js` `.lw-delivery-company`(존재, 14개 실제 API 코드) | payload 루트 | 조건부(SEQUENCIAL/일반배송 시 필수, Live 실패로 확인) | CONFIRMED(Live, 실패 재현) | 동일 | `DELIVERY_COMPANY_CODE_REQUIRED` | `test_missing_delivery_company_code_blocks` | CONFIRMED(Live) |
| `deliveryMethod` | `required_fields.deliveryMethod` | 자동 결정(SEQUENCIAL 고정, UI 노출 안 함) | payload 루트 | 필수 | CONFIRMED(문서) | 고정값 | Pydantic `Literal` | 기존 스키마 테스트 | CONFIRMED(문서) |
| `outboundShippingPlaceCode` | `required_fields.outboundShippingPlaceCode` | `console.js` `.lw-outbound-place`(존재, 조회 기반 select) | payload 루트(Number로 형 변환) | 필수, **Number 타입**(문자열 거부) | CONFIRMED(문서) | `_coerce_outbound_shipping_place_code` | `OUTBOUND_SHIPPING_PLACE_CODE_REQUIRED`/`_INVALID` | `test_non_numeric_outbound_shipping_place_code_is_blocked` 등 4개 | CONFIRMED(문서+코드) |
| `returnCenterCode`/`returnChargeName`/`companyContactNumber`/`returnZipCode`/`returnAddress` | `required_fields.*` | `console.js` `.lw-return-center` 계열 select(조회 기반) | payload 루트 | 필수 | CONFIRMED(문서) | `_valid_required_fields()` | Pydantic 필드 + `reject_placeholder_values` | 기존 스키마 테스트 | CONFIRMED(문서) |
| `displayCategoryCode` | `required_fields.displayCategoryCode` | `console.js` `.lw-policy-official-category`(readonly, 카테고리 추천 결과) | payload 루트(`int()`) | 필수 | CONFIRMED(문서) | `80754` | `DISPLAY_CATEGORY_CODE_REQUIRED` | 신규 계약 테스트 내 간접 검증 | CONFIRMED(문서) |
| `images[]` (`imageOrder`/`imageType`/`vendorPath`/`cdnPath`) | `required_fields.images` | `console.js` `.lw-live-image-url`(존재) | payload `items[].images` | 필수(대표 이미지 1개 이상, 공개 URL) | CONFIRMED(문서+코드) | `LIVE_IMAGES` | `PUBLIC_IMAGE_URL_REQUIRED`/`REPRESENTATION_IMAGE_REQUIRED` | `CoupangLivePayloadTestCase` 다수 | CONFIRMED(문서) |
| `liveImageRightsConfirmed` | `required_fields.liveImageRightsConfirmed` | `console.js` `.lw-live-image-rights`(존재, 체크박스) | 전송 안 함(HOMEZ 내부) | 필수(HOMEZ 정책) | HOMEZ 내부 정책 | `True` | `IMAGE_RIGHTS_CONFIRMATION_REQUIRED` | 기존 payload 테스트 | HOMEZ 내부 |
| `notices[]` | `required_fields.notices` | `console.js` `[data-lw-notice-fields]`(존재) | payload `items[].notices` | 필수 | CONFIRMED(문서) | `LIVE_NOTICES` | `NOTICE_INFORMATION_REQUIRED` | 기존 payload 테스트 | CONFIRMED(문서) |
| 고시정보 fingerprint 계약(`official_category_code`/`category_metadata_version`/`category_metadata_fingerprint`/`notice_information`/`notice_confirmed_at`/`notice_confirmed_by_user_id`/`notice_input_fingerprint`/`notice_required_field_keys`) | `channel_policy_attributes.*` | `console.js` `[data-lw-notice-fields]` 저장 시 자동 계산(사용자가 직접 만지는 필드 아님) | 전송 안 함(HOMEZ 내부) | 필수(HOMEZ 정책, 이번 세션 신규 — 7단계·9~10단계 동일 강제) | HOMEZ 내부 정책 | `LIVE_NOTICE_CONTRACT_ATTRIBUTES` | `validate_saved_notice_contract` → `NOTICE_CONTRACT_INCOMPLETE` | `test_metadata_fingerprint_mismatch_blocks` | HOMEZ 내부(UI 확인 흐름 자체는 재확인 필요 — 아래 미확인 항목 참고) |
| `contents[]`(`contentsType`/`contentDetails[].{content,detailType}`) | `required_fields.contents` | 콘솔 상세설명 입력 UI(정확한 `class`/`data-*` 선택자는 이번 세션에서 재확인하지 않음) | payload `items[].contents` | 필수 | CONFIRMED(문서, 원문 예시 확인) | `LIVE_CONTENTS` | `CONTENTS_REQUIRED` 등 6개 코드 | `test_missing_contents_is_fail_closed`, `test_empty_contents_array_blocks` | CONFIRMED(문서) |
| `items[].{itemName,externalVendorSku}` | `required_fields.items[]` | `console.js` `[data-lw-item-combo-builder]`(존재) | payload `items[]` | 필수(최소 1개) | CONFIRMED(문서) | `_valid_required_fields()["items"]` | `ITEM_REQUIRED`/`DUPLICATE_SKU` | `test_duplicate_sku_across_items_is_blocked` | CONFIRMED(문서) |
| `items[].{originalPrice,salePrice,maximumBuyCount,unitCount}` | `required_fields.items[]`(항목별) 또는 상품 전체 값 | 콤보 빌더(항목별) / 6단계 가격 입력(상품 전체) | payload `items[]`(항목 우선, 없으면 상품 전체로 대체) | 필수 | CONFIRMED(문서) | `test_per_item_price_and_stock_override_multiple_options` | Pydantic `Decimal/int gt=0` | 동일 | CONFIRMED(문서) |
| `items[].optionAttributes` → payload `items[].attributes[]` | `required_fields.items[].optionAttributes` | 콤보 빌더 | payload `items[].attributes[]` | 조건부(카테고리 메타데이터가 노출·필수로 지정한 속성만) | CONFIRMED(문서, 원문 예시 `"1개"` 단위 포함 확인) | `field_definitions` fixture | `validate_purchase_options` → `PURCHASE_OPTION_REQUIRED` | `test_purchase_option_wrong_value_blocks` | CONFIRMED(Live, 단위 오류 재현) |
| `maximumBuyForPerson` | `required_fields.maximumBuyForPerson`(기본 0) | 미노출(기본값만 사용) | payload `items[]` | 선택(기본 0=제한없음) | UNCONFIRMED(문서상 명시적 예시 미확인, 코드 기본값만 존재) | 기본값 | Pydantic 기본값 | 없음(전용 테스트 없음) | UNCONFIRMED |
| `maximumBuyForPersonPeriod` | `required_fields.maximumBuyForPersonPeriod`(기본 1) | 미노출 | payload `items[]` | 선택(기본 1) | UNCONFIRMED — **이전 감사 보고서의 "완전히 누락" 주장은 오류였다(이번 지시 정정 4번)**: 코드에 이미 기본값 1로 존재함을 재확인 | 기본값 | `required_fields.get(..., 1)` | 없음 | UNCONFIRMED이지만 이미 구현·기본값 존재 |
| `requested` | 코드 하드코딩(`True`, `required_fields`에 없음) | 없음(사용자 입력 대상 아님) | payload 루트 | 항상 `True` 고정 | UNCONFIRMED(실제 Live 성공 시 이 값으로 통과했다는 사실 자체가 유일한 근거) | 고정값 | 하드코딩 | 없음 | CONFIRMED(Live, 통과 사실로 방증) |
| `saleStartedAt`/`saleEndedAt` | 코드에서 `datetime.now()` 기준 계산(사용자 입력 아님) | 없음 | payload 루트 | 자동 계산 | UNCONFIRMED(공식 문서상 형식·범위 재확인 안 함) | 고정 로직 | `now`/`end` 계산 | 없음 | UNCONFIRMED이지만 실제 Live 성공 사례에서 이 로직으로 통과함 |
| `brand`/`brandId` | `required_fields.{brandState,brand,brandId,officialBrandName,brandEnrollmentStatus,brandLookupFingerprint}` | `console.js`에 **아직 UI 없음**(`lw-brand-state` 미구현) | payload 루트(`brandState`에 따라 `officialBrandName`/`brandId` 또는 사용자 확인 `brand`) | 조건부(브랜드 3상태 계약, 이번 세션 신규) | CONFIRMED(Live, Brand Enrollment 경고 관측) + HOMEZ 내부 정책 | `BrandSearchResult`/`FakeCoupangBrandProvider` | `BRAND_UNRESOLVED`/`BRAND_OFFICIAL_DATA_INCOMPLETE`/`BRAND_NO_BRAND_VALUE_MISSING` | `BrandThreeStateContractTestCase`(3개) | **프론트엔드 미구현** — 백엔드 계약·테스트만 완료 |
| `manufacture` | `required_fields.manufacture` | 미확인(2026-08-28 감사에서 "필드 자체가 payload에 없었다"고 지적, 이번 세션에 추가) | payload 루트 | 선택 | CONFIRMED(문서, 루트 선택 필드) | `test_manufacture_is_reflected_in_payload` | Pydantic 필드 | 동일 | CONFIRMED(문서) |

## 미확인·후속 확인 필요 항목

- `contents[]`를 실제로 입력하는 콘솔 UI 요소의 정확한 선택자는 이번
  세션에서 재확인하지 않았다(코드 동작은 검증됨, 화면 매핑만 미확인).
- `maximumBuyForPerson`/`maximumBuyForPersonPeriod`/`requested`/
  `saleStartedAt`/`saleEndedAt`의 정확한 공식 문서 근거(허용 범위·
  형식)는 이번 세션에서 재확인하지 않았다 — 실제 Live 성공 사례가
  현재 코드 그대로의 값으로 통과했다는 사실만 근거다. **이 지시의
  중요 정정 4번에 따라, 이 필드들을 "누락"으로 재기술하지 않으며
  추가로 필수화하지도 않는다.**
- `bundleInfo` 등 루트 레벨의 다른 선택 필드는 WING 화면 라벨과의
  매핑이 확인되지 않아 이번 세션에서도 추가하지 않았다.
