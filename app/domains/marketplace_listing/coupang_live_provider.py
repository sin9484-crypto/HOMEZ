"""Coupang product-creation HTTP provider.

The provider owns only the official HMAC/HTTP contract.  It never persists
credentials or retries an ambiguous request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import requests

from app.domains.store_connection.adapters.coupang_signing import (
    build_authorization_header,
)


BASE_URL = "https://api-gateway.coupang.com"
CREATE_PRODUCT_PATH = (
    "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products"
)
# 2026-08-28 Phase 3 감사 — 공식 문서에서 엔드포인트 존재와 statusName
# enum(SAVED/IN_REVIEW/APPROVING/APPROVED/PARTIAL_APPROVED/DENIED/
# DELETED)만 OFFICIAL_DOC_CONFIRMED로 확인했다. 정확한 응답 봉투 모양
# (data 중첩 여부 등)은 이 세션에서 실제 응답으로 재확인하지 못해
# UNCONFIRMED다 — create_product()와 같은 최상위
# {"code","message","data"} 계열로 우선 가정하되, Phase 10 실제 호출
# 전 반드시 실 응답으로 재검증한다.
STATUS_PATH_TEMPLATE = (
    "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{seller_product_id}"
)
STATUS_NAMES = frozenset({
    "SAVED", "IN_REVIEW", "APPROVING", "APPROVED",
    "PARTIAL_APPROVED", "DENIED", "DELETED",
})
# 2026-08-30 V7 후속 안정화 — 상태 파싱 결함 수정. 실제 Live 상태 조회
# (2026-08-30, 읽기 전용 GET 1회, 사용자 명시 승인 — 실제
# sellerProductId는 코드에 남기지 않는다, 감사 증거 문서에만 보존)에서
# 쿠팡이 statusName을 한글 "승인완료"로 반환하는 것을
# 실제로 확인했다(CONFIRMED_LIVE). STATUS_NAMES(영문 7종)에는 없어
# UNKNOWN/UNRECOGNIZED_STATUS로 오판정됐다 — 이 결함만 좁게 고친다.
# "승인완료" -> APPROVED만 추가하고, 나머지 6개 상태의 실제 한글
# 표기는 이번 세션에서 관측된 바 없으므로 추측해서 채우지 않는다
# (임의 추가 금지). 이 매핑에 없는 값은 전부 UNKNOWN으로 남는다
# (fail-closed) — 원본 문자열은 raw_status_name에 항상 별도로
# 보존하므로 매핑이 틀렸거나 모자라도 원문을 잃지 않는다.
CANONICAL_STATUS_MAP: dict[str, str] = {
    **{name: name for name in STATUS_NAMES},
    "승인완료": "APPROVED",  # CONFIRMED_LIVE 2026-08-30
}


@dataclass(frozen=True)
class LiveSubmissionResult:
    outcome: str
    external_reference: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    http_status: int | None = None
    correlation_id: str | None = None
    # 2026-08-28 진단 강화 — 쿠팡 응답이 문서화된 구조와 달라 결과가
    # UNKNOWN으로 나올 때, 원인을 다시 추측하지 않고 실제 원문을 볼 수
    # 있도록 남긴다. 쿠팡이 우리 access_key/secret_key를 응답 본문에
    # 되돌려줄 이유가 없으므로(우리가 보낸 요청 헤더에만 있음) 비밀
    # 노출 위험은 없다 — 다만 길이만 제한한다(로그 비대화 방지).
    raw_response_snippet: str | None = None
    # 2026-08-30 후속 지시(성공 경고 보존) — outcome=SUBMITTED(성공)
    # 이어도 쿠팡이 함께 돌려준 message가 있으면(예: Brand Enrollment
    # 안내) 버리지 않는다. 성공 여부(outcome)를 이 값으로 바꾸지
    # 않는다 — 순수하게 "확인이 필요한 부가 정보"만 담는다. 마스킹된
    # 값만 들어간다(원문 그대로 저장하지 않는다).
    warning_summary: str | None = None
    # 성공 시 쿠팡이 실제로 돌려준 최상위 code(예: "SUCCESS") — 감사
    # 로그·DB에 "무엇을 성공 판정 근거로 삼았는지" 구조적으로 남기기
    # 위함이다(성공 판정 자체를 code 존재 여부에서 분리하지 않는다).
    provider_response_code: str | None = None


@dataclass(frozen=True)
class ProductStatusResult:
    # 2026-08-29 Phase 4(V7-COUPANG-STATUS-001 신규) — HOMEZ에 등록
    # 이후 상태 조회 기능이 전혀 없던 것을 2026-08-28 감사에서 코드로
    # 확인했다. 이 메서드는 이번 Phase에서 실제 호출하지 않는다(실제
    # 호출은 Phase 10 사용자 승인 후에만).
    outcome: str  # "FOUND" | "NOT_FOUND" | "UNKNOWN"
    status_name: str | None = None  # canonical(예: "APPROVED") — 항상 영문
    # 2026-08-30 상태 파싱 결함 수정 — 쿠팡이 실제로 돌려준 원문 그대로
    # (영문이든 한글이든) 보존한다. canonical 매핑이 없거나(UNKNOWN)
    # 틀려도 원문 자체는 잃지 않기 위함이다(이 지시의 명시적 요구).
    raw_status_name: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    error_summary: str | None = None
    raw_response_snippet: str | None = None
    # 2026-08-31 V7 필수 작업 2번(제출 장부 정합화) — 정합화 서비스가
    # sellerProductId 하나만으로 다른 상품을 잘못 연결하지 않도록,
    # 이미 CONFIRMED 상태로 안전 추출기(coupang_live_response_
    # investigation.py::ALLOWED_DATA_FIELDS)에 등록돼 있던 필드들을
    # 구조화된 값으로도 노출한다. 새 필드를 추정해서 추가하지 않는다
    # — 전부 기존에 이미 CONFIRMED로 확인된 필드 이름 그대로다.
    seller_product_name: str | None = None
    vendor_user_id: str | None = None
    display_category_code: str | None = None


class CoupangProductProvider(Protocol):
    def create_product(self, payload: dict[str, Any]) -> LiveSubmissionResult: ...
    def get_product_status(self, seller_product_id: str) -> ProductStatusResult: ...


class CoupangLiveProductProvider:
    def __init__(
        self,
        credential: dict[str, str],
        *,
        timeout_seconds: float = 20.0,
        session: requests.Session | None = None,
        now_factory=None,
    ):
        self._access_key = credential["access_key"]
        self._secret_key = credential["secret_key"]
        self.vendor_id = credential["vendor_id"]
        self._timeout = timeout_seconds
        self._session = session or requests.Session()
        self._now = now_factory or (lambda: datetime.now(timezone.utc))

    def _headers(self, method: str, path: str) -> dict[str, str]:
        # 2026-08-29 Phase 4(V7-COUPANG-SIGN-001 수정) — 2026-08-28
        # 감사에서 이 Provider가 공식 문서 계약을 별도로 손으로 재구현해
        # coupang_signing.py와 중복됐던 것을 확인했다(query 이어붙이기
        # 누락 — 이 엔드포인트는 query가 없어 결과적으로 무해했지만
        # 유지보수 결함이었다). 공용 헬퍼로 교체해 서명 로직의 단일
        # 출처를 유지한다. query="" — 이 엔드포인트는 query string이
        # 없다.
        return {
            "Authorization": build_authorization_header(
                self._access_key, self._secret_key, method, path, "",
                now=self._now(),
            ),
            "Content-Type": "application/json;charset=UTF-8",
        }

    @staticmethod
    def _mask_text(text: Any, known_values: list[str]) -> str:
        # 2026-08-30 후속 지시 — "redact_free_text()만으로 주소를
        # 처리했다고 주장하지 않는다." message/errorItems/details
        # 처럼 쿠팡이 돌려준 자유 텍스트는 항상 이 메서드를 거친다.
        # 순서가 중요하다: (1) 이미 구조적으로 알고 있는 실제 값
        # (요청 payload 또는 같은 응답의 data에서 수집한 연락처·주소
        # 원문)을 정확히 일치하는 문자열만 먼저 치환하고 — 이건 패턴
        # 추측이 아니라 "우리가 이미 알고 있는 값"이라 오탐이 없다.
        # (2) 그 다음에만 전화번호/JWT 형태 패턴(redact_free_text)을
        # 2차 방어선으로 적용한다. known_values로 잡지 못하는(예:
        # 우리가 모르는 제3자 값) 전화번호 형태는 (2)가 잡는다.
        from app.core.sensitive_data import redact_free_text, redact_known_values

        if not text:
            return ""
        masked = redact_known_values(str(text), known_values)
        return redact_free_text(masked) or ""

    @staticmethod
    def _snippet(response: requests.Response) -> str:
        # 2026-08-30 V7 후속 안정화 Phase 4 — 길이 제한만으로 안전하다고
        # 간주하지 않는다(이 지시의 명시적 요구). 쿠팡이 우리
        # access_key/secret_key를 응답 본문에 되돌려줄 이유는 없지만,
        # 알려진 패턴(전화번호 형태, JWT 형태)은 이 저장소 공통
        # 마스킹 함수로 한 번 더 걸러 별도 방어선을 둔다.
        from app.core.sensitive_data import redact_free_text

        try:
            return redact_free_text(response.text[:500]) or ""
        except Exception:
            return ""

    @staticmethod
    def _status_snippet(response: requests.Response) -> str:
        # 2026-08-30 상태 응답 개인정보 마스킹 수정 — 이 지시의 명시적
        # 요구로, 정규식으로 "그럴듯한 패턴"을 찾아 지우는 denylist
        # 방식(위 _snippet())을 상태 조회 경로에서는 쓰지 않는다. 대신
        # JSON을 구조적으로 먼저 파싱하고, 진단에 실제로 필요한 필드
        # (code, message, data.sellerProductId, data.statusName)만
        # 새 객체로 옮겨 담아 다시 직렬화한다. 전화번호·주소·상세주소·
        # 우편번호·담당자·반품지 등은 물론, 아직 이름조차 모르는 미래의
        # 새 data 필드까지도 이름을 나열해서 지우는 게 아니라 애초에
        # 옮겨 담지 않으므로 구조적으로 빠진다(allowlist). message는
        # 자유 텍스트라 그 안에 전화번호가 섞여 나올 수 있으므로 기존
        # redact_free_text를 그대로 적용한다 — 단, sellerProductId
        # 등 허용 필드의 "값"까지 지워버리면 안 되므로(예: 실제
        # sellerProductId는 자릿수가 전화번호 정규식과 겹친다)
        # redact_free_text는 message 문자열 자체에만 적용하고, 최종
        # 직렬화된 JSON 전체에는 다시 적용하지 않는다(그러면 숫자형
        # sellerProductId 값이 깨지고 JSON 구조 자체가 손상된다 —
        # 이 파일 테스트 작성 중 실제로 재현·수정함). JSON 파싱 자체가
        # 실패하면(형식이 깨졌거나 예상 밖의 응답이면) 원문을 조금이라도
        # 내보내지 않고 빈 문자열로 fail-closed한다. 이 저장소에는
        # 이미 동일한 목적의 공용 허용목록 유틸리티(app/core/
        # sensitive_data.py::redact_dict)가 있어(2026-08-30, 이 세션의
        # 다른 작업으로 먼저 추가돼 있었음 — tests/test_sensitive_data_
        # masking.py로 이미 검증됨) 그 함수를 그대로 재사용한다(로직을
        # 새로 중복 구현하지 않는다).
        #
        # 2026-08-30 후속 지시 — message에 data의 연락처·주소 값이
        # 그대로 반복될 수 있으므로(쿠팡이 검증 오류 메시지에 입력값을
        # 그대로 echo하는 사례가 실제로 관측됨), data를 allowlist로
        # 자르기 전에 먼저 알려진 민감 필드 값을 전부 수집해 message
        # 마스킹에 쓴다 — 주소 정규식을 새로 추측하지 않는다.
        from app.core.sensitive_data import (
            KNOWN_CONTACT_ADDRESS_KEYS,
            collect_known_sensitive_values,
            redact_dict,
        )

        try:
            body = response.json()
        except Exception:
            return ""
        if not isinstance(body, dict):
            return ""

        data = body.get("data")
        known_values = (
            collect_known_sensitive_values(data, KNOWN_CONTACT_ADDRESS_KEYS)
            if isinstance(data, dict) else []
        )

        safe: dict[str, Any] = {}
        if "code" in body:
            safe["code"] = body.get("code")
        if "message" in body:
            safe["message"] = CoupangLiveProductProvider._mask_text(
                body.get("message"), known_values,
            )

        if isinstance(data, dict):
            safe_data = redact_dict(data, frozenset({"sellerProductId", "statusName"}))
            if safe_data:
                safe["data"] = safe_data

        try:
            import json as _json

            return _json.dumps(safe, ensure_ascii=False)[:500]
        except Exception:
            return ""

    @staticmethod
    def _safe_error(
        response: requests.Response, known_values: list[str] | None = None,
    ) -> tuple[str, str]:
        # 2026-08-30 후속 지시(개인정보 경로 전체 감사) — raw_message를
        # 마스킹 없이 그대로 summary(→ error_summary → 최종적으로
        # marketplace_submissions.error_reason DB 컬럼)에 담고 있던
        # 결함을 고친다. "메시지는 안전하다고 단정하지 않는다"는 요구
        # 그대로, 여기서도 반드시 _mask_text를 거친다.
        code = f"HTTP_{response.status_code}"
        summary = "쿠팡 상품등록 요청이 거부되었습니다."
        try:
            body = response.json()
            if isinstance(body, dict):
                raw_code = body.get("code")
                raw_message = body.get("message")
                if raw_code:
                    code = str(raw_code)[:80]
                if raw_message:
                    summary = CoupangLiveProductProvider._mask_text(
                        raw_message, known_values or [],
                    )[:500]
        except (ValueError, TypeError):
            pass
        return code, summary

    @staticmethod
    def _is_valid_seller_product_id(value: Any) -> bool:
        # 2026-08-30 후속 지시(엄격한 sellerProductId 검증 — 이전 버전의
        # 결함 수정) — 이전 버전은 문자열을 "" / "0"과만 비교해
        # "abc"/"12x"/"+1"/"-1"/"01.0" 같은 숫자 아닌 문자열을 그대로
        # 통과시켰다(실사 근거 없는 결함). 이제 명시적으로:
        # 유효 = 1 이상의 정수, 또는 숫자(0-9)로만 구성되고 정수값이
        # 1 이상인 문자열. 그 외(0/음수/bool/float/빈 문자열·공백/
        # 부호나 소수점·문자가 섞인 문자열/dict/list/None)는 전부
        # 차단한다. Python에서 bool은 int의 서브클래스라
        # (isinstance(True, int) == True) bool을 먼저 걸러야 한다.
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return value >= 1
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return False
            # "+1"/"-1"/"01.0"/"12x" 전부 여기서 걸러진다 — 부호,
            # 소수점, 문자가 하나라도 섞이면 isdigit()이 False다.
            # 유니코드 숫자 문자(예: 아랍-인디크 숫자)까지 통과시키지
            # 않도록 ASCII 여부도 함께 확인한다.
            if not text.isascii() or not text.isdigit():
                return False
            return int(text) >= 1
        return False

    @staticmethod
    def _create_snippet(
        response: requests.Response, known_values: list[str],
    ) -> str:
        # 2026-08-30 후속 지시 — create_product()도 get_product_status()
        # 와 동일하게 구조화된 allowlist로 바꾼다. 허용: code, 마스킹된
        # message, data가 scalar sellerProductId면 그 값, 중첩 data면
        # 그 안의 code/마스킹된 message/scalar data만. 제외: details,
        # errorItems, 이름 모르는 새 필드, 개인정보 필드, Credential
        # 형태 값 — 전부 옮겨 담지 않으므로 구조적으로 빠진다.
        try:
            body = response.json()
        except Exception:
            return ""
        if not isinstance(body, dict):
            return ""

        def _scalar_data(value: Any) -> Any | None:
            if isinstance(value, bool) or isinstance(value, (dict, list)):
                return None
            if isinstance(value, (str, int)) and CoupangLiveProductProvider._is_valid_seller_product_id(value):
                return value
            return None

        safe: dict[str, Any] = {}
        if "code" in body:
            safe["code"] = body.get("code")
        if "message" in body:
            safe["message"] = CoupangLiveProductProvider._mask_text(
                body.get("message"), known_values,
            )

        data = body.get("data")
        if isinstance(data, dict):
            safe_data: dict[str, Any] = {}
            if "code" in data:
                safe_data["code"] = data.get("code")
            if "message" in data:
                safe_data["message"] = CoupangLiveProductProvider._mask_text(
                    data.get("message"), known_values,
                )
            inner_scalar = _scalar_data(data.get("data"))
            if inner_scalar is not None:
                safe_data["data"] = inner_scalar
            if safe_data:
                safe["data"] = safe_data
        else:
            scalar = _scalar_data(data)
            if scalar is not None:
                safe["data"] = scalar

        try:
            import json as _json

            return _json.dumps(safe, ensure_ascii=False)[:500]
        except Exception:
            return ""

    def create_product(self, payload: dict[str, Any]) -> LiveSubmissionResult:
        # 2026-08-30 후속 지시(개인정보 경로 전체 감사) — 우리가 실제로
        # 보내는 요청 payload에 담긴 연락처·주소 실제 값을 먼저
        # 수집한다. 쿠팡이 검증 오류 message에 우리가 보낸 값을 그대로
        # echo하는 사례가 실제로 있었으므로, 응답을 마스킹할 때 이
        # 값들과 정확히 일치하는 부분을 치환한다(정규식 추측이 아니라
        # 우리가 이미 알고 있는 값).
        from app.core.sensitive_data import (
            KNOWN_CONTACT_ADDRESS_KEYS,
            collect_known_sensitive_values,
        )

        known_values = collect_known_sensitive_values(
            payload, KNOWN_CONTACT_ADDRESS_KEYS,
        )

        body = dict(payload)
        body["vendorId"] = self.vendor_id
        try:
            response = self._session.post(
                BASE_URL + CREATE_PRODUCT_PATH,
                headers=self._headers("POST", CREATE_PRODUCT_PATH),
                json=body,
                timeout=self._timeout,
            )
        except requests.Timeout:
            return LiveSubmissionResult(
                outcome="UNKNOWN", error_code="TIMEOUT",
                error_summary="응답 시간 초과로 등록 성공 여부를 확인할 수 없습니다.",
            )
        except requests.RequestException:
            return LiveSubmissionResult(
                outcome="UNKNOWN", error_code="NETWORK_ERROR",
                error_summary="네트워크 오류로 등록 성공 여부를 확인할 수 없습니다.",
            )

        if response.status_code >= 500:
            return LiveSubmissionResult(
                outcome="UNKNOWN", error_code=f"HTTP_{response.status_code}",
                error_summary="쿠팡 서버 오류로 등록 성공 여부를 확인할 수 없습니다.",
                http_status=response.status_code,
                raw_response_snippet=self._create_snippet(response, known_values),
            )
        if response.status_code >= 400:
            code, summary = self._safe_error(response, known_values)
            return LiveSubmissionResult(
                outcome="FAILED", error_code=code, error_summary=summary,
                http_status=response.status_code,
                raw_response_snippet=self._create_snippet(response, known_values),
            )

        try:
            body = response.json()
        except ValueError:
            return LiveSubmissionResult(
                outcome="UNKNOWN", error_code="INVALID_RESPONSE",
                error_summary="쿠팡 응답을 해석할 수 없어 결과 확인이 필요합니다.",
                http_status=response.status_code,
                raw_response_snippet=self._create_snippet(response, known_values),
            )
        # 2026-08-28 실사 확인 — 쿠팡의 실제 응답은 두 가지 서로 다른
        # 모양이다: (1) 검증 실패 시 HTTP 200이어도 바깥 레벨이 곧바로
        # {"code":"ERROR","message":"...","data":null}로 온다(중첩 없음).
        # (2) 성공 시(공식 문서) 바깥 {"code","message","data"}의 "data"가
        # 다시 {"code","message","data"} 객체이고, sellerProductId는 그
        # 안쪽 "data"에 순수 숫자로 온다("sellerProductId" 키가 아니다) —
        # response.data.data. 바깥 code가 성공을 나타내지 않으면(중첩을
        # 가정하지 않고) 그 사유(message)와 함께 즉시 FAILED로 구분한다.
        if not isinstance(body, dict):
            return LiveSubmissionResult(
                outcome="UNKNOWN", error_code="INVALID_RESPONSE",
                error_summary="쿠팡 응답 형식을 해석할 수 없어 결과 확인이 필요합니다.",
                http_status=response.status_code,
                raw_response_snippet=self._create_snippet(response, known_values),
            )
        outer_code = body.get("code")
        # 2026-08-30 후속 지시(엄격한 성공 판정) — 최상위 code가 없거나
        # (키 부재) null이면 더 이상 "성공으로 간주하고 통과"시키지
        # 않는다(과거 결함: None이 허용 튜플에 포함돼 있어 code 없는
        # 응답도 이후 data만 있으면 SUBMITTED로 판정될 수 있었다 —
        # 실사 근거 없는 방어적 코드였음, 이번에 실사례 확인 없이
        # 제거한다). 확인된 성공 code(SUCCESS/200/"200")가 없으면
        # UNKNOWN — 거부로 단정하지도, 성공으로 단정하지도 않는다.
        if outer_code is None:
            return LiveSubmissionResult(
                outcome="UNKNOWN", error_code="MISSING_SUCCESS_CODE",
                error_summary="응답에 최상위 code가 없어 성공 여부를 확인할 수 없습니다.",
                http_status=response.status_code,
                raw_response_snippet=self._create_snippet(response, known_values),
            )
        if outer_code not in ("SUCCESS", "200", 200):
            return LiveSubmissionResult(
                outcome="FAILED",
                error_code=str(outer_code),
                error_summary=self._mask_text(
                    body.get("message") or "쿠팡이 등록 요청을 거부했습니다.",
                    known_values,
                ),
                http_status=response.status_code,
                raw_response_snippet=self._create_snippet(response, known_values),
            )
        inner = body.get("data")
        outer_message = body.get("message")
        # 2026-08-29 Live 검증 실제 응답으로 확인(사용자 실제 계정 —
        # 실제 sellerProductId는 코드에 남기지 않는다, 감사 증거
        # 문서에만 보존) — 공식 봉투는 중첩되지 않는다:
        # 최상위가 곧바로 {"code":"SUCCESS","message":...,"data":
        # <sellerProductId 숫자>}이고, "data"가 다시 {"code","data"}
        # 객체인 경우는 실제로 관측된 적이 없다(이전 주석의 "중첩"
        # 가정은 미확인 추측이었음 — 이번에 실제로 반증됨). 그래도
        # 혹시 쿠팡이 문서화되지 않은 다른 계정/상황에서 중첩된 형태로
        # 응답할 가능성을 완전히 배제할 수 없으므로, data가 dict이면
        # 그 안의 code/data를 우선 확인하고, dict가 아니면(실제 확인된
        # 형태) data 자체를 sellerProductId로 취급한다.
        if isinstance(inner, dict):
            inner_code = inner.get("code")
            if inner_code != "SUCCESS":
                return LiveSubmissionResult(
                    outcome="FAILED",
                    error_code=str(inner_code) if inner_code else "COUPANG_REJECTED",
                    error_summary=self._mask_text(
                        inner.get("message") or "쿠팡이 등록 요청을 거부했습니다.",
                        known_values,
                    ),
                    http_status=response.status_code,
                    raw_response_snippet=self._create_snippet(response, known_values),
                )
            external_ref = inner.get("data")
            warning_source_message = inner.get("message")
        else:
            external_ref = inner
            warning_source_message = outer_message
        # 2026-08-30 후속 지시(엄격한 성공 판정) — 확인된 성공 code와
        # "유효한" scalar sellerProductId가 함께 있을 때만 SUBMITTED다.
        # bool/빈 문자열/0/dict/list는 인정하지 않는다.
        if not self._is_valid_seller_product_id(external_ref):
            return LiveSubmissionResult(
                outcome="UNKNOWN", error_code="MISSING_SELLER_PRODUCT_ID",
                error_summary="응답에 유효한 sellerProductId가 없어 결과 확인이 필요합니다.",
                http_status=response.status_code,
                raw_response_snippet=self._create_snippet(response, known_values),
            )
        # 2026-08-30 후속 지시(성공 경고 보존) — 성공이지만 함께 온
        # message(예: Brand Enrollment 안내)는 버리지 않는다. 마스킹
        # 후에만 담고, outcome은 그대로 SUBMITTED로 유지한다(경고가
        # 성공을 실패로 바꾸지 않는다).
        warning_summary = None
        if warning_source_message:
            masked_warning = self._mask_text(warning_source_message, known_values)
            if masked_warning:
                warning_summary = masked_warning
        return LiveSubmissionResult(
            outcome="SUBMITTED", external_reference=str(external_ref),
            http_status=response.status_code,
            warning_summary=warning_summary,
            provider_response_code=str(outer_code),
        )

    def get_product_status(self, seller_product_id: str) -> ProductStatusResult:
        """
        2026-08-29 Phase 4(V7-COUPANG-STATUS-001) — 코드만 추가한다.
        이 메서드는 이 세션 어떤 라우터에서도 호출되지 않는다(실제
        호출은 Phase 10 사용자 승인 후에만 예정).
        """
        path = STATUS_PATH_TEMPLATE.format(seller_product_id=seller_product_id)
        try:
            response = self._session.get(
                BASE_URL + path,
                headers=self._headers("GET", path),
                timeout=self._timeout,
            )
        except requests.Timeout:
            return ProductStatusResult(
                outcome="UNKNOWN", error_code="TIMEOUT",
                error_summary="응답 시간 초과로 상태를 확인할 수 없습니다.",
            )
        except requests.RequestException:
            return ProductStatusResult(
                outcome="UNKNOWN", error_code="NETWORK_ERROR",
                error_summary="네트워크 오류로 상태를 확인할 수 없습니다.",
            )

        if response.status_code == 404:
            return ProductStatusResult(outcome="NOT_FOUND", http_status=404)
        if response.status_code >= 500:
            return ProductStatusResult(
                outcome="UNKNOWN", error_code=f"HTTP_{response.status_code}",
                error_summary="쿠팡 서버 오류로 상태를 확인할 수 없습니다.",
                http_status=response.status_code,
                raw_response_snippet=self._status_snippet(response),
            )
        if response.status_code >= 400:
            code, summary = self._safe_error(response)
            return ProductStatusResult(
                outcome="UNKNOWN", error_code=code, error_summary=summary,
                http_status=response.status_code,
                raw_response_snippet=self._status_snippet(response),
            )

        try:
            body = response.json()
        except ValueError:
            return ProductStatusResult(
                outcome="UNKNOWN", error_code="INVALID_RESPONSE",
                error_summary="쿠팡 응답을 해석할 수 없어 상태 확인이 필요합니다.",
                http_status=response.status_code,
                raw_response_snippet=self._status_snippet(response),
            )
        if not isinstance(body, dict):
            return ProductStatusResult(
                outcome="UNKNOWN", error_code="INVALID_RESPONSE",
                error_summary="쿠팡 응답 형식을 해석할 수 없어 상태 확인이 필요합니다.",
                http_status=response.status_code,
                raw_response_snippet=self._status_snippet(response),
            )
        data = body.get("data")
        if not isinstance(data, dict):
            return ProductStatusResult(
                outcome="UNKNOWN", error_code="INVALID_RESPONSE",
                error_summary="쿠팡 응답에 상태 정보(data)가 없습니다.",
                http_status=response.status_code,
                raw_response_snippet=self._status_snippet(response),
            )
        status_name = data.get("statusName")
        raw_status_name = status_name if isinstance(status_name, str) else None
        canonical_status_name = CANONICAL_STATUS_MAP.get(status_name)
        # 2026-08-31 V7 필수 작업 2번 — 정합화의 다중 식별값 대조(상품명·
        # vendorUserId·카테고리)에 쓸 값을 함께 추출한다. 셋 다 이미
        # coupang_live_response_investigation.py의 ALLOWED_DATA_FIELDS에
        # CONFIRMED로 등록된 필드다(새 계약을 추정하지 않는다). 값이
        # 문자열이 아니면(예상 밖 타입) None으로 남긴다 — fail-closed로
        # 잘못된 타입을 비교에 쓰지 않는다.
        seller_product_name = self._safe_identifier_text(data.get("sellerProductName"))
        vendor_user_id = self._safe_identifier_text(data.get("vendorUserId"))
        raw_category_code = data.get("displayCategoryCode")
        display_category_code = (
            str(raw_category_code)
            if isinstance(raw_category_code, (str, int)) and not isinstance(raw_category_code, bool)
            else None
        )
        if canonical_status_name is None:
            return ProductStatusResult(
                outcome="UNKNOWN", error_code="UNRECOGNIZED_STATUS",
                error_summary=f"확인되지 않은 statusName입니다: {status_name!r}",
                http_status=response.status_code,
                raw_response_snippet=self._status_snippet(response),
                raw_status_name=raw_status_name,
            )
        return ProductStatusResult(
            outcome="FOUND", status_name=canonical_status_name,
            raw_status_name=raw_status_name,
            http_status=response.status_code,
            seller_product_name=seller_product_name,
            vendor_user_id=vendor_user_id,
            display_category_code=display_category_code,
        )

    @staticmethod
    def _safe_identifier_text(value: Any) -> str | None:
        # 2026-08-31 — 상품명 등은 자유 텍스트라 우연히 전화번호/JWT
        # 형태 패턴을 담고 있을 가능성을 배제할 수 없다(이 파일의 기존
        # _status_snippet() 2차 방어선과 동일한 원칙). 하나라도 걸리면
        # 그 값 자체를 비교에 쓰지 않는다(fail-closed) — 부분 마스킹을
        # 시도하지 않는다(마스킹된 값으로 "다르다"고 오판정하면 정상
        # 정합화를 막을 뿐이라 안전 쪽으로 실패한다).
        from app.core.sensitive_data import redact_free_text

        if not isinstance(value, str) or not value:
            return None
        if redact_free_text(value) != value:
            return None
        return value


__all__ = [
    "BASE_URL", "CREATE_PRODUCT_PATH", "STATUS_PATH_TEMPLATE", "STATUS_NAMES",
    "CANONICAL_STATUS_MAP",
    "LiveSubmissionResult", "ProductStatusResult",
    "CoupangProductProvider", "CoupangLiveProductProvider",
]
