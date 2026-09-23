"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/coupang_test_fakes.py

2026-08-29 Pre-Live 감사 Phase C — 격리 패키징 검증(HOMEZ-ISOLATED-TEST
빌드)에서 10단계 위저드 흐름을 실제 쿠팡 API 호출 없이 끝까지 클릭
검증하기 위한 테스트 전용 Fake Provider. `listing_wizard_router.py`의
3개 Provider 팩토리 함수가 `HOMEZ_TEST_FAKE_COUPANG_PROVIDER=1`
환경변수가 명시적으로 설정된 경우에만 이 모듈을 지연 import해 사용한다
(installer/homez.iss의 `HOMEZ_TEST_FORCE_REINSTALL_DECISION`/
`HOMEZ_TEST_FORCE_DATA_DECISION`과 동일한 "일반적인 이름이 아니라
실수로 켜질 수 없는 이름 + 설정 안 하면 절대 개입하지 않음" 원칙).

이 파일 자체는 실제 배포에서 import조차 되지 않는다(호출부의
os.environ 체크가 import 문보다 먼저 실행되는 지연 import이므로).
연결된 쿠팡 판매계정(StoreConnection)이나 Credential을 전혀 조회하지
않는다 — 이 파일을 쓰는 것 자체가 실제 API 호출을 완전히 대체한다는
뜻이다.
=========================================================
"""

from app.domains.marketplace_listing.category_metadata import (
    CategoryMetadata, CertificationDefinition, NoticeFieldDefinition,
    PurchaseOptionAttribute,
)
from app.domains.marketplace_listing.coupang_live_provider import (
    LiveSubmissionResult, ProductOptionIdentifier, ProductOptionIdentifiersResult,
    ProductStatusResult,
)
from app.domains.marketplace_listing.coupang_logistics_provider import (
    LogisticsLocation,
)


class FakeCoupangLiveProductProvider:
    """실제 쿠팡 상품 생성/조회 API를 대신한다. 상품명에 담긴 매직
    마커로 시나리오를 제어한다(실패/UNKNOWN/경고 재현용) — 기본은
    경고 없는 성공.

    2026-08-30 후속 지시(격리 브라우저 E2E 공백 닫기) — 성공+경고
    시나리오(FAKE_WARNING_TRIGGER)를 추가한다. 합성 sellerProductId·
    합성 경고 문구만 쓴다 — 실제 값이 아니다."""

    # 2026-09-21 옵션 연결 — 등록 시 보낸 옵션(externalVendorSku)을 기억해 두었다가
    # 상세조회에서 합성 vendorItemId와 함께 **역순으로** 돌려준다(응답 순서가
    # 달라져도 SKU로만 매칭되는지 화면 E2E에서 확인하기 위함). 시나리오 마커:
    # 상품명에 FAKE_OPTION_ID_PENDING → 모든 옵션번호 null(승인 전), 
    # FAKE_OPTION_ID_PARTIAL → 첫 옵션만 번호 있음. 합성 값만 쓴다.
    _registered: dict = {}

    def create_product(self, payload):
        name = str(payload.get("sellerProductName", ""))
        reference = "FAKE-PACKAGED-SELLER-PRODUCT-001"
        if "FAKE_WARNING_TRIGGER" in name:
            reference = "90000000999"
        self._registered[reference] = {
            "skus": [
                str(item.get("externalVendorSku"))
                for item in (payload.get("items") or [])
                if isinstance(item, dict) and item.get("externalVendorSku")
            ],
            "marker": (
                "PENDING" if "FAKE_OPTION_ID_PENDING" in name
                else "PARTIAL" if "FAKE_OPTION_ID_PARTIAL" in name else "ALL"
            ),
        }
        if "FAKE_FAIL_TRIGGER" in name:
            return LiveSubmissionResult(
                outcome="FAILED", error_code="ERROR",
                error_summary="[FAKE PACKAGED TEST] 판매자 상품명이 정책에 위반됩니다.",
                http_status=200,
            )
        if "FAKE_UNKNOWN_TRIGGER" in name:
            return LiveSubmissionResult(
                outcome="UNKNOWN", error_code="TIMEOUT",
                error_summary="[FAKE PACKAGED TEST] 응답 시간 초과로 결과를 확인할 수 없습니다.",
                http_status=None,
            )
        if "FAKE_WARNING_TRIGGER" in name:
            return LiveSubmissionResult(
                outcome="SUBMITTED",
                external_reference="90000000999",
                http_status=200,
                warning_summary=(
                    "[FAKE PACKAGED TEST] Brand Enrollment 확인이 필요합니다."
                ),
                provider_response_code="SUCCESS",
            )
        return LiveSubmissionResult(
            outcome="SUBMITTED",
            external_reference="FAKE-PACKAGED-SELLER-PRODUCT-001",
            http_status=200,
            provider_response_code="SUCCESS",
        )

    def get_product_status(self, seller_product_id):
        # 2026-08-31 V7 필수 작업 2번(제출 장부 정합화) — 정합화 검토
        # 화면을 실제 쿠팡 호출 없이 격리 브라우저 E2E로 검증하기 위한
        # 시나리오 마커. 합성 값만 쓴다(실제 상품명·vendorUserId 아님).
        if seller_product_id == "FAKE-DENIED":
            return ProductStatusResult(outcome="FOUND", status_name="DENIED", http_status=200)
        if seller_product_id == "FAKE-PARTIAL":
            return ProductStatusResult(outcome="FOUND", status_name="PARTIAL_APPROVED", http_status=200)
        if seller_product_id == "FAKE-RECONCILE-MISMATCH":
            return ProductStatusResult(
                outcome="FOUND", status_name="APPROVED", http_status=200,
                seller_product_name="[FAKE PACKAGED TEST] 전혀 다른 상품",
                vendor_user_id="OTHER-VENDOR-USER",
                display_category_code="99999",
            )
        if seller_product_id == "FAKE-RECONCILE-NO-EVIDENCE":
            return ProductStatusResult(
                outcome="FOUND", status_name="APPROVED", http_status=200,
            )
        return ProductStatusResult(
            outcome="FOUND", status_name="APPROVED", http_status=200,
            seller_product_name="[FAKE PACKAGED TEST] 정합화 대상 상품",
            vendor_user_id="FAKE-VENDOR-USER-001",
            display_category_code="80754",
        )


    def get_product_option_identifiers(self, seller_product_id):
        record = self._registered.get(str(seller_product_id))
        if record is None:
            return ProductOptionIdentifiersResult(
                outcome="UNKNOWN", error_code="FAKE_NOT_REGISTERED",
                error_summary="[FAKE PACKAGED TEST] 이 가짜 Provider가 등록한 상품이 아닙니다.",
            )
        skus = list(reversed(record["skus"]))
        items = []
        for index, sku in enumerate(skus):
            has_id = record["marker"] == "ALL" or (
                record["marker"] == "PARTIAL" and index == 0
            )
            items.append(ProductOptionIdentifier(
                external_vendor_sku=sku,
                vendor_item_id=str(70000000 + index + 1) if has_id else None,
                seller_product_item_id=str(80000000 + index + 1),
            ))
        return ProductOptionIdentifiersResult(
            outcome="FOUND", items=tuple(items), http_status=200,
            raw_status_name="승인완료" if record["marker"] == "ALL" else "임시저장",
        )


class FakeCoupangLogisticsProvider:
    """실제 출고지/반품지 조회 API를 대신한다."""

    def list_outbound_shipping_places(self):
        return [LogisticsLocation(
            code="88009999", name="[FAKE PACKAGED TEST] 인천 물류센터", usable=True,
        )]

    def list_return_shipping_centers(self):
        return [LogisticsLocation(
            code="RET-PACKAGED-FAKE-001", name="[FAKE PACKAGED TEST] 서울 반품센터",
            usable=True, contact_number="0212345678", zip_code="08029",
            address="서울특별시 강남구 테헤란로 1", address_detail="101호",
        )]


class FakeCoupangCategoryMetadataProvider:
    """실제 카테고리 추천·Metadata 조회 API를 대신한다. 이전 세션(dev
    모드 격리 E2E)에서 이미 검증한 카테고리 80754("주방행주") 구조를
    그대로 재사용해 다중 옵션 조합 UI까지 동일하게 검증한다."""

    def recommend(self, product_name, description="", brand="", attributes=None, seller_sku_code=""):
        return {"display_category_code": "80754", "display_category_name": "주방행주"}

    def get(self, display_category_code):
        return CategoryMetadata(
            display_category_code=str(display_category_code),
            display_category_name="주방행주",
            version="fake-packaged-test-version-1",
            notice_fields=(
                NoticeFieldDefinition("기타 재화::품명 및 모델명", "품명 및 모델명", True),
                NoticeFieldDefinition("기타 재화::재질", "재질", True),
                NoticeFieldDefinition("기타 재화::제조국", "제조국", True),
            ),
            purchase_option_fields=(
                PurchaseOptionAttribute(
                    attribute_type_name="색상", data_type="STRING", basic_unit="",
                    usable_units=(), required=True, input_type="SELECT",
                    input_values=("레드", "블루", "삼색 혼합"), group_number="1",
                    exposed=True,
                ),
                PurchaseOptionAttribute(
                    attribute_type_name="구성", data_type="STRING", basic_unit="매",
                    usable_units=(), required=True, input_type="INPUT",
                    input_values=(), group_number="1", exposed=True,
                ),
            ),
            required_documents=(),
            certifications=(
                CertificationDefinition(
                    certification_type="KC_KID_CERTIFICATION", name="KC인증 어린이제품 안전인증",
                    data_type="CODE", required="OPTIONAL",
                ),
            ),
        )


__all__ = [
    "FakeCoupangLiveProductProvider",
    "FakeCoupangLogisticsProvider",
    "FakeCoupangCategoryMetadataProvider",
]
