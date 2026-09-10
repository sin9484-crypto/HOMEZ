"""
=========================================================
Homez OS

File : app/domains/purchase_task/order_submission_service.py

Gate PT-3(2026-09-08 후속, "확인된 발주 계약 구현") — 온채널 실제
발주(POST seller/order/regist) 실행. **이 파일에 코드가 있다는
사실 자체는 승인이 아니다** — `submit_order()`는 `confirm_real_
submission=True`를 명시적으로 넘기지 않으면 항상 거부한다(기본값
False가 fail-closed). 실제 발주·결제 실행은 여전히 별도 승인
대상이며, 이 파일은 현재 어떤 라우터·UI에도 연결돼 있지 않다 —
호출은 오직 개발 담당자가 직접 스크립트로 구성해 실행한다.

핵심 원칙(사용자 지시 원문):
1. 확인된 스펙 필드·타입·필수값만 사용한다(onchannel_client.py의
   `OnchannelOrderRegistrationRequest`가 이미 그렇게 고정돼 있다) —
   문서에 없는 필드를 추측해서 추가하지 않는다.
2. "매입 작업 배정 가능 판정"(select_connection_for_task)과 "발주
   실행 가능 판정"은 분리된 별도 게이트다
   (verify_connection_ready_for_order_submission).
3. 내부 중복 실행 잠금은 (company_id, idempotency_key) UNIQUE
   제약(PurchaseOrderSubmissionAttempt)으로 강제한다. 2026-09-10
   온채널 공식 답변으로 "동일 sale_code 중복 발주를 온채널 서버가
   제한하지 않는다"가 확정됐다(더 이상 미확인이 아니다, docs/
   HOMEZ_ONCHANNEL_OPENAPI_FINDINGS_20260908.md 참고) — 즉 이 UNIQUE
   제약이 유일한 중복 방지 수단임이 확정됐다. 이 잠금은 "HOMEZ가
   같은 idempotency_key로 두 번 호출하지 않는다"만 보장한다.
4. 결과 불명(타임아웃·네트워크 오류·응답 형식 오류)은 실패로
   단정하지 않는다 — RESULT_UNKNOWN으로 남기고, 같은 idempotency_key
   로는 다시 시도할 수 없다(자동 재시도·재클릭에 의한 중복 전송
   차단). 새로 시도하려면 사람이 결과를 직접 확인한 뒤 새
   idempotency_key로 호출해야 한다.
5. 개인정보(수취인명·연락처·주소)는 이 서비스의 로그·이벤트·DB
   컬럼 어디에도 원문으로 남기지 않는다 — DB에는 product_code·
   options만 저장한다.
6. (2026-09-10 후속, Phase 9 — 자동화 모드 배선) 비상정지(Emergency
   Stop)가 켜져 있거나, 이 회사의 PURCHASE_ORDER 기능 모드가
   PAUSED/ERROR면 confirm_real_submission=True를 넘겨도 무조건
   막는다 — "일시중지"·"오류"는 그 자체로 "새 실행을 시작하지
   않는다"는 뜻이다(docs/HOMEZ_USER_OPERATION_SETTINGS.md,
   FunctionMode.DESCRIPTIONS_KO 참고). MANUAL/SEMI_AUTOMATIC/
   AUTOMATIC 세 모드의 차이(누가·언제 이 메서드를 호출하는가)는
   아직 이 메서드를 자동으로 호출하는 오케스트레이션 코드 자체가
   없어(UI-5의 "실제 전송" 버튼도 여전히 비활성) 이 메서드 안에서
   추가로 분기하지 않는다 — 세 모드 모두 여전히 confirm_real_
   submission=True라는 동일한 명시적 승인을 요구한다.
=========================================================
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.domains.automation_safety.constants import FunctionCode
from app.domains.automation_safety.constants import FunctionMode
from app.domains.automation_safety.service import SafetyService
from app.domains.purchase_task.channel_adapter import get_purchase_channel_adapter
from app.domains.purchase_task.channel_connection_service import (
    PurchaseChannelConnectionService,
)
from app.domains.purchase_task.constants import OrderSubmissionStatus
from app.domains.purchase_task.constants import SalesApplicationStatus
from app.domains.purchase_task.model import PurchaseOrderSubmissionAttempt
from app.domains.purchase_task.sales_application_service import (
    PurchaseSalesApplicationService,
)


class PurchaseOrderSubmissionService:

    def __init__(self, db: Session, credential_store=None):

        self.db = db
        if credential_store is None:
            from app.core.windows_credential_store import WindowsCredentialStore
            credential_store = WindowsCredentialStore()
        self._credential_store = credential_store
        self._connection_service = PurchaseChannelConnectionService(
            db, credential_store=credential_store,
        )
        self._sales_application_service = PurchaseSalesApplicationService(
            db, credential_store=credential_store,
        )
        self._safety_service = SafetyService(db)

    # ---------------- 실행 이력 조회(부작용 없음) ----------------

    def get_attempt(
        self, connection_id: int, company_id: int, idempotency_key: str,
    ) -> PurchaseOrderSubmissionAttempt | None:

        return (
            self.db.query(PurchaseOrderSubmissionAttempt)
            .filter(
                PurchaseOrderSubmissionAttempt.company_id == company_id,
                PurchaseOrderSubmissionAttempt.connection_id == connection_id,
                PurchaseOrderSubmissionAttempt.idempotency_key == idempotency_key,
            )
            .first()
        )

    # ---------------- 실제 발주 실행 ----------------

    def submit_order(
        self, connection_id: int, company_id: int, *,
        idempotency_key: str, product_code: str,
        options: list[dict], recv_name: str, recv_tell: str,
        recv_mobile: str, zipcode: str, address: str,
        address_detail: str = "", comment: str = "", site_name: str = "",
        purchase_task_id: int | None = None, triggered_by: int | None = None,
        confirm_real_submission: bool = False,
    ) -> PurchaseOrderSubmissionAttempt:
        """실제 온채널 발주를 시도한다. `confirm_real_submission=True`를
        명시적으로 넘기지 않으면 아무 것도 하지 않고 거부한다(연결
        조회조차 하지 않는다 — 이 승인 게이트가 이 메서드의 첫 줄이다,
        다른 어떤 검증보다 먼저 막는다)."""

        if not confirm_real_submission:
            raise BadRequestException(
                "실제 발주 실행은 명시적 승인이 필요합니다 "
                "(confirm_real_submission=True) — 코드가 존재한다는 사실 "
                "자체는 승인이 아닙니다.",
            )

        # 2026-09-10 후속(Phase 9) — 비상정지·PAUSED·ERROR는 confirm_
        # real_submission=True를 넘겨도 뚫리지 않는다. 이 두 검사는
        # 연결 조회보다도 먼저 막는다(자격증명 유무와 무관하게 "이
        # 회사는 지금 이 기능을 실행하면 안 된다"는 판정이 항상
        # 우선한다).
        if self._safety_service.is_emergency_stop_active():
            raise ConflictException(
                "비상정지가 활성화되어 있습니다 — 실제 발주를 시도하지 "
                "않습니다.",
            )
        function_mode = self._safety_service.get_function_mode(
            company_id, FunctionCode.PURCHASE_ORDER,
        )
        if function_mode in (FunctionMode.PAUSED, FunctionMode.ERROR):
            raise ConflictException(
                f"매입 발주 기능이 현재 \"{FunctionMode.LABELS_KO.get(function_mode, function_mode)}\" "
                f"상태입니다({FunctionMode.DESCRIPTIONS_KO.get(function_mode, '')}) — 새 발주를 "
                "시작하지 않습니다.",
            )

        self._validate_inputs(
            product_code=product_code, options=options,
            recv_name=recv_name, recv_tell=recv_tell, recv_mobile=recv_mobile,
            zipcode=zipcode, address=address,
        )

        # 2026-09-08 재정정 — "매입 작업 배정 가능 판정"과 별개의
        # 발주 전용 게이트. 이 검사는 select_connection_for_task()를
        # 호출하지 않는다(재사용 금지 — 두 판정이 같은 코드로
        # 결합되면 안 된다는 사용자 지시).
        connection = self._connection_service.verify_connection_ready_for_order_submission(
            connection_id, company_id,
        )

        # 2026-09-10 후속 — 발주 전용 Adapter를 한 번만 만든다(전에는
        # 발주 직전에 따로 만들었으나, 판매신청 게이트도 같은 Adapter가
        # 필요해져서 여기로 끌어올렸다 — 같은 연결의 자격증명을 이
        # 메서드 안에서 두 번 읽지 않는다).
        adapter = get_purchase_channel_adapter(
            connection.mall_code, credential_reference=connection.credential_reference,
            credential_store=self._credential_store,
        )

        # 2026-09-10 후속(온채널 공식 답변 — "발주 전 판매신청 필수"
        # 확정) — 이 게이트는 발주 시도 행(PurchaseOrderSubmissionAttempt)
        # 을 만들기 전에 막는다. 판매신청이 접수 확인(SUBMITTED)되지
        # 않은 상품은 애초에 발주를 시도조차 하지 않는다 — 미확인
        # 상태로 온채널에 발주를 보내 어떤 응답이 올지 추측하지 않는다.
        if not self._sales_application_service.is_sales_application_confirmed(
            connection.id, company_id, product_code,
        ):
            application = self._sales_application_service.ensure_sales_application_submitted(
                connection.id, company_id, product_code,
                triggered_by=triggered_by, confirm_real_submission=True,
                adapter=adapter,
            )
            if application.status not in SalesApplicationStatus.SATISFIES_ORDER_GATE:
                raise ConflictException(
                    f"상품({product_code})의 판매신청이 아직 접수 확인되지 "
                    f"않았습니다(상태: {application.status}) — 발주를 시도하지 "
                    "않습니다. 판매신청 결과를 먼저 확인하세요.",
                )

        # 2026-09-10 후속(Phase 4 — 포인트 잔액 사전 확인) — 발주 직전
        # 시점의 최신 포인트·상품가를 다시 조회한다(캐시·이전 조회값
        # 재사용 금지 — 잔액은 매 호출마다 달라질 수 있는 사실이다).
        self._verify_point_balance_or_block(
            adapter, product_code=product_code, options=options,
        )

        attempt = self._create_locked_attempt(
            connection_id=connection.id, company_id=company_id,
            purchase_task_id=purchase_task_id, idempotency_key=idempotency_key,
            mall_code=connection.mall_code, product_code=product_code,
            options=options, triggered_by=triggered_by,
        )

        attempt.status = OrderSubmissionStatus.IN_FLIGHT
        self.db.commit()
        self.db.refresh(attempt)

        from app.domains.purchase_task.onchannel_client import (
            OnchannelAuthenticationError, OnchannelNetworkError,
            OnchannelNotFoundError, OnchannelPermissionError,
            OnchannelRateLimitedError, OnchannelResponseFormatError,
            OnchannelValidationError, OnchannelOrderOption,
            OnchannelOrderRegistrationRequest,
        )

        request = OnchannelOrderRegistrationRequest(
            product_code=product_code,
            recv_name=recv_name, recv_tell=recv_tell, recv_mobile=recv_mobile,
            zipcode=zipcode, address=address,
            options=tuple(
                OnchannelOrderOption(id=o["id"], qty=o["qty"]) for o in options
            ),
            address_detail=address_detail, comment=comment,
            sale_code=idempotency_key, site_name=site_name,
        )

        from app.domains.purchase_task.channel_adapter import PurchaseChannelAdapterError

        try:
            order_code = adapter.submit_order(request)
        except (
            OnchannelAuthenticationError, OnchannelPermissionError,
            OnchannelNotFoundError, OnchannelRateLimitedError,
            OnchannelValidationError,
        ) as exc:
            # 온채널이 명시적으로 거부했다 — "안 됐다"는 사실 자체는
            # 확실하다. RESULT_UNKNOWN이 아니라 REJECTED다.
            self._finalize_attempt(
                attempt, status=OrderSubmissionStatus.REJECTED,
                failure_detail=str(exc),
            )
            raise
        except PurchaseChannelAdapterError as exc:
            # 자격증명 자체가 없어서 나는 예외 — 네트워크에 아예
            # 도달하지 않았다는 사실이 확실하므로 RESULT_UNKNOWN이
            # 아니라 REJECTED다(모호함이 없다).
            self._finalize_attempt(
                attempt, status=OrderSubmissionStatus.REJECTED,
                failure_detail=str(exc),
            )
            raise
        except (OnchannelNetworkError, OnchannelResponseFormatError) as exc:
            # 요청이 실제로 온채널에 도달해 주문이 생겼는지조차 알 수
            # 없다 — 실패로 단정하지 않는다.
            self._finalize_attempt(
                attempt, status=OrderSubmissionStatus.RESULT_UNKNOWN,
                failure_detail=str(exc),
            )
            raise
        except Exception as exc:  # noqa: BLE001 — 예상 못한 예외도 결과불명으로
            self._finalize_attempt(
                attempt, status=OrderSubmissionStatus.RESULT_UNKNOWN,
                failure_detail=f"{type(exc).__name__}: 예상하지 못한 오류",
            )
            raise

        self._finalize_attempt(
            attempt, status=OrderSubmissionStatus.SUCCEEDED,
            external_order_code=order_code,
        )
        return attempt

    # ---------------- 내부 ----------------

    def _validate_inputs(
        self, *, product_code, options, recv_name, recv_tell, recv_mobile,
        zipcode, address,
    ) -> None:
        """스펙이 필수로 정의한 필드만 검증한다 — 문서에 없는 조건을
        추측해서 추가 검증하지 않는다."""

        missing = []
        if not product_code or not product_code.strip():
            missing.append("product_code")
        if not options:
            missing.append("options")
        else:
            for opt in options:
                if "id" not in opt or "qty" not in opt:
                    raise BadRequestException(
                        "options의 각 항목은 id·qty를 모두 포함해야 합니다.",
                    )
                if not isinstance(opt["qty"], int) or opt["qty"] < 1:
                    raise BadRequestException("options[].qty는 1 이상의 정수여야 합니다.")
        if not recv_name or not recv_name.strip():
            missing.append("recv_name")
        if not recv_tell or not recv_tell.strip():
            missing.append("recv_tell")
        if not recv_mobile or not recv_mobile.strip():
            missing.append("recv_mobile")
        if not zipcode or not zipcode.strip():
            missing.append("zipcode")
        if not address or not address.strip():
            missing.append("address")

        if missing:
            raise BadRequestException(
                f"필수 항목이 비어 있습니다: {', '.join(missing)}",
            )

    def _verify_point_balance_or_block(
        self, adapter, *, product_code: str, options: list[dict],
    ) -> None:
        """2026-09-10 후속(Phase 4) — 발주 전 포인트(예치금) 잔액
        사전 확인. 온채널 공식 답변으로 `GET common/member/point`의
        `point`가 발주 가능 잔액 그 자체임이 확정됐다(constants.py의
        ONCHANNEL_ORDER_CONTRACT_STATUS PAYMENT_SOURCE 항목 참고).

        이 메서드가 실제로 계산할 수 있는 것은 "상품가×수량" 소계
        (`GET seller/product/{code}`로 매 호출 새로 조회 — 캐시된
        과거 값을 재사용하지 않는다, 가격 인상을 놓치지 않기 위해)
        뿐이다. **배송비를 사전에 확인할 방법이 온채널 스펙 어디에도
        없다**(`order/regist` 요청 바디에 배송비 필드가 없고, 별도
        배송비 견적 API도 없다 — docs/HOMEZ_ONCHANNEL_OPENAPI_
        FINDINGS_20260908.md 참고, `build_order_submission_review()`
        의 "배송비 미확인" 고지와 동일한 근거).

        그래서 이 메서드는 포인트·상품가가 전부 정상 확인되어도
        **항상 마지막에 차단한다** — "상품가만으로는 충분해 보여도
        배송비가 더해지면 잔액이 부족해질 수 있는지 확인할 방법이
        없다"는 사실 자체가 구조적 차단 사유다. 추측으로 "배송비
        포함 최종 금액이 잔액 이내일 것"이라고 넘기지 않는다 — 이
        차단은 온채널이 배송비 사전 확인 방법을 제공하기 전까지는
        구조적으로 풀리지 않는다(임시 결함이 아니라 현재 확인된
        사실 그 자체)."""

        from app.domains.purchase_task.channel_adapter import CapabilitySupport

        point_result = adapter.check_member_point()
        if point_result.support != CapabilitySupport.SUPPORTED:
            raise ConflictException(
                "포인트(예치금) 잔액을 확인할 수 없습니다 — 발주를 시도하지 "
                "않습니다.",
            )
        if not point_result.point_interpretable:
            raise ConflictException(
                "포인트(예치금) 응답을 해석할 수 없습니다(필드 누락·null·"
                "예상과 다른 타입) — 잔액을 0이나 임의값으로 추정하지 않고 "
                "발주를 차단합니다.",
            )

        product = adapter.lookup_product(product_code)
        if product.support != CapabilitySupport.SUPPORTED:
            raise ConflictException(
                "상품·옵션 가격을 확인할 수 없습니다 — 발주를 시도하지 "
                "않습니다.",
            )
        option_by_id = {opt.option_id: opt for opt in product.options}
        item_subtotal = 0
        for requested in options:
            option = option_by_id.get(str(requested["id"]))
            if option is None or option.price is None:
                raise ConflictException(
                    f"옵션({requested['id']})의 실제 가격을 확인할 수 없습니다 "
                    "(선택한 옵션·상품 불일치 가능성 포함) — 발주를 시도하지 "
                    "않습니다.",
                )
            item_subtotal += option.price * requested["qty"]

        if point_result.point < item_subtotal:
            raise ConflictException(
                f"현재 포인트 잔액({point_result.point})이 상품가 소계"
                f"({item_subtotal})보다 적습니다(배송비 제외 기준으로도 "
                "이미 부족) — 발주를 시도하지 않습니다.",
            )

        # 상품가 소계까지는 잔액이 충분해 보이지만, 배송비를 더하면
        # 부족해질 수 있는지 확인할 방법이 없다 — 이 사실 자체가
        # 항상 최종 차단 사유다(위 클래스 docstring 참고).
        raise ConflictException(
            "온채널 배송비를 발주 전에 확인할 수 있는 API가 없어, 배송비를 "
            "포함한 최종 필요 포인트를 확정할 수 없습니다 — 상품가 소계 "
            f"({item_subtotal})만으로는 잔액 충분 여부를 확정할 수 없으므로 "
            "발주를 차단합니다(추측으로 통과시키지 않습니다).",
        )

    def _create_locked_attempt(
        self, *, connection_id, company_id, purchase_task_id, idempotency_key,
        mall_code, product_code, options, triggered_by,
    ) -> PurchaseOrderSubmissionAttempt:
        """(company_id, idempotency_key) UNIQUE 제약이 곧 잠금이다 —
        이미 존재하면 DB가 IntegrityError로 거부한다(사전 SELECT가
        아니라 INSERT 자체의 실패로 판단해, 동시 요청 두 개가 동시에
        "아직 없음"을 보고 둘 다 진행하는 경쟁 상태까지 막는다)."""

        attempt = PurchaseOrderSubmissionAttempt(
            company_id=company_id, connection_id=connection_id,
            purchase_task_id=purchase_task_id, idempotency_key=idempotency_key,
            mall_code=mall_code, product_code=product_code,
            options_json=json.dumps(options, ensure_ascii=False),
            status=OrderSubmissionStatus.PENDING,
            triggered_by=triggered_by, started_at=datetime.utcnow(),
        )
        self.db.add(attempt)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ConflictException(
                f"이미 이 idempotency_key(\"{idempotency_key}\")로 발주 시도가 "
                "있습니다 — 같은 시도를 다시 보내지 않습니다. 결과를 먼저 "
                "확인하고, 새로 시도하려면 새 idempotency_key를 쓰세요.",
            ) from exc
        self.db.refresh(attempt)
        return attempt

    def _finalize_attempt(
        self, attempt: PurchaseOrderSubmissionAttempt, *, status: str,
        external_order_code: str | None = None,
        failure_detail: str | None = None,
    ) -> None:

        attempt.status = status
        attempt.external_order_code = external_order_code
        attempt.failure_detail = failure_detail
        attempt.finished_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(attempt)


__all__ = ["PurchaseOrderSubmissionService"]
