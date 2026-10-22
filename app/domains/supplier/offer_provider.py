"""
=========================================================
Homez OS

File : app/domains/supplier/offer_provider.py

2026-10-23 — 상품 서칭 AI S3(반영 D): 공급처 **읽기 전용 어댑터 경계**. 공급처마다 응답 모양이 달라도 `SupplierOffer`(공급 조건 공통 모양)로만 내보내게 해서, 평가 규칙(`offer_rules.py`)이
공급처 이름을 모르게 한다. 온채널에 기대지 않고 공급처를 늘릴 때 이 경계에 새 제공자를 추가한다.

  - 제공자는 **읽기만** 한다 — 주문·결제·계정·취소 메서드를 갖지 않는다(시험이 클래스 공개 메서드를 고정한다).
  - 지금 쓸 수 있는 제공자는 `FAKE`(시험·시연)와 `MANUAL`(사용자가 화면·문서에서 확인해 직접 입력한 값)뿐이다. **실제 공급처 제공자는 아직 없다.**
    우선순위 후보(S0 문서: 오너클랜 → 도매매)는 가입·API 권한·**실제 응답 모양**을 확인하기 전에는 만들지 않는다(🔴 응답을 한 번도 보지 못한 어댑터는 추정일 뿐이다).
    새 제공자를 추가하는 기준: ① 공식 문서로 상품·가격·배송비·최소 주문·위탁 가능 여부를 읽는 방법이 확인됨 ② 시험 환경 또는 가짜 응답 시험이 있음 ③ 호출은 승인 센터 허용 목적
    아래에서만 ④ 모르는 값은 None으로 둠(0으로 채우지 않음).
  - 외부 호출·DB·파일·AI 없음.
=========================================================
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from app.core.exceptions import BadRequestException
from app.domains.supplier.offer_rules import SupplierOffer

# 아직 만들지 않은 실제 제공자(안내용 — 코드에 연결돼 있지 않다).
PENDING_REAL_PROVIDERS = ("OWNERCLAN", "DOMEME")


class SupplierOfferProvider(ABC):
    """공급 조건 조회 어댑터. 읽기 전용."""

    code: str = "ABSTRACT"

    @abstractmethod
    def get_offer(self, external_id: str) -> SupplierOffer | None:
        """공급처의 상품 번호로 공급 조건을 읽는다. 없으면 None. 모르는 값은 None으로 둔 SupplierOffer를 낸다."""


class _FixedOfferProvider(SupplierOfferProvider):

    def __init__(self, offers: dict[str, SupplierOffer] | None = None):
        self._offers = {k: v for k, v in (offers or {}).items()}

    def get_offer(self, external_id: str) -> SupplierOffer | None:
        return self._offers.get(str(external_id))


class FakeSupplierOfferProvider(_FixedOfferProvider):
    """시험·시연용 — 정해 둔 값만 돌려준다."""

    code = "FAKE"


class ManualSupplierOfferProvider(_FixedOfferProvider):
    """사용자가 공급처 화면·문서에서 확인해 직접 입력한 값. 입력하지 않은 값은 None(모름)이다."""

    code = "MANUAL"


def get_offer_provider(code: str, offers: dict[str, SupplierOffer] | None = None) -> SupplierOfferProvider:
    providers = {"FAKE": FakeSupplierOfferProvider, "MANUAL": ManualSupplierOfferProvider}
    cls = providers.get(code)
    if cls is None:
        if code in PENDING_REAL_PROVIDERS:
            raise BadRequestException(f"{code} 공급처 연동은 아직 만들지 않았습니다 — 가입·API 권한·응답 형태를 확인한 뒤 추가합니다(직접 입력(MANUAL)은 쓸 수 있습니다).")
        raise BadRequestException(f"알 수 없는 공급처 제공자입니다: {code}")
    return cls(offers)


__all__ = ["SupplierOfferProvider", "FakeSupplierOfferProvider", "ManualSupplierOfferProvider", "get_offer_provider", "PENDING_REAL_PROVIDERS"]
