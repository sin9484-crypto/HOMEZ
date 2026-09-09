"""
=========================================================
Homez OS

File : app/domains/marketplace_listing/capability_registry.py

정적 시딩 입력값 — 실제 판매 방식 노출 여부를 런타임에 결정하지
않는다. seed_capability_registry()(service.py)가 이 dict를 읽어
MarketplaceFulfillmentCapability 행을 status=DRAFT로 생성/갱신할 뿐,
이 dict 자체는 어떤 API 응답에도 직접 노출되지 않는다 — 실제 노출
가능 여부는 항상 DB의 status=VERIFIED 여부로만 판단한다.

각 항목은 실제 공식 문서 조사 결과를 반영한다(2026-07-31 조사):

- COUPANG: developers.coupang.com 공식 문서 확인 — 상품 생성 API는
  단일 enum 필드가 아니라 marketplaceItemData / rocketGrowthItemData
  객체의 존재 여부로 판매 방식을 구분한다. 로켓그로스는
  rocketGrowthAdditionalInformation.legalAgreement="AGREE" + WING에서
  "로켓그로스 상품 생성 API 및 검수기준 동의" 사전 동의가 필요함을
  확인했다. 로켓배송(직매입)은 일반 판매자 상품등록 Open API 범위
  밖임을 확인했다(자기 신청 경로 없음, 별도 계약 필요) — 따라서
  is_supported=False로 영구 비활성 등록한다.
- NAVER_SMARTSTORE: 네이버 공식 GitHub(commerce-api-naver/commerce-api)
  로 판매자배송(deliveryType=DELIVERY, deliveryAttributeType=NORMAL)만
  확인했다. 네이버 자체 풀필먼트 프로그램의 API 필드 수준 확인은
  이번 세션에서 하지 못했으므로 등록하지 않는다.
- ELEVENST(11번가): 이번 세션에서 필드 수준 공식 문서를 확인하지
  못했다 — 채널 자체는 등록하되(doc_verification_status=UNVERIFIED)
  방식은 0개로 둔다.
=========================================================
"""

from app.domains.marketplace_listing.constants import ChannelCode
from app.domains.marketplace_listing.constants import (
    ChannelDocVerificationStatus,
)
from app.domains.marketplace_listing.constants import FulfillmentMode

CAPABILITY_REGISTRY_VERSION = "1.0.0"

CHANNEL_SEED = {
    ChannelCode.COUPANG: {
        "name": "쿠팡",
        "doc_verification_status": ChannelDocVerificationStatus.VERIFIED,
        "doc_source_reference": (
            "https://developers.coupang.com/hc/en-us/articles/"
            "39406974365849-Product-Creation-Rocket-Growth-"
            "Rocket-Growth-Marketplace-Hybrid-Products (확인일 2026-07-31)"
        ),
    },
    ChannelCode.NAVER_SMARTSTORE: {
        "name": "네이버 스마트스토어",
        "doc_verification_status": ChannelDocVerificationStatus.PARTIAL,
        "doc_source_reference": (
            "https://github.com/commerce-api-naver/commerce-api "
            "discussions #246 (확인일 2026-07-31, 판매자배송 필드만 확인 — "
            "네이버 자체 풀필먼트 프로그램은 필드 수준 미확인)"
        ),
    },
    ChannelCode.ELEVENST: {
        "name": "11번가",
        "doc_verification_status": ChannelDocVerificationStatus.UNVERIFIED,
        "doc_source_reference": (
            "openapi.11st.co.kr — 이번 세션에서 필드 수준 공식 문서를 "
            "확인하지 못함(2026-07-31). 후속 조사 필요, 그 전까지 방식 "
            "0개 유지."
        ),
    },
}

# {channel_code: {fulfillment_mode: {...}}}
CAPABILITY_SEED = {
    ChannelCode.COUPANG: {
        FulfillmentMode.SELLER_FULFILLED: {
            "external_display_name": "판매자배송",
            "is_supported": True,
            "requires_eligibility_check": False,
            "requires_account_contract": False,
            "schema_name": "coupang_seller_fulfilled",
            "schema_version": "1.0.0",
            "doc_source_reference": (
                "developers.coupang.com — marketplaceItemData 객체 "
                "(확인일 2026-07-31)"
            ),
        },
        FulfillmentMode.MARKETPLACE_FULFILLED: {
            "external_display_name": "로켓그로스",
            "is_supported": True,
            "requires_eligibility_check": True,
            "requires_account_contract": False,
            "schema_name": "coupang_rocket_growth",
            "schema_version": "1.0.0",
            "doc_source_reference": (
                "developers.coupang.com — rocketGrowthItemData, "
                "rocketGrowthAdditionalInformation.legalAgreement=AGREE "
                "+ WING 사전 동의 필요 (확인일 2026-07-31)"
            ),
        },
        FulfillmentMode.DIRECT_PURCHASE: {
            "external_display_name": "로켓배송(직매입)",
            # 일반 판매자 상품등록 Open API 범위 밖임을 공식 확인 —
            # 실제 계약 검증 경로가 구현되기 전까지 영구 비활성.
            "is_supported": False,
            "requires_eligibility_check": True,
            "requires_account_contract": True,
            # is_supported=False라 선택 자체가 불가능하므로 Schema가
            # 없다 — None으로 남긴다(추측 스키마를 만들지 않는다).
            "schema_name": None,
            "schema_version": None,
            "doc_source_reference": (
                "확인됨: 일반 판매자 상품등록 Open API 범위 밖 — "
                "seller-facing 자기 신청 경로 없음(별도 계약 필요, "
                "확인일 2026-07-31)"
            ),
        },
    },
    ChannelCode.NAVER_SMARTSTORE: {
        FulfillmentMode.SELLER_FULFILLED: {
            "external_display_name": "판매자배송",
            "is_supported": True,
            "requires_eligibility_check": False,
            "requires_account_contract": False,
            "schema_name": "naver_seller_fulfilled",
            "schema_version": "1.0.0",
            "doc_source_reference": (
                "github.com/commerce-api-naver/commerce-api #246 — "
                "deliveryType=DELIVERY, deliveryAttributeType=NORMAL "
                "(확인일 2026-07-31)"
            ),
        },
        # 다른 방식(네이버 자체 풀필먼트 등)은 공식 문서에서 필드 수준으로
        # 확인하지 못했으므로 등록하지 않는다 — 추측 금지.
    },
    ChannelCode.ELEVENST: {
        # 필드 수준 공식 문서 확인 불가 — 방식 0개.
    },
}


__all__ = [
    "CAPABILITY_REGISTRY_VERSION",
    "CHANNEL_SEED",
    "CAPABILITY_SEED",
]
