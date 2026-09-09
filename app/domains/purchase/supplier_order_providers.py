"""
=========================================================
Homez OS

File : app/domains/purchase/supplier_order_providers.py

공급처 발주 전송(SupplierOrderProvider) 경계 — 2026-08-20 CTO
Section 4. 실제 공급처 API/EDI 연동은 아직 없다
(SUPPLIER_ORDER_PROVIDER_PENDING). FakeSupplierOrderProvider만 실제
동작하며 네트워크 호출이 없다 — Purchase 도메인 자체(app.domains.
purchase.service.PurchaseService)는 이 Provider를 아직 호출하지
않는다(발주 생성/확정/입고는 그대로 사용자가 명시적으로 수행 —
"실제 발주 직전에는 반드시 별도 승인" 원칙을 지키기 위해 자동 호출
지점을 만들지 않는다).
=========================================================
"""

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SupplierOrderRequest:

    purchase_id: int
    supplier_id: int
    idempotency_key: str
    items: tuple[tuple[str, int, float], ...]  # (supplier_sku, quantity, unit_cost)
    # 2026-08-21 5차 지시(작업 3) — FAKE Provider 전용, 격리 E2E에서만
    # 실제로 채워지는 결정적 시나리오 선택자. None(기본값)이면 기존
    # 동작(전량 접수)이 정확히 그대로 유지된다 — MANUAL/CSV Provider는
    # 이 필드를 아예 읽지 않으므로 아무 영향이 없다.
    test_scenario: str | None = None


@dataclass(frozen=True)
class SupplierOrderResult:

    status: str  # ACCEPTED / PARTIAL / REJECTED / FAILED
    supplier_order_id: str | None
    accepted_quantities: dict[str, int]
    rejected_quantities: dict[str, int]
    confirmed_price: float | None
    estimated_shipment_at: datetime | None
    tracking_number: str | None
    error_code: str | None
    retryable: bool
    retry_after_seconds: int | None


class SupplierOrderProviderError(Exception):
    """Provider 자체가 요청을 처리할 수 없을 때."""


class SupplierOrderProvider(ABC):

    code: str = "ABSTRACT"

    @abstractmethod
    def create_order(self, request: SupplierOrderRequest) -> SupplierOrderResult:
        ...

    @abstractmethod
    def get_order(self, supplier_order_id: str) -> SupplierOrderResult:
        ...

    @abstractmethod
    def cancel_order(self, supplier_order_id: str) -> SupplierOrderResult:
        ...

    @abstractmethod
    def get_status(self, supplier_order_id: str) -> str:
        ...

    @abstractmethod
    def get_tracking(self, supplier_order_id: str) -> str | None:
        ...


class FakeSupplierOrderScenario:
    """2026-08-21 5차 지시(작업 3) — FAKE Provider가 지원하는 결정적
    테스트 시나리오 이름. `FULL_ACCEPT`(기본값, `test_scenario=None`과
    동일)만 실제 프로덕션 기본 동작이고, 나머지 4개는 격리 Browser
    E2E가 UI를 실제로 재현·검증하기 위해서만 존재한다 — 어디에서도
    실제 공급처를 호출하지 않는다(순수 결정론적 로컬 시뮬레이션)."""

    FULL_ACCEPT = "FULL_ACCEPT"
    PARTIAL = "PARTIAL"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    RETRY_AFTER = "RETRY_AFTER"
    NON_RETRYABLE_FAILURE = "NON_RETRYABLE_FAILURE"

    ALL = (
        FULL_ACCEPT, PARTIAL, RETRYABLE_FAILURE, RETRY_AFTER,
        NON_RETRYABLE_FAILURE,
    )

    # 격리 E2E가 실제로 재시도-대기 흐름을 짧은 시간 안에 재현할 수
    # 있도록 짧게 고정한다(실제 공급처 rate limit과 무관 — 순수 데모값).
    RETRY_AFTER_SECONDS = 5


class FakeSupplierOrderProvider(SupplierOrderProvider):
    """네트워크 호출 없이 결정론적으로 시뮬레이션한다 —
    idempotency_key로 supplier_order_id를 만들어 같은 요청은 항상
    같은 주문번호를 낸다. `request.test_scenario`가 없으면(기본값)
    기존 동작(전량 접수)이 정확히 그대로다."""

    code = "FAKE"

    _orders: dict[str, SupplierOrderResult]

    def __init__(self):

        self._orders = {}

    def create_order(self, request: SupplierOrderRequest) -> SupplierOrderResult:

        supplier_order_id = f"FAKE-SO-{request.idempotency_key}"

        if supplier_order_id in self._orders:
            return self._orders[supplier_order_id]

        scenario = request.test_scenario or FakeSupplierOrderScenario.FULL_ACCEPT
        result = self._build_result(supplier_order_id, request, scenario)
        self._orders[supplier_order_id] = result

        return result

    def _build_result(
        self, supplier_order_id: str, request: SupplierOrderRequest,
        scenario: str,
    ) -> SupplierOrderResult:

        total_price = sum(qty * cost for _sku, qty, cost in request.items)

        if scenario == FakeSupplierOrderScenario.RETRYABLE_FAILURE:
            return SupplierOrderResult(
                status="FAILED",
                supplier_order_id=None,
                accepted_quantities={},
                rejected_quantities={},
                confirmed_price=None,
                estimated_shipment_at=None,
                tracking_number=None,
                error_code="FAKE_RETRYABLE_TEST_FAILURE",
                retryable=True,
                retry_after_seconds=None,
            )

        if scenario == FakeSupplierOrderScenario.RETRY_AFTER:
            return SupplierOrderResult(
                status="FAILED",
                supplier_order_id=None,
                accepted_quantities={},
                rejected_quantities={},
                confirmed_price=None,
                estimated_shipment_at=None,
                tracking_number=None,
                error_code="FAKE_RATE_LIMITED_TEST_FAILURE",
                retryable=True,
                retry_after_seconds=FakeSupplierOrderScenario.RETRY_AFTER_SECONDS,
            )

        if scenario == FakeSupplierOrderScenario.NON_RETRYABLE_FAILURE:
            return SupplierOrderResult(
                status="FAILED",
                supplier_order_id=None,
                accepted_quantities={},
                rejected_quantities={},
                confirmed_price=None,
                estimated_shipment_at=None,
                tracking_number=None,
                error_code="FAKE_NON_RETRYABLE_TEST_FAILURE",
                retryable=False,
                retry_after_seconds=None,
            )

        if scenario == FakeSupplierOrderScenario.PARTIAL:
            accepted: dict[str, int] = {}
            rejected: dict[str, int] = {}
            for sku, qty, _cost in request.items:
                # 결정적 절반 분할 — 수량 1이면 전량 거절(가장 눈에
                # 띄는 부분거절 재현), 2 이상이면 절반 올림 접수/
                # 나머지 거절.
                accepted_qty = qty // 2
                rejected_qty = qty - accepted_qty
                if accepted_qty > 0:
                    accepted[sku] = accepted_qty
                if rejected_qty > 0:
                    rejected[sku] = rejected_qty
            accepted_price = sum(
                accepted.get(sku, 0) * cost for sku, _qty, cost in request.items
            )
            return SupplierOrderResult(
                status="PARTIAL",
                supplier_order_id=supplier_order_id,
                accepted_quantities=accepted,
                rejected_quantities=rejected,
                confirmed_price=accepted_price,
                estimated_shipment_at=None,
                tracking_number=None,
                error_code=None,
                retryable=False,
                retry_after_seconds=None,
            )

        # FULL_ACCEPT(기본값) — 기존 동작과 완전히 동일.
        accepted_all = {sku: qty for sku, qty, _cost in request.items}
        return SupplierOrderResult(
            status="ACCEPTED",
            supplier_order_id=supplier_order_id,
            accepted_quantities=accepted_all,
            rejected_quantities={},
            confirmed_price=total_price,
            estimated_shipment_at=None,
            tracking_number=None,
            error_code=None,
            retryable=False,
            retry_after_seconds=None,
        )

    def get_order(self, supplier_order_id: str) -> SupplierOrderResult:

        result = self._orders.get(supplier_order_id)
        if result is None:
            raise SupplierOrderProviderError(
                f"알 수 없는 supplier_order_id입니다: {supplier_order_id}",
            )

        return result

    def cancel_order(self, supplier_order_id: str) -> SupplierOrderResult:

        result = self.get_order(supplier_order_id)
        cancelled = SupplierOrderResult(
            status="REJECTED",
            supplier_order_id=result.supplier_order_id,
            accepted_quantities={},
            rejected_quantities=result.accepted_quantities,
            confirmed_price=None,
            estimated_shipment_at=None,
            tracking_number=None,
            error_code="CANCELLED_BY_USER",
            retryable=False,
            retry_after_seconds=None,
        )
        self._orders[supplier_order_id] = cancelled

        return cancelled

    def get_status(self, supplier_order_id: str) -> str:

        return self.get_order(supplier_order_id).status

    def get_tracking(self, supplier_order_id: str) -> str | None:

        return self.get_order(supplier_order_id).tracking_number


class ManualSupplierOrderProvider(SupplierOrderProvider):
    """자동 전송을 하지 않는다 — 사용자가 공급처에 직접 연락해
    발주하고, 결과를 수기로 기록해야 함을 나타낸다."""

    code = "MANUAL"

    def create_order(self, request: SupplierOrderRequest) -> SupplierOrderResult:

        raise SupplierOrderProviderError(
            "수동 발주 Provider입니다 — 공급처에 직접 연락해 발주한 뒤 "
            "결과를 기록하세요(자동 전송 없음).",
        )

    def get_order(self, supplier_order_id: str) -> SupplierOrderResult:
        raise SupplierOrderProviderError("수동 발주 Provider는 조회를 지원하지 않습니다.")

    def cancel_order(self, supplier_order_id: str) -> SupplierOrderResult:
        raise SupplierOrderProviderError("수동 발주 Provider는 취소를 지원하지 않습니다.")

    def get_status(self, supplier_order_id: str) -> str:
        raise SupplierOrderProviderError("수동 발주 Provider는 상태 조회를 지원하지 않습니다.")

    def get_tracking(self, supplier_order_id: str) -> str | None:
        return None


class CsvSupplierOrderProvider(SupplierOrderProvider):
    """실제 전송 대신 발주 요청을 CSV 발주서 형태의 행(dict)으로
    누적한다 — 사람이 내려받아 공급처에 전달하는 흐름을 시뮬레이션."""

    code = "CSV"

    def __init__(self):

        self.exported_rows: list[dict] = []

    def create_order(self, request: SupplierOrderRequest) -> SupplierOrderResult:

        supplier_order_id = f"CSV-SO-{request.idempotency_key}"

        for sku, qty, cost in request.items:
            self.exported_rows.append({
                "supplier_order_id": supplier_order_id,
                "supplier_id": request.supplier_id,
                "supplier_sku": sku,
                "quantity": qty,
                "unit_cost": cost,
            })

        return SupplierOrderResult(
            status="ACCEPTED",
            supplier_order_id=supplier_order_id,
            accepted_quantities={sku: qty for sku, qty, _cost in request.items},
            rejected_quantities={},
            confirmed_price=sum(qty * cost for _sku, qty, cost in request.items),
            estimated_shipment_at=None,
            tracking_number=None,
            error_code=None,
            retryable=False,
            retry_after_seconds=None,
        )

    def get_order(self, supplier_order_id: str) -> SupplierOrderResult:
        raise SupplierOrderProviderError("CSV 발주 Provider는 조회를 지원하지 않습니다.")

    def cancel_order(self, supplier_order_id: str) -> SupplierOrderResult:
        raise SupplierOrderProviderError("CSV 발주 Provider는 취소를 지원하지 않습니다.")

    def get_status(self, supplier_order_id: str) -> str:
        return "EXPORTED"

    def get_tracking(self, supplier_order_id: str) -> str | None:
        return None


class OnchannelSupplierOrderProvider(SupplierOrderProvider):
    """
    2026-09-08 V7 통합 매입 순서 6번("공식 API 매입 연결") — 온채널
    (국내 B2B 도매·위탁배송 공급처, `docs/HOMEZ_V7_PROCUREMENT_
    CHANNEL_SURVEY_20260907.md` §1에서 이미 조사한 후보). 신청서
    원본은 `C:\\Users\\Daum pc\\Desktop\\온채널\\온채널_OpenAPI_
    사용_계획서 최종.pdf`(판매사 권한, 일반 연동/직접 개발 방식,
    대시보드 자동화 아님). 처음 확인된 인증키(target:
    "homez_onchannel_api")를 Windows Credential Manager로 옮겼으나,
    **그 값은 온채널의 "사용 승인" 처리 전(발급대기) 상태에서 캡처된
    무효한 값이었음을 사용자가 확인해 즉시 삭제했다**(2026-09-08 —
    `docs/HOMEZ_PROJECT_STATE.md` "정정: 온채널 인증키는 무효" 절
    참고). 등록 엔드포인트(`POST /purchases/system/onchannel-
    credential`)와 이 Provider 자체는 그대로 유효하다 — 실제 승인된
    키가 발급되면 같은 경로로 다시 등록하면 된다. 키 원문은 이
    코드베이스 어디에도 평문으로 없다.

    **이 Provider는 아직 어떤 실제 HTTP 호출도 하지 않는다** — 온채널
    쪽 실제 API 기술 문서(엔드포인트·요청/응답 형식·에러 코드)를
    이 세션이 구할 수 없었다(공개 검색으로 찾은 페이지는 UI 사용법
    안내일 뿐 기술 스펙이 없음 — `mypage/oc_api_setting.php`의
    "온채널 API 문서 바로가기" 버튼은 로그인 필요 영역이라 이
    세션이 열람할 수 없다). 추측으로 엔드포인트를 만들어 실제 인증키로
    호출을 시도하지 않는다(실 서비스에 잘못된 요청을 보낼 위험 —
    이 저장소의 fail-closed 원칙과 동일). 실제 기술 문서를 확보하면
    이 클래스의 각 메서드에 실제 요청/응답 매핑만 채우면 된다 —
    자격증명 배선과 Provider 등록은 이미 완료돼 있다.
    """

    code = "ONCHANNEL"

    CREDENTIAL_REFERENCE = "homez_onchannel_api"

    def __init__(self, credential_store=None):

        if credential_store is None:
            from app.core.windows_credential_store import (
                WindowsCredentialStore,
            )

            credential_store = WindowsCredentialStore()

        self.credential_store = credential_store

    def _not_implemented(self, method: str):

        from app.core.windows_credential_store import (
            CredentialNotFoundError,
            CredentialStoreError,
        )

        try:
            self.credential_store.read(self.CREDENTIAL_REFERENCE)
            credential_status = "자격증명 저장됨"
        except (CredentialNotFoundError, CredentialStoreError):
            credential_status = "자격증명 없음"

        raise SupplierOrderProviderError(
            f"온채널 Provider의 {method}()는 아직 구현되지 않았습니다 "
            f"— 실제 API 기술 문서(엔드포인트·요청/응답 형식)가 "
            f"확보되지 않아 추측으로 호출을 만들지 않습니다({credential_status}, "
            "ONCHANNEL_API_SPEC_REQUIRED).",
        )

    def create_order(self, request: SupplierOrderRequest) -> SupplierOrderResult:
        self._not_implemented("create_order")

    def get_order(self, supplier_order_id: str) -> SupplierOrderResult:
        self._not_implemented("get_order")

    def cancel_order(self, supplier_order_id: str) -> SupplierOrderResult:
        self._not_implemented("cancel_order")

    def get_status(self, supplier_order_id: str) -> str:
        self._not_implemented("get_status")

    def get_tracking(self, supplier_order_id: str) -> str | None:
        self._not_implemented("get_tracking")


def get_supplier_order_provider(code: str) -> SupplierOrderProvider:

    if code == "FAKE":
        return FakeSupplierOrderProvider()
    if code == "MANUAL":
        return ManualSupplierOrderProvider()
    if code == "CSV":
        return CsvSupplierOrderProvider()
    if code == "ONCHANNEL":
        return OnchannelSupplierOrderProvider()

    raise SupplierOrderProviderError(
        f"알 수 없거나 아직 연결되지 않은 Provider입니다: {code} "
        "(SUPPLIER_ORDER_PROVIDER_PENDING)",
    )


__all__ = [
    "SupplierOrderRequest",
    "SupplierOrderResult",
    "SupplierOrderProviderError",
    "SupplierOrderProvider",
    "FakeSupplierOrderProvider",
    "ManualSupplierOrderProvider",
    "CsvSupplierOrderProvider",
    "OnchannelSupplierOrderProvider",
    "get_supplier_order_provider",
]
