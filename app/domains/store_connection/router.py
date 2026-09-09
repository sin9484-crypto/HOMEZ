"""
=========================================================
Homez OS

File : app/domains/store_connection/router.py

판매채널 연결 — 운영자 API. 모든 엔드포인트는 admin_guard로 보호된다
(클라이언트 UI의 disabled 속성은 보안 검사로 간주하지 않는다 —
서버가 항상 다시 검사한다). 어떤 응답에도 Secret·Access Token·
Authorization 헤더·Credential Manager 저장 원문·Win32 오류 전체
문자열·traceback을 포함하지 않는다(StoreConnectionResponse/
StoreConnectionVerifyResponse가 그 경계를 강제한다).
=========================================================
"""

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.dependency import get_db
from app.core.guard import admin_guard
from app.core.windows_credential_store import CredentialStore
from app.core.windows_credential_store import WindowsCredentialStore
from app.domains.store_connection.schema import (
    StoreConnectionCreateRequest,
    StoreConnectionDeleteCredentialRequest,
    StoreConnectionDisableRequest,
    StoreConnectionGuideResponse,
    StoreConnectionGuideStepResponse,
    StoreConnectionResponse,
    StoreConnectionRotateCredentialRequest,
    StoreConnectionVerifyRequest,
    StoreConnectionVerifyResponse,
)
from app.domains.store_connection.service import StoreConnectionService
from app.domains.user.model import User

router = APIRouter(prefix="/store-connections", tags=["store-connections"])


def get_credential_store() -> CredentialStore:
    """
    운영 경로의 기본 Credential 저장소 — 항상 실제 Windows Credential
    Manager다. 테스트는 이 의존성을 오버라이드해 in-memory 구현을
    주입한다(운영 코드 자신은 절대 자동으로 in-memory를 선택하지
    않는다).
    """

    return WindowsCredentialStore()


def get_store_connection_service(
    db: Session = Depends(get_db),
    credential_store: CredentialStore = Depends(get_credential_store),
) -> StoreConnectionService:

    return StoreConnectionService(db, credential_store)


# --------------------------------------------------
# 안내 이미지 마법사 — 정적 데이터(공식 문서 확인일 2026-07-31)
# --------------------------------------------------

_COUPANG_GUIDE_STEPS = [
    {
        "step_number": 1, "title": "쿠팡 WING 판매자센터 접속",
        "description": (
            "브라우저에서 쿠팡 WING 판매자센터에 접속해 로그인합니다. "
            "이 화면은 HOMEZ가 아니라 쿠팡의 공식 사이트입니다."
        ),
        "image_path": "assets/guides/marketplace/coupang-step-01-open-wing.svg",
        "image_alt_text": "브라우저로 쿠팡 WING 판매자센터에 접속하는 절차 안내",
        "is_official_capture": False,
    },
    {
        "step_number": 2, "title": "판매자정보 또는 추가판매정보 메뉴 이동",
        "description": (
            "우측 상단의 아이디를 클릭한 뒤 '추가판매정보' 또는 "
            "'판매자정보' 메뉴로 이동합니다."
        ),
        "image_path": "assets/guides/marketplace/coupang-step-02-open-api-menu.svg",
        "image_alt_text": "쿠팡 WING에서 판매자정보 메뉴로 이동하는 절차 안내",
        "is_official_capture": False,
    },
    {
        "step_number": 3, "title": "OPEN API 키 발급 메뉴 확인",
        "description": (
            "'OPEN API 키 발급' 메뉴를 찾습니다. 사업자 인증이 완료되지 "
            "않은 계정은 이 메뉴가 보이지 않을 수 있습니다."
        ),
        "image_path": "assets/guides/marketplace/coupang-step-03-issue-api-key.svg",
        "image_alt_text": "쿠팡 WING의 OPEN API 키 발급 메뉴 위치 안내",
        "is_official_capture": False,
    },
    {
        "step_number": 4, "title": "이용약관 동의 및 API 키 발급",
        "description": (
            "이용약관에 동의하고 발급을 클릭하면 업체코드, Access Key, "
            "Secret Key가 화면에 표시됩니다. Secret Key는 이 화면에서만 "
            "확인할 수 있습니다."
        ),
        "image_path": "assets/guides/marketplace/coupang-step-04-copy-credentials.svg",
        "image_alt_text": "쿠팡 업체코드·Access Key·Secret Key 확인 화면 안내(예시 값은 전부 가짜)",
        "is_official_capture": False,
    },
    {
        "step_number": 5, "title": "HOMEZ에 연결 정보 입력",
        "description": (
            "HOMEZ의 쿠팡 연결 화면에 업체코드, Access Key, Secret Key를 "
            "입력합니다. 이 화면은 쿠팡 WING 로그인 비밀번호를 입력하는 "
            "곳이 아닙니다."
        ),
        "image_path": "assets/guides/marketplace/coupang-step-05-homez-connect.svg",
        "image_alt_text": "HOMEZ 쿠팡 연결 화면에 자격증명을 입력하는 절차 안내",
        "is_official_capture": False,
    },
]

_NAVER_GUIDE_STEPS = [
    {
        "step_number": 1, "title": "네이버 커머스API센터 접속",
        "description": "브라우저에서 네이버 커머스API센터에 접속해 로그인합니다.",
        "image_path": "assets/guides/marketplace/naver-step-01-open-api-center.svg",
        "image_alt_text": "브라우저로 네이버 커머스API센터에 접속하는 절차 안내",
        "is_official_capture": False,
    },
    {
        "step_number": 2, "title": "애플리케이션 등록",
        "description": "새 애플리케이션을 등록하고 필요한 API 권한을 설정합니다.",
        "image_path": "assets/guides/marketplace/naver-step-02-register-application.svg",
        "image_alt_text": "네이버 커머스API센터 애플리케이션 등록 절차 안내",
        "is_official_capture": False,
    },
    {
        "step_number": 3, "title": "필요한 API 권한 설정",
        "description": "상품·주문 등 HOMEZ 연동에 필요한 권한을 신청합니다.",
        "image_path": "assets/guides/marketplace/naver-step-03-permissions.svg",
        "image_alt_text": "네이버 커머스API 권한 설정 절차 안내",
        "is_official_capture": False,
    },
    {
        "step_number": 4, "title": "Client ID / Client Secret 확인",
        "description": (
            "등록된 애플리케이션에서 Client ID와 Client Secret을 "
            "확인합니다. Client Secret은 이 화면에서만 확인할 수 있습니다."
        ),
        "image_path": "assets/guides/marketplace/naver-step-04-copy-credentials.svg",
        "image_alt_text": "네이버 Client ID·Client Secret 확인 화면 안내(예시 값은 전부 가짜)",
        "is_official_capture": False,
    },
    {
        "step_number": 5, "title": "HOMEZ에 연결 정보 입력",
        "description": (
            "HOMEZ의 네이버 스마트스토어 연결 화면에 Client ID와 Client "
            "Secret을 입력합니다. 이 화면은 네이버 로그인 비밀번호를 "
            "입력하는 곳이 아닙니다."
        ),
        "image_path": "assets/guides/marketplace/naver-step-05-homez-connect.svg",
        "image_alt_text": "HOMEZ 네이버 연결 화면에 자격증명을 입력하는 절차 안내",
        "is_official_capture": False,
    },
]

_GUIDES = {
    "COUPANG": {
        "official_doc_url": "https://developers.coupang.com/",
        "steps": _COUPANG_GUIDE_STEPS,
    },
    "NAVER_SMARTSTORE": {
        "official_doc_url": "https://apicenter.commerce.naver.com/",
        "steps": _NAVER_GUIDE_STEPS,
    },
}


# --------------------------------------------------
# 조회
# --------------------------------------------------

@router.get("", response_model=list[StoreConnectionResponse])
def list_connections(
    current_user: User = Depends(admin_guard),
    service: StoreConnectionService = Depends(get_store_connection_service),
):

    company_id = current_user.company_id
    return service.list_for_company(company_id)


@router.get("/guides/{marketplace_code}", response_model=StoreConnectionGuideResponse)
def get_guide(
    marketplace_code: str,
    current_user: User = Depends(admin_guard),
):

    from app.core.exceptions import NotFoundException

    guide = _GUIDES.get(marketplace_code)
    if guide is None:
        raise NotFoundException("지원하지 않는 판매채널입니다.")

    return StoreConnectionGuideResponse(
        marketplace_code=marketplace_code,
        official_doc_url=guide["official_doc_url"],
        steps=[
            StoreConnectionGuideStepResponse(**step) for step in guide["steps"]
        ],
    )


@router.get("/{connection_id}", response_model=StoreConnectionResponse)
def get_connection(
    connection_id: int,
    current_user: User = Depends(admin_guard),
    service: StoreConnectionService = Depends(get_store_connection_service),
):

    return service.get(connection_id, current_user.company_id)


# --------------------------------------------------
# 연결 테스트(저장과 분리)
# --------------------------------------------------

@router.post("/verify", response_model=StoreConnectionVerifyResponse)
def verify_new_connection(
    data: StoreConnectionVerifyRequest,
    current_user: User = Depends(admin_guard),
    service: StoreConnectionService = Depends(get_store_connection_service),
):

    return service.verify_new(data, current_user.company_id)


@router.post("/{connection_id}/verify", response_model=StoreConnectionVerifyResponse)
def verify_existing_connection(
    connection_id: int,
    current_user: User = Depends(admin_guard),
    service: StoreConnectionService = Depends(get_store_connection_service),
):

    return service.verify_existing(
        connection_id, current_user.company_id, current_user.id,
    )


# --------------------------------------------------
# 생성(저장) — verification_token 필수
# --------------------------------------------------

@router.post("", response_model=StoreConnectionResponse)
def create_connection(
    data: StoreConnectionCreateRequest,
    current_user: User = Depends(admin_guard),
    service: StoreConnectionService = Depends(get_store_connection_service),
):

    company_id = current_user.company_id
    connection, _dup = service.create(
        data, company_id=company_id, created_by=current_user.id,
    )

    return connection


@router.post(
    "/{connection_id}/rotate-credential", response_model=StoreConnectionResponse,
)
def rotate_credential(
    connection_id: int,
    data: StoreConnectionRotateCredentialRequest,
    current_user: User = Depends(admin_guard),
    service: StoreConnectionService = Depends(get_store_connection_service),
):

    return service.rotate_credential(
        connection_id, current_user.company_id, data,
    )


@router.post("/{connection_id}/disable", response_model=StoreConnectionResponse)
def disable_connection(
    connection_id: int,
    data: StoreConnectionDisableRequest,
    current_user: User = Depends(admin_guard),
    service: StoreConnectionService = Depends(get_store_connection_service),
):

    return service.disable(connection_id, current_user.company_id, data)


@router.delete("/{connection_id}/credential", response_model=StoreConnectionResponse)
def delete_credential(
    connection_id: int,
    data: StoreConnectionDeleteCredentialRequest,
    current_user: User = Depends(admin_guard),
    service: StoreConnectionService = Depends(get_store_connection_service),
):

    return service.delete_credential(
        connection_id, current_user.company_id, data,
    )


__all__ = [
    "router",
]
