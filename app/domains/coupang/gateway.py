"""
=========================================================
Homez OS

File : app/domains/coupang/gateway.py

쿠팡 API Gateway — Interface/Adapter 분리.

이번 단계는 Dry Run만 허용한다:
  - 외부 네트워크 호출 금지(이 파일은 requests/httpx 등 어떤 HTTP
    라이브러리도 import하지 않는다)
  - Access Key/Secret Key를 요구하지 않는다(함수 시그니처 자체에
    자격증명 파라미터가 없다)
  - 가짜 성공 응답을 만들지 않는다 — Payload 구조·필수 필드만
    검증하고, 결과는 그 검증 결과 그대로다
=========================================================
"""

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Protocol

from app.domains.coupang.constants import DryRunOutcome

# 쿠팡 상품등록 Payload 필수 필드(Dry Run 검증 대상).
# 실제 쿠팡 Open API 필드 체계는 developers.coupang.com 문서 확인이
# 필요하며, 이 목록은 사용자 요청 섹션 4/5에 명시된 필드만 반영한
# 초안이다.
REQUIRED_PAYLOAD_FIELDS = (
    "sales_method",
    "external_vendor_sku",
    "seller_product_name",
    "brand",
    "display_category_code",
    "sale_price",
    "shipping_method",
    "outbound_shipping_place_code",
    "return_center_code",
    "options",
    # 상품 고시정보(전자상거래법 표시정보) — VERIFIED 상태의 완전한
    # 고시정보가 없으면 Dry Run은 실제 등록 준비도를 과대평가하게 되므로
    # 필수 필드로 취급한다(재감사 Medium-1 반영).
    "notices",
)


@dataclass(frozen=True)
class DryRunResult:

    outcome: str
    errors: list[str]
    payload_field_count: int
    attempted_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self):

        if self.outcome not in DryRunOutcome.ALL:
            raise ValueError(f"알 수 없는 outcome: {self.outcome}")


class CoupangGateway(Protocol):

    def submit_dry_run(self, payload: dict) -> DryRunResult: ...


class CoupangDryRunGateway:
    """
    실제 네트워크 호출 없이 Payload 구조와 필수 필드만 검증한다.

    성공(outcome=PASSED)은 "필수 필드가 전부 채워져 있고 값이 비어있지
    않다"는 것만 의미하며, 실제 쿠팡 서버의 승인을 의미하지 않는다.
    """

    def submit_dry_run(self, payload: dict) -> DryRunResult:

        errors: list[str] = []

        for required_field in REQUIRED_PAYLOAD_FIELDS:
            value = payload.get(required_field)

            if value in (None, "", [], {}):
                errors.append(f"필수 필드 누락: {required_field}")

        options = payload.get("options")
        if isinstance(options, list) and len(options) == 0:
            errors.append("구매옵션(options)이 최소 1개 이상 필요합니다.")

        outcome = DryRunOutcome.FAILED if errors else DryRunOutcome.PASSED

        return DryRunResult(
            outcome=outcome,
            errors=errors,
            payload_field_count=len(payload),
        )


@dataclass(frozen=True)
class CoupangOpenApiGatewayConfig:
    """
    향후 실제 쿠팡 Open API 연동 시 필요한 설정의 타입 계약만 미리
    반영한다. 이 클래스는 이번 단계에서 인스턴스화되어 실제 값을 담지
    않는다 — Secret은 여기 저장하지 않고, `.env`도 읽지 않는다.
    """

    wing_api_key_env_var: str | None = None
    allowed_ips: tuple[str, ...] = ()
    hmac_secret_env_var: str | None = None
    vendor_id: str | None = None
    timeout_seconds: float | None = None
    retry_count: int | None = None
    rate_limit_per_minute: int | None = None
    error_mapping: dict | None = None
    audit_log_enabled: bool = True


__all__ = [
    "REQUIRED_PAYLOAD_FIELDS",
    "DryRunResult",
    "CoupangGateway",
    "CoupangDryRunGateway",
    "CoupangOpenApiGatewayConfig",
]
