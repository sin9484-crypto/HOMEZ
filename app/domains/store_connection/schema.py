"""
=========================================================
Homez OS

File : app/domains/store_connection/schema.py

판매채널 연결 — Pydantic 계약.

Secret 필드(access_key/secret_key/client_secret)는 요청 스키마에만
존재한다 — 어떤 응답 스키마에도 Secret 원문이 없다. 로그인
ID/비밀번호 필드는 어디에도 없다(공식 API 자격증명만 사용).
=========================================================
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from app.domains.store_connection.constants import MarketplaceCode


# --------------------------------------------------
# 채널별 Credential 필드 — extra="forbid"로 미지원 필드 거부
# (로그인 ID/비밀번호 필드가 구조적으로 존재하지 않는다)
# --------------------------------------------------

class _StrictCredentialFields(BaseModel):

    model_config = ConfigDict(extra="forbid")


class CoupangCredentialFields(_StrictCredentialFields):
    """
    쿠팡 WING Open API — 업체코드 + Access Key + Secret Key.
    로그인 ID/비밀번호가 아니다(WING 계정 로그인 비밀번호를 요구하지
    않는다).
    """

    vendor_id: str = Field(min_length=1, max_length=50, description="업체코드")
    access_key: str = Field(min_length=1, max_length=200)
    secret_key: str = Field(min_length=1, max_length=200)


class NaverCredentialFields(_StrictCredentialFields):
    """
    네이버 커머스API센터 — Client ID + Client Secret(+ 공식 규격이
    요구하는 경우에만 판매자 식별정보). 네이버 로그인 ID/비밀번호가
    아니다.
    """

    client_id: str = Field(min_length=1, max_length=200)
    client_secret: str = Field(min_length=1, max_length=200)
    account_id: str | None = Field(default=None, max_length=100)
    account_type: str | None = Field(default=None, max_length=50)


CREDENTIAL_FIELDS_REGISTRY: dict[str, type[_StrictCredentialFields]] = {
    MarketplaceCode.COUPANG: CoupangCredentialFields,
    MarketplaceCode.NAVER_SMARTSTORE: NaverCredentialFields,
}


def get_credential_fields_schema(
    marketplace_code: str,
) -> type[_StrictCredentialFields] | None:

    return CREDENTIAL_FIELDS_REGISTRY.get(marketplace_code)


# --------------------------------------------------
# 연결 테스트(저장과 분리) — 이 요청/응답에는 어디에도 저장되지 않는다
# --------------------------------------------------

class StoreConnectionVerifyRequest(BaseModel):
    """
    신규 연결을 저장하기 전, credential이 실제로 유효한지 fixture로
    확인한다. 이 호출 자체는 아무것도 저장하지 않는다(idempotency_key
    불필요 — 순수 조회성 검증).
    """

    marketplace_code: Literal["COUPANG", "NAVER_SMARTSTORE"]
    seller_identifier: str = Field(min_length=1, max_length=100)
    credential_fields: dict = Field(default_factory=dict)


class StoreConnectionVerifyResponse(BaseModel):
    """
    verification_token은 "이 정확한 credential 값으로 방금 검증에
    성공했다"는 서명된 짧은 만료(예: 10분) 증표다 — Secret 자체는
    포함하지 않는다. 입력값이 하나라도 바뀌면 fingerprint가 달라져
    저장 시점에 재검증에서 걸린다(app/domains/store_connection/
    verification_token.py 참고).
    """

    success: bool
    verification_token: str | None
    token_expires_at: datetime | None
    masked_credential_hint: str | None
    error_code: str | None
    error_summary: str | None
    expiration_status: str | None


# --------------------------------------------------
# 연결 생성(저장) — verification_token 필수
# --------------------------------------------------

class StoreConnectionCreateRequest(BaseModel):

    marketplace_code: Literal["COUPANG", "NAVER_SMARTSTORE"]
    display_name: str = Field(min_length=1, max_length=100)
    seller_identifier: str = Field(min_length=1, max_length=100)
    credential_fields: dict = Field(default_factory=dict)
    verification_token: str
    idempotency_key: str


class StoreConnectionRotateCredentialRequest(BaseModel):
    """자격증명 교체 — 기존 연결과 동일한 검증·저장 분리 흐름을 탄다."""

    credential_fields: dict = Field(default_factory=dict)
    verification_token: str
    idempotency_key: str


class StoreConnectionDisableRequest(BaseModel):

    idempotency_key: str


class StoreConnectionDeleteCredentialRequest(BaseModel):
    """
    Credential 삭제는 연결 비활성화와 다른, 더 위험한 작업이다 —
    명시적 확인(confirm=True)이 없으면 서버가 거부한다.
    """

    confirm: bool
    idempotency_key: str


# --------------------------------------------------
# 응답 — credential_reference·Secret·Access Token은 절대 포함 안 함
# --------------------------------------------------

class StoreConnectionResponse(BaseModel):

    id: int
    company_id: int
    marketplace_code: str
    display_name: str
    seller_identifier: str
    masked_credential_hint: str | None
    connection_status: str
    credential_version: int
    expires_at: datetime | None
    last_verified_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None
    last_error_summary: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StoreConnectionGuideStepResponse(BaseModel):

    step_number: int
    title: str
    description: str
    image_path: str
    image_alt_text: str
    is_official_capture: bool


class StoreConnectionGuideResponse(BaseModel):

    marketplace_code: str
    official_doc_url: str
    steps: list[StoreConnectionGuideStepResponse]


__all__ = [
    "CoupangCredentialFields",
    "NaverCredentialFields",
    "CREDENTIAL_FIELDS_REGISTRY",
    "get_credential_fields_schema",
    "StoreConnectionVerifyRequest",
    "StoreConnectionVerifyResponse",
    "StoreConnectionCreateRequest",
    "StoreConnectionRotateCredentialRequest",
    "StoreConnectionDisableRequest",
    "StoreConnectionDeleteCredentialRequest",
    "StoreConnectionResponse",
    "StoreConnectionGuideStepResponse",
    "StoreConnectionGuideResponse",
]
