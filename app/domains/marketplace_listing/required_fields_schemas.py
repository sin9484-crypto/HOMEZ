"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/required_fields_schemas.py

방식별 필수 입력 — 자유 JSON이 아니라 채널·방식별 엄격한 Pydantic
Schema로 검증한다. 각 Schema는 이번 감사에서 실제로 공식 문서를 직접
fetch해 확인한 필드만 포함한다(확인일 2026-07-31):

- Coupang SELLER_FULFILLED / MARKETPLACE_FULFILLED:
  developers.coupang.com 공식 문서를 이 세션에서 직접 fetch해 확인.
- Naver SELLER_FULFILLED: deliveryType/deliveryAttributeType만 직접
  1차 출처(공식 문서 계열)로 확인됨. 다른 후보 필드(salePrice 등)는
  GitHub Discussion(2차 근거)에서만 나왔으므로 이 Schema에 포함하지
  않는다 — "공식 문서 Gate" 요구사항에 따라 확인되지 않은 필드는
  추측해 넣지 않는다.

모든 Schema는 extra="forbid"다 — 미지원 필드는 기본적으로 거부한다.
금액은 Decimal, 수량은 양의 정수, 중량·크기는 범위 검증한다.
=========================================================
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from app.domains.marketplace_listing.constants import ChannelCode
from app.domains.marketplace_listing.constants import FulfillmentMode


class _StrictModel(BaseModel):

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------
# 쿠팡 — 판매자배송 (marketplaceItemData)
# 출처: developers.coupang.com/hc/en-us/articles/360033877853
# --------------------------------------------------

class CoupangSellerFulfilledItem(_StrictModel):

    # 2026-08-31 Phase 7.6 — 공식 문서(Product Creation) 원문 직접
    # 확인: "Input for each item so that there is no overlap. This is
    # not the option name displayed on the site and may change
    # depending on the buying option." 필수(*), 최대 150자. 빈 값은
    # 허용되지 않는다(min_length=1) — 공식 계약으로 확정된 범위만
    # 반영했다.
    itemName: str = Field(min_length=1, max_length=150)
    # 2026-09-21 옵션 연결 완성 — 위 주석이 "min_length=1, 최대 150자"라고
    # 적고 있었지만 실제 필드에는 제약이 없었다(빈 값·공백 값이 통과).
    # 이 값은 주문 수집에서 channel_sku(주문 품목의 externalVendorSkuCode를
    # strip한 값)로 돌아와 HOMEZ SKU·공급처 옵션 연결의 조인 키가 되므로,
    # 공백이 붙은 값은 주문 측 값과 영원히 일치하지 않는다 — 앞뒤 공백도
    # 저장 시점에 막는다.
    externalVendorSku: str = Field(min_length=1, max_length=150)

    @field_validator("externalVendorSku")
    @classmethod
    def sku_must_be_a_clean_join_key(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError(
                "SKU는 비어 있거나 앞뒤에 공백이 있으면 안 됩니다 — 주문 수집이 "
                "이 값을 공백 없이 읽어 오므로 공백이 붙으면 옵션 연결이 "
                "영원히 맞지 않습니다.",
            )
        return value
    # 2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 공식 문서(Product
    # Creation) 확인 결과 originalPrice/salePrice/maximumBuyCount/
    # unitCount는 상품 전체가 아니라 items[] 각 항목(옵션 조합) 레벨
    # 필드다. 옵션 조합별로 다른 가격·재고를 지정할 수 있도록 선택
    # 필드로 추가한다(없으면 상품 전체 값을 그대로 쓴다 — 기존 단일
    # 옵션 데이터의 동작은 바뀌지 않는다).
    originalPrice: Decimal | None = Field(default=None, gt=0)
    salePrice: Decimal | None = Field(default=None, gt=0)
    maximumBuyCount: int | None = Field(default=None, gt=0)
    unitCount: int | None = Field(default=None, gt=0)
    # 2026-08-29 — 옵션 조합 UI(Section 2A). 이 item이 나타내는 구매옵션
    # 조합의 attributeTypeName→attributeValueName 값이다(HOMEZ 내부
    # 전용 필드 — 쿠팡 payload의 items[].attributes[]는 이 값으로부터
    # Payload Builder가 별도로 만든다). 없으면 단일 옵션/레거시
    # 상품으로 취급해 상품 전체 공유 purchase_options를 쓴다.
    optionAttributes: dict[str, str] | None = None


class CoupangProductImage(_StrictModel):

    imageOrder: int = Field(ge=0)
    imageType: Literal["REPRESENTATION", "DETAIL", "USED_PRODUCT"]
    vendorPath: str | None = None
    cdnPath: str | None = None

    @field_validator("cdnPath", "vendorPath")
    @classmethod
    def trim_image_path(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class CoupangNoticeDetail(_StrictModel):

    noticeCategoryDetailName: str
    content: str


class CoupangNotice(_StrictModel):

    noticeCategoryName: str
    noticeCategoryDetailNames: list[CoupangNoticeDetail] = Field(min_length=1)


class CoupangContentDetail(_StrictModel):
    """
    2026-08-29 — 공식 문서(Product Creation) WebFetch로 확인한
    contents[].contentDetails[] 계약(원문 예시 포함으로 확인). IMAGE
    타입은 별도 URL 필드가 없고 content 문자열 안에 <img src="..."> 로
    이미지 경로가 들어간다(공식 예시 그대로) — Payload Builder가
    제출 직전에 그 URL이 공개 접근 가능한지 별도로 검사한다.
    """

    content: str = Field(..., min_length=1)
    detailType: Literal["IMAGE", "TEXT"]


class CoupangContentEntry(_StrictModel):

    contentsType: Literal[
        "IMAGE", "IMAGE_NO_SPACE", "TEXT", "IMAGE_TEXT", "TEXT_IMAGE",
        "IMAGE_IMAGE", "TEXT_TEXT", "TITLE", "HTML",
    ]
    contentDetails: list[CoupangContentDetail] = Field(min_length=1)


class CoupangSellerFulfilledFields(_StrictModel):

    # Coupang Product Creation 공식 계약에 공개된 값만 허용한다.
    # 국내 일반배송 상품은 SEQUENCIAL이며 AGENT_BUY는 해외구매대행이다.
    deliveryMethod: Literal[
        "SEQUENCIAL", "COLD_FRESH", "MAKE_ORDER", "AGENT_BUY",
        "VENDOR_DIRECT",
    ]
    deliveryCompanyCode: str | None = None
    deliveryChargeType: Literal[
        "FREE", "NOT_FREE", "CHARGE_RECEIVED", "CONDITIONAL_FREE",
    ]
    deliveryCharge: Decimal = Field(ge=0)
    deliveryChargeOnReturn: Decimal = Field(ge=0)
    returnCharge: Decimal = Field(ge=0)
    returnCenterCode: str
    outboundShippingPlaceCode: str
    returnChargeName: str
    companyContactNumber: str
    returnZipCode: str
    returnAddress: str
    returnAddressDetail: str | None = None
    items: list[CoupangSellerFulfilledItem] = Field(min_length=1)
    originalPrice: Decimal = Field(gt=0)
    salePrice: Decimal = Field(gt=0)
    maximumBuyCount: int = Field(gt=0)
    # Category Metadata 확인이 완료된 경우 Listing Wizard가 제출
    # payload와 동일한 공식 카테고리/고시정보를 이 계약에 포함한다.
    displayCategoryCode: int | None = Field(default=None, gt=0)
    notices: list[CoupangNotice] | None = Field(default=None, min_length=1)
    # 쿠팡 상품 생성은 로컬 파일 경로를 받지 않는다. Live 전송 전
    # 검사에서 공개 vendorPath 또는 쿠팡 cdnPath와 대표 이미지 존재를
    # 별도로 fail-closed 검증한다.
    images: list[CoupangProductImage] = Field(default_factory=list)
    # 외부 이미지 URL 사용권 확인도 selection fingerprint에 고정한다.
    # 이 HOMEZ 전용 값 자체는 쿠팡 payload로 전송하지 않는다.
    liveImageRightsConfirmed: bool = False
    maximumBuyForPerson: int = Field(default=0, ge=0)
    outboundShippingTimeDay: int = Field(default=1, gt=0)
    unitCount: int = Field(default=1, gt=0)
    adultOnly: Literal["EVERYONE", "ADULT_ONLY"] = "EVERYONE"
    taxType: Literal["TAX", "FREE"] = "TAX"
    parallelImported: Literal[
        "PARALLEL_IMPORTED", "NOT_PARALLEL_IMPORTED",
    ] = "NOT_PARALLEL_IMPORTED"
    overseasPurchased: Literal[
        "OVERSEAS_PURCHASED", "NOT_OVERSEAS_PURCHASED",
    ] = "NOT_OVERSEAS_PURCHASED"
    pccNeeded: bool = False
    barcode: str | None = None
    emptyBarcodeReason: str | None = None
    modelNo: str | None = None
    certifications: list[dict] = Field(default_factory=list)
    # 2026-08-29 — 공식 문서로 확인한 정확한 구조로 교체(이전에는
    # `list[dict]`로만 선언해 어떤 모양이든 통과시켰다 — Phase 3 감사
    # 결함 V7-COUPANG-PAYLOAD-005의 수정).
    contents: list[CoupangContentEntry] = Field(default_factory=list)
    freeShipOverAmount: Decimal = Field(default=0, ge=0)
    remoteAreaDeliverable: Literal["Y", "N"] = "N"
    unionDeliveryType: str = "UNION_DELIVERY"
    vendorUserId: str | None = None
    # 2026-08-30 V7 안정화 Phase 3 — 브랜드 3상태 계약(감사 F-02 수정).
    # "무브랜드" 같은 임의 문자열을 그대로 brand에 실어 보내던 기존
    # 방식을 대체한다. HOMEZ 전용 상태값이라 이 필드들 자체는 쿠팡
    # payload로 전송하지 않는다(coupang_live_payload.py가 이 상태를
    # 읽어 실제 brand/brandId를 구성한다). UNRESOLVED가 기본값이며,
    # 제출 계약(coupang_submission_contract.py)이 UNRESOLVED를 항상
    # 차단한다 — 값을 추측해서 자동으로 넘기지 않는다.
    brandState: Literal["NO_BRAND", "OFFICIAL_BRAND", "UNRESOLVED"] = "UNRESOLVED"
    # NO_BRAND 확정 시 실제로 전송할 표기(사용자가 직접 확인한 값).
    # brandState가 OFFICIAL_BRAND면 officialBrandName을 대신 쓴다 —
    # coupang_submission_contract.py/coupang_live_payload.py 둘 다
    # 이 규칙을 그대로 따른다.
    brand: str | None = None
    brandId: str | None = None
    officialBrandName: str | None = None
    brandEnrollmentStatus: str | None = None
    brandLookupFingerprint: str | None = None
    # 2026-08-31 V7 후속 결함 수정(Phase 7.5 브라우저 E2E) — contents를
    # "선택한 이미지로 상세설명 구성" 버튼으로 만들 때 어떤 media_asset
    # id들을 골랐는지 화면 복원용으로만 기억한다. brandState 등과 같은
    # 원칙 — HOMEZ 전용 값이라 이 필드 자체는 쿠팡 payload로 전송하지
    # 않는다(coupang_live_payload.py는 이 키를 읽지 않는다).
    contentsSourceAssetIds: list[int] = Field(default_factory=list)
    # 2026-08-29 V7 종합 감사 Phase 4(V7-COUPANG-PAYLOAD-001 수정) —
    # 공식 문서(Product Creation) 루트 레벨 선택 필드, brand와 별개
    # 개념("제조사"). 2026-08-28 감사에서 이 필드 자체가 payload에
    # 없다는 것을 코드로 확인했다 — WING 화면(z5)은 "제조사"를 별도
    # 필수 표시(●) 없는 일반 입력칸으로 요구한다.
    manufacture: str | None = None

    @field_validator(
        "returnCenterCode", "outboundShippingPlaceCode", "returnChargeName",
        "companyContactNumber", "returnZipCode", "returnAddress",
        "returnAddressDetail",
    )
    @classmethod
    def reject_placeholder_values(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized or (normalized.startswith("<") and normalized.endswith(">")):
            raise ValueError("실제 출고·반품 정보를 입력해야 합니다.")
        return normalized

    @model_validator(mode="after")
    def reject_duplicate_item_sku(self) -> "CoupangSellerFulfilledFields":
        """
        2026-08-29 쿠팡 상품등록 핵심 차단 해결 — 옵션 조합별 SKU를
        지원하도록 items[]가 여러 행을 가질 수 있게 됐다(가격·재고
        오버라이드 추가와 같은 작업). 같은 externalVendorSku가 두
        옵션 행에 중복되면 쿠팡이 어느 옵션의 재고·주문인지 구분할
        수 없다 — 저장 시점에 미리 막는다.

        2026-08-31 Phase 7.6 — 공식 문서(Product Creation) 원문 확인
        결과 itemName도 "Input for each item so that there is no
        overlap"(겹치지 않게 입력)이 명시된 필수 계약이다 — SKU와
        동일한 원칙으로 중복을 막는다.
        """

        seen: set[str] = set()
        duplicates: set[str] = set()
        seen_names: set[str] = set()
        duplicate_names: set[str] = set()
        seen_combinations: set[tuple] = set()
        for item in self.items:
            sku = item.externalVendorSku
            if sku in seen:
                duplicates.add(sku)
            seen.add(sku)
            if item.itemName in seen_names:
                duplicate_names.add(item.itemName)
            seen_names.add(item.itemName)
            if item.optionAttributes:
                combo = tuple(sorted(item.optionAttributes.items()))
                if combo in seen_combinations:
                    raise ValueError(
                        "동일한 구매옵션 조합이 두 번 이상 등록되었습니다: "
                        + str(dict(combo)),
                    )
                seen_combinations.add(combo)
        if duplicates:
            raise ValueError(
                "옵션 간 SKU가 중복되었습니다: " + ", ".join(sorted(duplicates)),
            )
        if duplicate_names:
            raise ValueError(
                "옵션 간 옵션명(itemName)이 중복되었습니다 — 옵션을 구분할 "
                "수 있는 이름으로 각각 다르게 입력하세요: "
                + ", ".join(sorted(duplicate_names)),
            )
        return self


# --------------------------------------------------
# 쿠팡 — 로켓그로스 (rocketGrowthItemData +
# rocketGrowthAdditionalInformation)
# 출처: developers.coupang.com/hc/en-us/articles/39406974365849
# --------------------------------------------------

class CoupangRocketGrowthPriceData(_StrictModel):

    salePrice: Decimal = Field(gt=0)
    originalPrice: Decimal = Field(gt=0)


class CoupangRocketGrowthSkuInfo(_StrictModel):

    weight: Decimal = Field(gt=0)  # g
    netWeight: Decimal | None = Field(default=None, gt=0)  # g
    width: Decimal = Field(gt=0)  # mm
    length: Decimal = Field(gt=0)  # mm
    height: Decimal = Field(gt=0)  # mm
    fragile: bool
    # 공식 문서: 로켓그로스는 반드시 1이어야 한다("should be 1 for
    # Rocket Growth all the time").
    quantityPerBox: Literal[1]


class CoupangRocketGrowthAdditionalInfo(_StrictModel):

    rfmInboundName: str
    # 공식 문서: 반드시 "AGREE"여야 한다 — 로켓그로스 취급 조건 동의.
    legalAgreement: Literal["AGREE"]


class CoupangRocketGrowthFields(_StrictModel):

    barcode: str
    externalVendorSku: str | None = None
    modelNo: str | None = None
    priceData: CoupangRocketGrowthPriceData
    skuInfo: CoupangRocketGrowthSkuInfo
    distributionPeriod: int = Field(gt=0)
    expiredAtManaged: bool
    rocketGrowthAdditionalInformation: CoupangRocketGrowthAdditionalInfo


# --------------------------------------------------
# 네이버 스마트스토어 — 판매자배송
# 출처: deliveryType/deliveryAttributeType만 1차 출처로 확인됨.
# --------------------------------------------------

class NaverSellerFulfilledFields(_StrictModel):
    """
    공식 문서 Gate: 이 Schema는 의도적으로 최소한이다. 다른 후보
    필드(salePrice, stockQuantity 등)는 이번 세션에서 1차 공식 출처로
    확인하지 못했다(GitHub Discussion에서만 확인 — 2차 근거로만 취급).
    doc_verification_status="PARTIAL"을 이 Schema 확장으로 임의로
    올리지 않는다.
    """

    deliveryType: Literal["DELIVERY"] = "DELIVERY"
    deliveryAttributeType: Literal["NORMAL"] = "NORMAL"


# --------------------------------------------------
# 레지스트리 — (channel_code, fulfillment_mode) -> (schema, name, version)
# --------------------------------------------------

REQUIRED_FIELDS_SCHEMA_REGISTRY: dict[
    tuple[str, str], tuple[type[BaseModel], str, str],
] = {
    (ChannelCode.COUPANG, FulfillmentMode.SELLER_FULFILLED): (
        CoupangSellerFulfilledFields, "coupang_seller_fulfilled", "1.0.0",
    ),
    (ChannelCode.COUPANG, FulfillmentMode.MARKETPLACE_FULFILLED): (
        CoupangRocketGrowthFields, "coupang_rocket_growth", "1.0.0",
    ),
    (ChannelCode.NAVER_SMARTSTORE, FulfillmentMode.SELLER_FULFILLED): (
        NaverSellerFulfilledFields, "naver_seller_fulfilled", "1.0.0",
    ),
}


def get_required_fields_schema(
    channel_code: str, fulfillment_mode: str,
) -> tuple[type[BaseModel], str, str] | None:

    return REQUIRED_FIELDS_SCHEMA_REGISTRY.get(
        (channel_code, fulfillment_mode),
    )


__all__ = [
    "CoupangSellerFulfilledItem",
    "CoupangProductImage",
    "CoupangNoticeDetail",
    "CoupangNotice",
    "CoupangSellerFulfilledFields",
    "CoupangRocketGrowthPriceData",
    "CoupangRocketGrowthSkuInfo",
    "CoupangRocketGrowthAdditionalInfo",
    "CoupangRocketGrowthFields",
    "NaverSellerFulfilledFields",
    "REQUIRED_FIELDS_SCHEMA_REGISTRY",
    "get_required_fields_schema",
]
