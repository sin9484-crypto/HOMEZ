"""
=========================================================
Homez OS

File : app/domains/channel_policy/rule_catalog.py

채널 정책 규칙 카탈로그 — 실제 공식 채널 정책·API 문서를 이 세션에서
직접 조회해 확인한 규칙만 담는다(확인일 2026-08-21). 블로그·카페·
검색결과 요약·출처 불명 게시물은 근거로 쓰지 않는다.

=== 쿠팡 ===
- "판매 불가 품목 안내"(marketplace.coupang.com/information-center/
  blog-news7) — 쿠팡 마켓플레이스 공식 정보센터, 절대 판매 금지
  품목·취급주의(허가/신고 필요) 품목·정책상 판매 금지 품목 3단 구조를
  이 세션에서 WebFetch로 직접 확인.
- "쿠팡 판매자 필수 확인! 상품 정보 입력, 무엇이 달라졌나요?"
  (marketplace.coupang.com/information-center/
  marketplace3p-product-info-update-uid) — 브랜드/구매옵션/상품
  식별번호(품번·바코드) 필수 입력을 이 세션에서 WebFetch로 직접 확인.
- "Category Metadata Query"(developers.coupang.com 공식 Open API
  문서, GET /v2/providers/seller_api/apis/api/v1/marketplace/meta/
  category-related-metas/display-category-codes/{code}) — 응답에
  noticeCategories(고시정보)/requiredDocumentNames(필수구비서류)/
  certifications(인증정보)가 카테고리별로 배열로 반환됨을 이 세션에서
  WebFetch로 직접 확인. 예시 고시정보 항목(품명 및 모델명/출시년월/
  KC 인증 필 유무/제조국/크기/적용차종/품질보증기준/A/S 책임자와
  전화번호)도 문서에서 그대로 확인.

=== 네이버 스마트스토어 ===
이 세션에서 safety.smartstore.naver.com과 developers.commerce.
naver.com에 직접 접근을 시도했으나 둘 다 차단되어 새로운 1차 근거를
확보하지 못했다(추측 금지 원칙에 따라 새 규칙을 추가하지 않는다).
유일하게 활성화하는 규칙(NAVER_DELIVERY_TYPE_REQUIRED)은 기존
required_fields_schemas.py::NaverSellerFulfilledFields가 이미
1차 출처로 확인해 반영한 deliveryType/deliveryAttributeType 계약을
그대로 재사용한다(확인일 2026-07-31, 새로 조사한 근거 아님 — 기존
검증된 사실 재인용). 그 외 네이버 항목은 전부 POLICY_EVIDENCE_
REQUIRED로 남긴다.
=========================================================
"""

from datetime import datetime

from app.domains.channel_policy.constants import PolicySeverity
from app.domains.channel_policy.constants import PolicyValidationType

CHANNEL_POLICY_PROFILE_VERSION = "2026.08.21.1"

_VERIFIED_AT = datetime(2026, 8, 21)
_NAVER_FIELD_VERIFIED_AT = datetime(2026, 7, 31)

_COUPANG_PROHIBITED_ITEMS_URL = (
    "https://marketplace.coupang.com/information-center/blog-news7"
)
_COUPANG_PROHIBITED_ITEMS_TITLE = "쿠팡 마켓플레이스 — 판매 불가 품목 안내"

_COUPANG_PRODUCT_INFO_URL = (
    "https://marketplace.coupang.com/information-center/"
    "marketplace3p-product-info-update-uid"
)
_COUPANG_PRODUCT_INFO_TITLE = (
    "쿠팡 마켓플레이스 — 상품 정보 입력 필수 항목 안내"
)

_COUPANG_CATEGORY_METADATA_URL = (
    "https://developers.coupang.com/hc/en-us/articles/"
    "360034035713-Category-Metadata-Query"
)
_COUPANG_CATEGORY_METADATA_TITLE = (
    "쿠팡 Open API 공식문서 — Category Metadata Query"
)

_NAVER_DELIVERY_TYPE_URL = (
    "https://github.com/commerce-api-naver/commerce-api/discussions/246"
)
_NAVER_DELIVERY_TYPE_TITLE = (
    "네이버 커머스 API 공식 GitHub — commerce-api-naver/commerce-api "
    "discussions #246(deliveryType/deliveryAttributeType, 확인일 "
    "2026-07-31, required_fields_schemas.py 기존 검증 재인용)"
)

# --------------------------------------------------
# 규칙 카탈로그 — (channel, rule_code) 당 1건.
# 각 항목: category_scope, severity, validation_type,
#          required_fields, required_evidence,
#          official_source_url, source_title, verified_at, active
# --------------------------------------------------

CHANNEL_POLICY_RULE_CATALOG: list[dict] = [
    # ---------------- 쿠팡 ----------------
    {
        "channel": "COUPANG",
        "rule_code": "PROHIBITED_CATEGORY_ABSOLUTE",
        "category_scope": [
            "마약", "항정신성", "대마", "총", "포", "도검", "화약",
            "모의총포", "경찰제복", "경찰장비", "군복", "군용", "전투복",
            "멸종위기", "처방약", "전문의약품", "동물용의약품",
            "주류", "소주", "맥주", "위스키", "와인", "고량주",
            "담배", "전자담배", "콘택트렌즈", "시력 보정용 안경",
            "불법복제", "미심의 영상물", "몰래카메라", "위장형 카메라",
            "욱일승천기", "나치 문양", "리콜",
        ],
        "severity": PolicySeverity.BLOCKING,
        "validation_type": PolicyValidationType.CATEGORY_PROHIBITED,
        "required_fields": None,
        "required_evidence": None,
        "official_source_url": _COUPANG_PROHIBITED_ITEMS_URL,
        "source_title": _COUPANG_PROHIBITED_ITEMS_TITLE,
        "verified_at": _VERIFIED_AT,
        "active": True,
    },
    {
        "channel": "COUPANG",
        "rule_code": "PROHIBITED_ORIGIN_COUNTRY",
        "category_scope": ["ALL"],
        "severity": PolicySeverity.BLOCKING,
        "validation_type": PolicyValidationType.ORIGIN_COUNTRY_PROHIBITED,
        # required_fields_json을 이 validation_type에서는 "금지 원산지
        # 값 목록"으로 재해석한다(engine.py 참고) — 별도 컬럼을 추가하지
        # 않기 위한 최소 스키마 설계.
        "required_fields": ["IRAN", "NORTH_KOREA", "CUBA"],
        "required_evidence": None,
        "official_source_url": _COUPANG_PROHIBITED_ITEMS_URL,
        "source_title": _COUPANG_PROHIBITED_ITEMS_TITLE,
        "verified_at": _VERIFIED_AT,
        "active": True,
    },
    {
        "channel": "COUPANG",
        "rule_code": "RESTRICTED_ORIGIN_COUNTRY_EVIDENCE",
        "category_scope": ["ALL"],
        "severity": PolicySeverity.ACTION_REQUIRED,
        "validation_type": PolicyValidationType.ORIGIN_COUNTRY_PROHIBITED,
        "required_fields": [
            "RUSSIA", "VENEZUELA", "MYANMAR", "SUDAN", "SYRIA",
        ],
        "required_evidence": ["제재 예외 확인 및 원산지 증빙 서류"],
        "official_source_url": _COUPANG_PROHIBITED_ITEMS_URL,
        "source_title": _COUPANG_PROHIBITED_ITEMS_TITLE,
        "verified_at": _VERIFIED_AT,
        "active": True,
    },
    {
        "channel": "COUPANG",
        "rule_code": "RESTRICTED_CATEGORY_CERTIFICATION_REQUIRED",
        "category_scope": [
            "의료기기", "전기용품", "생활용품", "어린이제품",
            "방송통신기자재", "정수기", "가스용품", "고압가스",
            "소방용품", "자동차부품", "화장품", "식품", "의약외품",
        ],
        "severity": PolicySeverity.ACTION_REQUIRED,
        "validation_type": PolicyValidationType.CATEGORY_RESTRICTED_EVIDENCE,
        "required_fields": None,
        "required_evidence": ["KC 인증서 또는 해당 품목 안전인증 서류"],
        "official_source_url": _COUPANG_PROHIBITED_ITEMS_URL,
        "source_title": _COUPANG_PROHIBITED_ITEMS_TITLE,
        "verified_at": _VERIFIED_AT,
        "active": True,
    },
    {
        "channel": "COUPANG",
        "rule_code": "ADULT_ONLY_LABEL_REQUIRED",
        "category_scope": ["성인용품", "레이저포인터", "성인용 비비탄"],
        "severity": PolicySeverity.ACTION_REQUIRED,
        "validation_type": PolicyValidationType.CATEGORY_RESTRICTED_EVIDENCE,
        "required_fields": None,
        "required_evidence": ["19금 성인 인증 표시"],
        "official_source_url": _COUPANG_PROHIBITED_ITEMS_URL,
        "source_title": _COUPANG_PROHIBITED_ITEMS_TITLE,
        "verified_at": _VERIFIED_AT,
        "active": True,
    },
    {
        "channel": "COUPANG",
        "rule_code": "PRODUCT_IDENTIFICATION_REQUIRED",
        "category_scope": ["ALL"],
        "severity": PolicySeverity.BLOCKING,
        "validation_type": PolicyValidationType.STRUCTURAL_FIELD_REQUIRED,
        "required_fields": ["brand", "purchase_options", "product_identifier"],
        "required_evidence": None,
        "official_source_url": _COUPANG_PRODUCT_INFO_URL,
        "source_title": _COUPANG_PRODUCT_INFO_TITLE,
        "verified_at": _VERIFIED_AT,
        "active": True,
    },
    {
        "channel": "COUPANG",
        "rule_code": "CATEGORY_NOTICE_INFO_REQUIRED",
        "category_scope": ["ALL"],
        "severity": PolicySeverity.ACTION_REQUIRED,
        "validation_type": PolicyValidationType.EVIDENCE_REQUIRED_GENERIC,
        "required_fields": None,
        "required_evidence": [
            "상품정보제공고시(카테고리별 고시정보 — 예: 품명 및 "
            "모델명/출시년월/KC 인증 필 유무/제조국/크기/적용차종/"
            "품질보증기준/A/S 책임자와 전화번호 — 정확한 목록은 쿠팡 "
            "Category Metadata Query API로 카테고리별 조회 필요)",
        ],
        "official_source_url": _COUPANG_CATEGORY_METADATA_URL,
        "source_title": _COUPANG_CATEGORY_METADATA_TITLE,
        "verified_at": _VERIFIED_AT,
        "active": True,
    },

    # ---------------- 네이버 스마트스토어 ----------------
    {
        "channel": "NAVER_SMARTSTORE",
        "rule_code": "NAVER_DELIVERY_TYPE_REQUIRED",
        "category_scope": ["ALL"],
        "severity": PolicySeverity.BLOCKING,
        "validation_type": PolicyValidationType.STRUCTURAL_FIELD_REQUIRED,
        "required_fields": ["deliveryType", "deliveryAttributeType"],
        "required_evidence": None,
        "official_source_url": _NAVER_DELIVERY_TYPE_URL,
        "source_title": _NAVER_DELIVERY_TYPE_TITLE,
        "verified_at": _NAVER_FIELD_VERIFIED_AT,
        "active": True,
    },
    {
        "channel": "NAVER_SMARTSTORE",
        "rule_code": "NAVER_PROHIBITED_CATEGORY_PLACEHOLDER",
        "category_scope": ["ALL"],
        "severity": PolicySeverity.BLOCKING,
        "validation_type": PolicyValidationType.CATEGORY_PROHIBITED,
        "required_fields": None,
        "required_evidence": None,
        "official_source_url": None,
        "source_title": (
            "POLICY_EVIDENCE_REQUIRED — 2026-08-21 이 세션에서 "
            "safety.smartstore.naver.com 접근이 차단되어(WebFetch "
            "실패) 네이버 공식 판매금지 상품 목록을 1차 출처로 확인하지 "
            "못했다. 간접 검색 결과만으로 규칙을 채우지 않는다 — "
            "공식 문서 확인 후 별도로 활성화해야 한다."
        ),
        "verified_at": None,
        "active": False,
    },
    {
        "channel": "NAVER_SMARTSTORE",
        "rule_code": "NAVER_CATEGORY_NOTICE_INFO_PLACEHOLDER",
        "category_scope": ["ALL"],
        "severity": PolicySeverity.ACTION_REQUIRED,
        "validation_type": PolicyValidationType.EVIDENCE_REQUIRED_GENERIC,
        "required_fields": None,
        "required_evidence": None,
        "official_source_url": None,
        "source_title": (
            "POLICY_EVIDENCE_REQUIRED — 2026-08-21 이 세션에서 "
            "developers.commerce.naver.com 접근이 차단되어 카테고리별 "
            "상품정보제공고시 요구사항을 1차 출처로 확인하지 못했다."
        ),
        "verified_at": None,
        "active": False,
    },
]


__all__ = [
    "CHANNEL_POLICY_PROFILE_VERSION",
    "CHANNEL_POLICY_RULE_CATALOG",
]
