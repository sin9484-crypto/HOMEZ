"""
=========================================================
Homez OS

File : app/domains/purchase_task/onchannel_client.py

Gate PT-3(2026-09-08 후속) — 온채널 실제 공식 OpenAPI(스펙 원본:
docs/HOMEZ_ONCHANNEL_OPENAPI_SPEC_20260908.json, 요약:
docs/HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md) 호출 클라이언트.

이 파일은 "seller"(판매사 = HOMEZ의 역할) 엔드포인트 중 상품 조회·
주문 조회(읽기 전용)와 발주(POST .../order/regist, 2026-09-08
재정정 — "확인된 계약 구현" 지시로 요청 생성·전송 코드까지 만든다)
를 구현한다.

**주의 — 이 클라이언트가 실제로 이 메서드를 호출하는 것과 실제
네트워크로 나가는 것은 이 파일의 책임이 아니다.** 이 파일은 스펙
그대로 요청을 만들고 응답을 해석할 뿐이다 — "지금 실제로 발주해도
되는지"는 항상 호출자(order_submission_service.py)의 idempotency
잠금·연결 상태 검증을 거쳐야 한다. 실제 발주·결제 실행 자체는
여전히 별도 승인 대상이다(이 파일의 코드 존재 자체가 승인이
아니다).

인증 실패(401)·권한 부족(403)·존재하지 않음(404)·호출 제한(429,
스펙에는 명시되지 않았지만 방어적으로 처리)·응답 형식 오류(JSON
파싱 실패·필수 필드 누락)·네트워크 오류(타임아웃·연결 실패)를 각각
다른 예외로 구분한다 — 전부 하나의 "실패"로 뭉개지 않는다.

이 클라이언트는 실제 네트워크를 부른다(requests). 테스트에서는
`http_get`을 주입해 실제 호출을 하지 않는다. JWT·개인정보 원문은
어떤 예외 메시지·로그에도 담지 않는다(마스킹 헬퍼 참고).
=========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Callable

ONCHANNEL_BASE_URL = "https://api.onch3.co.kr"
DEFAULT_TIMEOUT_SECONDS = 10


def mask_secret(value: str | None) -> str:
    """로그·오류 메시지에 JWT 등 비밀값을 남길 때 절대 원문을 쓰지
    않는다 — 길이와 앞 4글자만 남긴다(디버깅에 최소한으로 필요한
    정보, 값 자체는 복원 불가)."""

    if not value:
        return "(empty)"
    prefix = value[:4]
    return f"{prefix}...(len={len(value)})"


def mask_pii(value: str | None) -> str:
    """수령인명·전화번호·주소 등 개인정보를 로그에 남길 때 쓰는
    범용 마스킹 — 앞 1글자만 남기고 나머지는 길이만 표시한다."""

    if not value:
        return "(empty)"
    return f"{value[:1]}***(len={len(value)})"


class OnchannelApiError(Exception):
    """이 클라이언트에서 나는 모든 오류의 공통 부모. JWT·개인정보
    원문을 메시지에 담지 않는다."""


class OnchannelAuthenticationError(OnchannelApiError):
    """401 — 자격증명이 없거나 무효함(만료인지 원천 무효인지 온채널
    응답만으로는 구분되지 않는다 — 스펙의 error 스키마가 단일 코드/
    메시지만 제공)."""


class OnchannelPermissionError(OnchannelApiError):
    """403 — 인증은 됐으나 이 자원에 대한 권한이 없음(예: 판매신청
    하지 않은 상품에 대한 발주 시도로 추정, 미확인)."""


class OnchannelNotFoundError(OnchannelApiError):
    """404 — 존재하지 않는 상품코드/주문코드."""


class OnchannelRateLimitedError(OnchannelApiError):
    """429 — 호출 제한. 스펙 문서에 명시적으로 나열되어 있지는
    않지만, 실제 서비스가 보낼 가능성에 대비해 방어적으로 분류한다."""


class OnchannelValidationError(OnchannelApiError):
    """400/409 — 요청 자체가 잘못됐거나(필수 파라미터 누락 등) 상태
    충돌(예: 이미 판매신청된 상품 재신청)."""


class OnchannelResponseFormatError(OnchannelApiError):
    """온채널이 200을 반환했지만 JSON 파싱 실패, 또는 스펙에서
    필수로 정의한 필드가 응답에 없음 — 스펙과 실제 응답이 어긋난
    경우다(둘 다 실제로 겪어보기 전까지는 이론상 시나리오)."""


class OnchannelNetworkError(OnchannelApiError):
    """타임아웃·연결 실패 등 — 요청이 온채널 서버에 도달했는지조차
    알 수 없다. 재시도 여부는 이 클라이언트가 결정하지 않는다(호출자
    책임 — 이 세션의 "결과 불명 상태에서 자동 재시도하지 않는다"
    원칙과 동일)."""


@dataclass(frozen=True)
class OnchannelProductOption:

    option_id: int
    label: str
    price: int | None
    stock_qty: int | None


@dataclass(frozen=True)
class OnchannelShippingInfo:
    """2026-09-11 후속(반자동 완료 라운드, Phase 3 배송비 계약 재감사) —
    `GET seller/product/{code}` 응답의 `extends_info` 객체를 그대로
    옮긴 것. 이전 조사(2026-09-08~10)는 이 필드를 놓쳤다 —
    "shipping"/"delivery"/"fee" 같은 영문 키워드로만 스펙을 훑어서
    실제로는 `send_price` 등 영문 키 이름이 그 패턴에 안 걸렸기
    때문이다(한글 description에만 "배송비"가 있었다). 이 필드가
    존재한다는 사실 자체가 "배송비를 사전에 확정할 수 있다"는
    뜻은 아니다 — 아래 3가지 이유로 여전히 미확정으로 다룬다:
    1. 실제 발주 후 받는 `sum_delivery_price`(주문 상세 응답)와
       이 사전 조회값이 항상 일치한다는 것을 실제 주문으로
       검증한 적이 없다(가격 필드와 마찬가지로 드리프트 가능성).
    2. `send_type`이 "수량별 배송비"일 때 정확한 계산 규칙(구간
       단위)이 스펙에 명시돼 있지 않다.
    3. `jeju_send_price`/`etc_send_price`가 적용되는 정확한 우편번호
       범위를 HOMEZ가 갖고 있지 않다 — "제주/도서산간 여부"를
       주소만 보고 자동 판정하지 않는다(오판정 시 실제보다 적은
       금액으로 발주해 포인트 부족 위험).
    그래서 이 값은 Gate D를 자동으로 통과시키는 데 쓰지 않고,
    사용자가 배송비를 수동 확인할 때 참고할 "제안값"으로만 노출한다
    (사용자 확인 없이 자동 채택 금지)."""

    send_type: str | None
    quantity_threshold: int | None
    base_shipping_cost: int | None
    jeju_shipping_cost: int | None
    remote_area_shipping_cost: int | None


@dataclass(frozen=True)
class OnchannelProduct:

    product_code: str
    title: str
    status: str | None
    options: tuple[OnchannelProductOption, ...]
    shipping_info: OnchannelShippingInfo | None = None


@dataclass(frozen=True)
class OnchannelOrderDelivery:

    courier: str
    tracking_number: str
    created_at: str


@dataclass(frozen=True)
class OnchannelOrderOption:
    """POST /openapi/seller/order/regist의 options[] 항목 — 개인정보
    없음."""

    id: int
    qty: int


@dataclass(frozen=True)
class OnchannelOrderRegistrationRequest:
    """2026-09-08 후속(item 7 지시 4번, 이후 재정정 — "확인된 계약
    구현") — POST /openapi/seller/order/regist(발주 생성)의 요청
    바디를 그대로 옮긴 자료구조. `OnchannelApiClient.register_order()`
    가 실제로 이 값을 전송하는 코드까지 갖췄지만, **그 메서드를
    호출할지 여부(=실제 발주 실행)는 여전히 별도 승인 대상**이다 —
    이 파일에 코드가 있다는 사실 자체가 승인이 아니다(order_
    submission_service.py의 명시적 실행 게이트를 거쳐야만 호출된다).
    스펙이 요구하는 필드 이상을 담지 않는다(예: 고객 이메일·
    과거 주문 이력·회원 등급 등은 여기 없다 — 승인된 주문 이행
    목적에 필요하지 않다).

    `__repr__`을 오버라이드해 수취인 개인정보(recv_name/recv_tell/
    recv_mobile/address/address_detail)가 일반 로그·예외 메시지·
    디버그 출력에 실수로 그대로 찍히는 사고를 구조적으로 막는다 —
    dataclass 기본 repr은 모든 필드를 원문으로 보여주므로 그대로
    두면 이 클래스를 print()/logger.info()에 넘기는 순간 개인정보가
    샌다."""

    product_code: str
    recv_name: str
    recv_tell: str
    recv_mobile: str
    zipcode: str
    address: str
    options: tuple[OnchannelOrderOption, ...]
    address_detail: str = ""
    comment: str = ""
    sale_code: str = ""
    site_name: str = ""

    def __repr__(self) -> str:

        return (
            "OnchannelOrderRegistrationRequest("
            f"product_code={self.product_code!r}, "
            f"recv_name={mask_pii(self.recv_name)}, "
            f"recv_tell={mask_pii(self.recv_tell)}, "
            f"recv_mobile={mask_pii(self.recv_mobile)}, "
            f"zipcode={mask_pii(self.zipcode)}, "
            f"address={mask_pii(self.address)}, "
            f"address_detail={mask_pii(self.address_detail)}, "
            f"options={self.options!r}, "
            f"sale_code={self.sale_code!r})"
        )

    def to_request_body(self) -> dict:
        """실제 HTTP 요청 바디로 직렬화할 때만 원문을 그대로 쓴다 —
        이 메서드의 반환값을 로그에 남기지 않는다(호출자 책임)."""

        body: dict[str, Any] = {
            "product_code": self.product_code,
            "recv_name": self.recv_name,
            "recv_tell": self.recv_tell,
            "recv_mobile": self.recv_mobile,
            "zipcode": self.zipcode,
            "address": self.address,
            "options": [{"id": o.id, "qty": o.qty} for o in self.options],
        }
        if self.address_detail:
            body["address_detail"] = self.address_detail
        if self.comment:
            body["comment"] = self.comment
        if self.sale_code:
            body["sale_code"] = self.sale_code
        if self.site_name:
            body["site_name"] = self.site_name
        return body


@dataclass(frozen=True)
class OnchannelOrder:

    order_code: str
    product_code: str
    product_name: str
    order_price: int
    status: str | None
    detail_status: str | None
    deliverys: tuple[OnchannelOrderDelivery, ...]


def _classify_and_raise(status_code: int, body: Any) -> None:
    """스펙의 공통 error 스키마({status, meta, error:{code, message}})를
    기준으로 분류한다. message는 온채널이 만든 문구이므로 그대로
    전달해도 개인정보가 아니다(정적 안내문)."""

    message = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")

    detail = message or f"HTTP {status_code}"

    if status_code == 401:
        raise OnchannelAuthenticationError(f"인증 실패(401): {detail}")
    if status_code == 403:
        raise OnchannelPermissionError(f"권한 부족(403): {detail}")
    if status_code == 404:
        raise OnchannelNotFoundError(f"존재하지 않음(404): {detail}")
    if status_code == 429:
        raise OnchannelRateLimitedError(f"호출 제한(429): {detail}")
    if status_code in (400, 409):
        raise OnchannelValidationError(f"요청 오류({status_code}): {detail}")
    raise OnchannelApiError(f"알 수 없는 오류({status_code}): {detail}")


class OnchannelApiClient:

    def __init__(
        self, auth_key: str, *, base_url: str = ONCHANNEL_BASE_URL,
        http_get: Callable | None = None, http_post: Callable | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ):

        self._auth_key = auth_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        if http_get is not None:
            self._http_get = http_get
        else:
            import requests
            self._http_get = requests.get
        if http_post is not None:
            self._http_post = http_post
        else:
            import requests
            self._http_post = requests.post

    def _headers(self) -> dict[str, str]:

        return {"Authorization": f"Bearer {self._auth_key}"}

    def _parse_response(self, response) -> dict:
        """GET/POST 공통 — 상태코드·형식을 검증하고 result만 반환한다."""

        try:
            body = response.json()
        except ValueError as exc:
            raise OnchannelResponseFormatError(
                f"온채널 응답이 JSON이 아닙니다(HTTP {response.status_code}).",
            ) from exc

        if response.status_code != 200:
            _classify_and_raise(response.status_code, body)

        if not isinstance(body, dict) or "result" not in body:
            raise OnchannelResponseFormatError(
                "온채널 응답에 필수 필드(result)가 없습니다 — 스펙과 실제 "
                "응답이 어긋났을 수 있습니다.",
            )

        return body

    def _get(self, path: str, *, params: dict | None = None) -> dict:

        url = f"{self._base_url}{path}"
        try:
            response = self._http_get(
                url, headers=self._headers(), params=params,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — 네트워크 계열 전부 포괄
            raise OnchannelNetworkError(
                f"온채널 API 호출 실패(네트워크): {type(exc).__name__}",
            ) from exc

        return self._parse_response(response)

    def _post(self, path: str, *, json_body: dict) -> dict:
        """POST 전용 — 발주(order/regist) 같은 상태 변경 호출.
        `json_body`는 호출부 책임으로 이미 필수 필드가 채워져 있어야
        한다(이 메서드는 필드 존재를 다시 검증하지 않는다 — 그건
        `OnchannelOrderRegistrationRequest`/서비스 계층의 책임).
        네트워크 오류(타임아웃 포함)는 "결과 불명"이지 "실패"가
        아니다 — 이 메서드는 그 구분을 하지 않는다(예외 타입만
        던진다), 결과 판정은 항상 호출자 책임이다."""

        url = f"{self._base_url}{path}"
        try:
            response = self._http_post(
                url, headers=self._headers(), json=json_body,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — 네트워크 계열 전부 포괄(타임아웃 포함)
            raise OnchannelNetworkError(
                f"온채널 API 호출 실패(네트워크, 결과 불명): {type(exc).__name__}",
            ) from exc

        return self._parse_response(response)

    def get_product(self, product_code: str) -> OnchannelProduct:
        """GET /openapi/seller/product/{code} — 상품 상세."""

        body = self._get(f"/openapi/seller/product/{product_code}")
        result = body["result"]
        try:
            options = tuple(
                OnchannelProductOption(
                    option_id=opt["num"], label=opt.get("option_nm", ""),
                    price=opt.get("option_price"), stock_qty=opt.get("amount"),
                )
                for opt in (result.get("options") or [])
            )
            shipping_info = None
            extends = result.get("extends_info")
            if isinstance(extends, dict):
                # 필드 하나라도 없거나 정수가 아니면 그 항목만 None —
                # 나머지 항목까지 통째로 버리지 않는다(부분 관측도
                # 그대로 남긴다, 0으로 대체하지 않는다).
                def _int_or_none(value):
                    if isinstance(value, int) and not isinstance(value, bool):
                        return value
                    return None

                shipping_info = OnchannelShippingInfo(
                    send_type=(
                        extends.get("send_type")
                        if isinstance(extends.get("send_type"), str) else None
                    ),
                    quantity_threshold=_int_or_none(extends.get("quantity")),
                    base_shipping_cost=_int_or_none(extends.get("send_price")),
                    jeju_shipping_cost=_int_or_none(extends.get("jeju_send_price")),
                    remote_area_shipping_cost=_int_or_none(extends.get("etc_send_price")),
                )
            return OnchannelProduct(
                product_code=result["prd_code"], title=result.get("product_nm", ""),
                status=result.get("prd_state"), options=options,
                shipping_info=shipping_info,
            )
        except KeyError as exc:
            raise OnchannelResponseFormatError(
                f"온채널 상품 상세 응답에 필수 필드가 없습니다: {exc}",
            ) from exc

    def list_products(
        self, *, page: int = 1, page_size: int = 30, status: int | None = None,
    ) -> tuple[OnchannelProduct, ...]:
        """GET /openapi/seller/product — 상품 리스트."""

        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if status is not None:
            params["status"] = status
        body = self._get("/openapi/seller/product", params=params)
        result = body["result"]
        try:
            items = result.get("items") or []
            return tuple(
                OnchannelProduct(
                    product_code=item["product_code"], title=item.get("name", ""),
                    status=item.get("status"),
                    options=tuple(
                        OnchannelProductOption(
                            option_id=opt["id"], label=opt.get("name", ""),
                            price=None, stock_qty=opt.get("qty"),
                        )
                        for opt in (item.get("options") or [])
                    ),
                )
                for item in items
            )
        except KeyError as exc:
            raise OnchannelResponseFormatError(
                f"온채널 상품 리스트 응답에 필수 필드가 없습니다: {exc}",
            ) from exc

    def get_member_point(self) -> dict:
        """GET /openapi/common/member/point — 회원 포인트조회.

        2026-09-08 후속("발주·결제 계약 조사" 3번 재조사) — 이
        엔드포인트는 공식 스펙에 응답 필드가 전혀 정의돼 있지 않다
        (`result: array of object`뿐, 구체적 속성 없음). 그래서 다른
        메서드들과 달리 특정 필드명을 가정해 dataclass로 매핑하지
        않는다 — `result`를 그대로(dict 또는 list) 반환한다. 필드
        구조를 실제로 확인하는 것 자체가 이 호출의 목적이다. 결제·
        예치금과의 관계는 이 응답만으로 단정하지 않는다(호출자
        책임)."""

        body = self._get("/openapi/common/member/point")
        return body["result"]

    def get_order(self, order_code: str) -> OnchannelOrder:
        """GET /openapi/seller/order/{code} — 주문 상세."""

        body = self._get(f"/openapi/seller/order/{order_code}")
        result = body["result"]
        try:
            deliverys = tuple(
                OnchannelOrderDelivery(
                    courier=d.get("name", ""), tracking_number=d.get("tracking_number", ""),
                    created_at=d.get("created_at", ""),
                )
                for d in (result.get("deliverys") or [])
            )
            return OnchannelOrder(
                order_code=result["order_code"], product_code=result.get("product_code", ""),
                product_name=result.get("product_name", ""),
                order_price=result.get("order_price", 0),
                status=result.get("status"), detail_status=result.get("detail_status"),
                deliverys=deliverys,
            )
        except KeyError as exc:
            raise OnchannelResponseFormatError(
                f"온채널 주문 상세 응답에 필수 필드가 없습니다: {exc}",
            ) from exc

    def list_orders(
        self, *, start_at: str, end_at: str, page: int = 1, page_size: int = 30,
        status: int | None = None,
    ) -> tuple[OnchannelOrder, ...]:
        """GET /openapi/seller/order — 주문 리스트(최대 조회기간
        30일, 스펙 명시)."""

        params: dict[str, Any] = {
            "page": page, "page_size": page_size,
            "start_at": start_at, "end_at": end_at,
        }
        if status is not None:
            params["status"] = status
        body = self._get("/openapi/seller/order", params=params)
        result = body["result"]
        try:
            items = result.get("items") or []
            return tuple(
                OnchannelOrder(
                    order_code=item["order_code"], product_code=item.get("product_code", ""),
                    product_name=item.get("product_name", ""),
                    order_price=item.get("order_price", 0),
                    status=item.get("status"), detail_status=item.get("detail_status"),
                    deliverys=(),
                )
                for item in items
            )
        except KeyError as exc:
            raise OnchannelResponseFormatError(
                f"온채널 주문 리스트 응답에 필수 필드가 없습니다: {exc}",
            ) from exc

    def apply_for_sale(self, product_code: str) -> str:
        """POST /openapi/seller/product/apply — 판매신청. 2026-09-10
        온채널 공식 답변으로 "발주 전 판매신청 필수"가 확정됐다(docs/
        HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md 참고). 요청 바디는
        `{"prd_code": product_code}` 하나뿐(스펙 원본 확인, 2026-09-08
        다운로드본 기준), 응답도 `result.prd_code`만 돌려준다 — 판매
        신청이 실제로 승인됐는지 알려주는 필드는 없다(승인 상태 조회
        API 자체가 없다는 것도 공식 답변으로 확정됨). 그래서 이 메서드가
        성공(HTTP 200)했다는 사실은 "신청이 접수됐다"는 뜻이지 "승인
        됐다"는 뜻이 아니다 — 그 차이를 호출자가 반드시 유지해야 한다.

        409는 스펙상 "이미 신청된 상품 재신청" 정황으로 추정되지만
        (docs 재조사 절 참고) 공식 답변으로 확정된 사실은 아니다 —
        이 메서드는 그 추정을 하지 않는다. 다른 명시적 거부(400/401/
        403/404/409)와 똑같이 예외로 던질 뿐이며, "409니까 이미
        신청된 것으로 간주해도 된다"는 해석은 호출자도 하지 않는다
        (미확인을 확인으로 바꾸지 않는다는 이 세션 원칙)."""

        body = self._post(
            "/openapi/seller/product/apply", json_body={"prd_code": product_code},
        )
        result = body["result"]
        try:
            return result["prd_code"]
        except KeyError as exc:
            raise OnchannelResponseFormatError(
                f"온채널 판매신청 응답에 필수 필드가 없습니다: {exc}",
            ) from exc

    def register_order(
        self, request: OnchannelOrderRegistrationRequest,
    ) -> str:
        """POST /openapi/seller/order/regist — 실제 발주. 성공 시
        `order_code`만 반환한다(응답 전체를 그대로 넘기지 않는다 —
        호출부가 실수로 로그에 개인정보 없는 응답이라도 통째로
        찍는 습관을 만들지 않기 위함, 스펙 응답 자체엔 PII가 없지만
        원칙을 통일한다).

        이 메서드 자체는 멱등하지 않다 — 2026-09-10 온채널 공식
        답변으로 "동일 sale_code로 중복 발주해도 온채널 서버가
        제한하지 않는다"가 확정됐다(더 이상 "미확인"이 아니라 확인된
        사실이다, docs/HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md
        참고). 그래서 이 메서드를 몇 번 호출하느냐의 책임은 전부
        호출자(order_submission_service.py의 (company_id,
        idempotency_key) UNIQUE 제약 기반 DB 잠금)에 있다 — 이
        클라이언트 스스로는 재시도하지 않고, 몇 번 불렸는지도
        기억하지 않는다."""

        body = self._post(
            "/openapi/seller/order/regist", json_body=request.to_request_body(),
        )
        result = body["result"]
        try:
            return result["order_code"]
        except KeyError as exc:
            raise OnchannelResponseFormatError(
                f"온채널 발주 응답에 필수 필드가 없습니다: {exc}",
            ) from exc


__all__ = [
    "ONCHANNEL_BASE_URL",
    "mask_secret",
    "mask_pii",
    "OnchannelApiError",
    "OnchannelAuthenticationError",
    "OnchannelPermissionError",
    "OnchannelNotFoundError",
    "OnchannelRateLimitedError",
    "OnchannelValidationError",
    "OnchannelResponseFormatError",
    "OnchannelNetworkError",
    "OnchannelProductOption",
    "OnchannelShippingInfo",
    "OnchannelProduct",
    "OnchannelOrderOption",
    "OnchannelOrderRegistrationRequest",
    "OnchannelOrderDelivery",
    "OnchannelOrder",
    "OnchannelApiClient",
]
