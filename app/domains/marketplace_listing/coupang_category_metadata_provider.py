"""Official Coupang category recommendation/metadata adapter.

No call happens at import or startup.  The UI endpoints invoke this adapter
only after an authenticated user explicitly requests a recommendation.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from app.domains.marketplace_listing.category_metadata import (
    CategoryMetadata, NoticeFieldDefinition, PurchaseOptionAttribute,
    RequiredDocumentDefinition, CertificationDefinition,
)
from app.domains.store_connection.adapters.coupang_signing import (
    build_authorization_header,
)

BASE_URL = "https://api-gateway.coupang.com"
RECOMMEND_PATH = "/v2/providers/openapi/apis/api/v1/categorization/predict"
METADATA_PATH = (
    "/v2/providers/seller_api/apis/api/v1/marketplace/meta/"
    "category-related-metas/display-category-codes/{code}"
)


class CategoryMetadataProviderError(RuntimeError):
    pass


class CoupangCategoryMetadataProvider:
    def __init__(self, credential: dict, timeout: int = 10):
        self.access_key = credential.get("access_key", "")
        self.secret_key = credential.get("secret_key", "")
        if not self.access_key or not self.secret_key:
            raise CategoryMetadataProviderError("쿠팡 API 자격증명이 준비되지 않았습니다.")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        encoded = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        auth = build_authorization_header(
            self.access_key, self.secret_key, method, path, "",
            now=datetime.now(timezone.utc),
        )
        request = urllib.request.Request(
            BASE_URL + path, data=encoded, method=method,
            headers={"Authorization": auth, "Content-Type": "application/json;charset=UTF-8"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Status code alone is safe to expose. Never include response bodies,
            # headers, request authorization, or credentials.
            raise CategoryMetadataProviderError(
                f"쿠팡 카테고리 조회에 실패했습니다 (HTTP {exc.code}).",
            ) from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise CategoryMetadataProviderError(
                "쿠팡 카테고리 서비스에 연결하지 못했습니다.",
            ) from exc

    def recommend(self, product_name: str, description: str = "", brand: str = "", attributes: dict | None = None, seller_sku_code: str = "") -> dict:
        response = self._request("POST", RECOMMEND_PATH, {
            "productName": product_name,
            "productDescription": description,
            "brand": brand,
            "attributes": attributes or {},
            "sellerSkuCode": seller_sku_code,
        })
        data = response.get("data") or {}
        if data.get("autoCategorizationPredictionResultType") != "SUCCESS":
            raise CategoryMetadataProviderError("정확한 카테고리를 추천할 정보가 부족합니다.")
        return {
            "display_category_code": str(data.get("predictedCategoryId") or ""),
            "display_category_name": str(data.get("predictedCategoryName") or ""),
        }

    def get(self, display_category_code: str) -> CategoryMetadata:
        if not display_category_code.isdigit():
            raise CategoryMetadataProviderError("카테고리 코드는 숫자여야 합니다.")
        response = self._request("GET", METADATA_PATH.format(code=display_category_code))
        data = response.get("data") or {}
        notices: list[NoticeFieldDefinition] = []
        for category in data.get("noticeCategories") or []:
            category_name = str(category.get("noticeCategoryName") or "")
            for item in category.get("noticeCategoryDetailNames") or []:
                label = str(item.get("noticeCategoryDetailName") or "").strip()
                if label:
                    notices.append(NoticeFieldDefinition(
                        key=f"{category_name}::{label}", label=label,
                        required=item.get("required") == "MANDATORY",
                    ))
        # 2026-08-29 Phase 4(V7-COUPANG-META-001 수정) — 이전에는
        # attributeTypeName/required만 저장하고 dataType/basicUnit/
        # usableUnits/inputType/inputValues/groupNumber/exposed를
        # 버렸다(2026-08-28 감사에서 코드로 확인된 결함). 공식 응답
        # 필드를 그대로 보존한다 — 값을 추측·가공하지 않는다.
        options = tuple(
            PurchaseOptionAttribute(
                attribute_type_name=str(item.get("attributeTypeName") or ""),
                data_type=str(item.get("dataType") or ""),
                basic_unit=str(item.get("basicUnit") or ""),
                usable_units=tuple(
                    str(u) for u in (item.get("usableUnits") or [])
                ),
                required=item.get("required") == "MANDATORY",
                input_type=str(item.get("inputType") or ""),
                input_values=tuple(
                    str(v) for v in (item.get("inputValues") or [])
                ),
                group_number=str(item.get("groupNumber") or "NONE"),
                exposed=item.get("exposed") == "EXPOSED",
            )
            for item in (data.get("attributes") or [])
            if item.get("attributeTypeName")
        )
        # 2026-08-29 Phase 4(V7-COUPANG-META-002 수정) — 이전에는
        # requiredDocumentNames/certifications 응답 자체를 저장하지
        # 않았다(감사에서 확인된 결함).
        required_documents = tuple(
            RequiredDocumentDefinition(
                template_name=str(item.get("templateName") or ""),
                required=str(item.get("required") or "OPTIONAL"),
            )
            for item in (data.get("requiredDocumentNames") or [])
            if item.get("templateName")
        )
        certifications = tuple(
            CertificationDefinition(
                certification_type=str(item.get("certificationType") or ""),
                name=str(item.get("name") or ""),
                data_type=str(item.get("dataType") or "NONE"),
                required=str(item.get("required") or "OPTIONAL"),
            )
            for item in (data.get("certifications") or [])
            if item.get("certificationType")
        )
        # Coupang does not expose a separate metadata version.  The canonical
        # response-derived hash is used as the immutable version/fingerprint.
        canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        import hashlib
        version = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return CategoryMetadata(
            display_category_code=display_category_code,
            display_category_name="",
            version=version, notice_fields=tuple(notices),
            purchase_option_fields=options,
            required_documents=required_documents,
            certifications=certifications,
        )


__all__ = ["CoupangCategoryMetadataProvider", "CategoryMetadataProviderError"]
