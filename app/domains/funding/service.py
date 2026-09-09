"""
=========================================================
Homez OS

File : app/domains/funding/service.py

Funding Service — 사업 운영자금 / Available Funding

2026-08-15 V7 Gate 2 — Funding 회사 스코프 강화(요구사항 3):

  - Account CRUD(create_account/get_account/list_accounts/add_funding/
    remove_funding/list_ledgers)는 이제 company_id를 필수로 받고,
    조회 대상 계정이 그 회사 소유가 아니면 NotFoundException을
    던진다(settlement/decision과 동일한 패턴 — 다른 회사 account_id의
    존재 여부 자체를 노출하지 않는다). 실제 감사 결과: 기존 Router는
    company_id를 요청 바디(FundingAccountCreate.company_id)로 그대로
    받아 어떤 인증된 admin이든 임의의 company_id로 계정을 만들거나
    (타사 명의 사칭), account_id만 알면 다른 회사의 계정을 조회/증액/
    감액할 수 있었다(coupang/decision/settlement과 동일 클래스의
    실제 크로스테넌트 결함) — 이번에 company_id를 요청 바디에서
    제거하고 항상 current_user.company_id에서만 가져오도록 고쳤다.
  - ensure_supply_hold()에 선택적 company_id 매개변수를 추가했다.
    company_id가 주어지면 get_account_by_company()로 그 회사의 계정을
    직접 사용한다(추측 없음, 계정이 없으면 fail-closed 404) — 이것이
    "기본 계정 선택은 반드시 company_id를 요구해야 한다"는 요청
    원문이 가리키는 정상 흐름이다. company_id가 주어지지 않으면(현재
    유일한 실제 호출자인 order/purchase 도메인 경로 — 이 두 도메인
    자체에 company_id 컬럼이 없어 호출 시점에 넘길 수 없다) Gate R13의
    기존 보수적 fail-closed(계정 0/1개는 그대로, 2개 이상이면 차단)를
    그대로 유지한다. 어느 경로든 실제로 자금이 차감되는 계정이 항상
    유일하게 결정된 뒤에만 진행되므로, 이번 변경이 기존 안전성을
    낮추지 않는다.
  - FundingHold/SupplierPayment/FundingLedger 생성 시 company_id를
    항상 채운다(계정 조회로 이미 확보한 account.company_id를 그대로
    사용 — FundingAccount.company_id가 이제 NOT NULL이라 항상 값이
    있다).
=========================================================
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.domains.funding.model import FundingAccount
from app.domains.funding.model import FundingHold
from app.domains.funding.model import FundingLedger
from app.domains.funding.model import SupplierPayment
from app.domains.funding.repository import FundingRepository
from app.domains.funding.schema import FundingAccountCreate
from app.domains.funding.schema import FundingAmountUpdate
from app.domains.funding.schema import SupplierPaymentConfirm


class FundingService:
    """
    사업 운영자금 관리.

    은행/PG 연동이 아닌, 사업자가 입력한
    운영 가능 금액을 기준으로 Purchase를 게이트한다.
    """

    TYPE_CREATE = "FUNDING_CREATE"
    TYPE_ADD = "FUNDING_ADD"
    TYPE_REMOVE = "FUNDING_REMOVE"
    TYPE_HOLD_CREATE = "HOLD_CREATE"
    TYPE_HOLD_RELEASE = "HOLD_RELEASE"
    TYPE_HOLD_COMMIT = "HOLD_COMMIT"
    TYPE_FUNDING_PAYOUT = "FUNDING_PAYOUT"

    STATUS_HELD = "HELD"
    STATUS_COMMITTED = "COMMITTED"
    STATUS_RELEASED = "RELEASED"

    PAYMENT_PAID = "PAID"

    def __init__(
        self,
        db: Session,
    ):

        self.db = db
        self.repository = FundingRepository(db)

    # --------------------------------------------------
    # 계산
    # --------------------------------------------------

    @staticmethod
    def available_amount(
        account: FundingAccount,
    ) -> float:
        """Available Funding = 사업 운영자금 − Hold."""

        return float(account.total_funding) - float(
            account.held_amount
        )

    @staticmethod
    def supply_required(
        purchase_price: float | None,
        shipping_price: float | None,
    ) -> float:
        """주문/발주 공급 필요 자금."""

        return float(purchase_price or 0) + float(
            shipping_price or 0
        )

    @staticmethod
    def hold_idempotency_key(
        order_id: int,
    ) -> str:

        return f"order:{order_id}:supply_hold"

    @staticmethod
    def payment_idempotency_key(
        purchase_id: int,
    ) -> str:

        return f"purchase:{purchase_id}:supplier_payment"

    @staticmethod
    def settlement_deposit_key(
        settlement_id: int,
    ) -> str:
        """마켓 정산 입금 멱등 키."""

        return f"settlement:{settlement_id}:deposit"

    @staticmethod
    def settlement_reverse_key(
        settlement_id: int,
    ) -> str:
        """마켓 정산 환수 멱등 키."""

        return f"settlement:{settlement_id}:reverse"

    def _get_settlement_ledger(
        self,
        settlement_id: int,
        entry_type: str,
    ) -> FundingLedger | None:
        """settlement 참조 Ledger 조회 (멱등 확인)."""

        return (
            self.db.query(FundingLedger)
            .filter(
                FundingLedger.reference_type == "settlement",
            )
            .filter(
                FundingLedger.reference_id == settlement_id,
            )
            .filter(FundingLedger.type == entry_type)
            .first()
        )

    def _to_response_dict(
        self,
        account: FundingAccount,
    ) -> dict:

        return {
            "id": account.id,
            "company_id": account.company_id,
            "total_funding": float(account.total_funding),
            "held_amount": float(account.held_amount),
            "available_amount": self.available_amount(
                account,
            ),
            "currency": account.currency,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
        }

    def _append_ledger(
        self,
        account_id: int,
        company_id: int,
        amount: float,
        entry_type: str,
        reference_type: str | None = None,
        reference_id: int | None = None,
        memo: str | None = None,
    ) -> FundingLedger:

        ledger = FundingLedger(
            company_id=company_id,
            account_id=account_id,
            amount=amount,
            type=entry_type,
            reference_type=reference_type,
            reference_id=reference_id,
            memo=memo,
        )

        return self.repository.add_ledger(ledger)

    def _get_account_required(
        self,
        account_id: int,
    ) -> FundingAccount:

        account = self.repository.get_account(account_id)

        if account is None:
            raise NotFoundException(
                "Funding Account를 찾을 수 없습니다.",
            )

        return account

    def _get_account_required_for_company(
        self,
        account_id: int,
        company_id: int,
    ) -> FundingAccount:
        """
        id 일치 + 이 회사 소유인 계정만 반환한다(2026-08-15 V7
        Gate 2). 존재하지만 타사 소유인 경우도 "존재하지 않음"과
        동일한 메시지로 응답해 다른 회사의 account_id 존재 여부를
        드러내지 않는다(settlement.create()의 확립된 패턴과 동일).
        """

        account = self._get_account_required(account_id)

        if account.company_id != company_id:
            raise NotFoundException(
                "Funding Account를 찾을 수 없습니다.",
            )

        return account

    # --------------------------------------------------
    # Account CRUD
    # --------------------------------------------------

    def create_account(
        self,
        data: FundingAccountCreate,
        company_id: int,
    ) -> dict:
        """
        사업 운영자금 최초 등록. company_id는 항상 호출자(Router의
        current_user.company_id)에서만 온다 — 요청 바디로는 받지
        않는다(2026-08-15 V7 Gate 2, 타사 명의 계정 생성 차단).
        """

        existing = self.repository.get_account_by_company(company_id)

        if existing is not None:
            raise BadRequestException(
                "해당 회사에 이미 Funding Account가 "
                "존재합니다.",
            )

        account = FundingAccount(
            company_id=company_id,
            total_funding=float(data.total_funding),
            held_amount=0,
            currency=data.currency or "KRW",
        )

        account = self.repository.add_account(account)

        self._append_ledger(
            account.id,
            company_id,
            float(data.total_funding),
            self.TYPE_CREATE,
            reference_type="account",
            reference_id=account.id,
            memo="사업 운영자금 최초 등록",
        )

        return self._to_response_dict(account)

    def get_account(
        self,
        account_id: int,
        company_id: int,
    ) -> dict:

        account = self._get_account_required_for_company(
            account_id, company_id,
        )

        return self._to_response_dict(account)

    def list_accounts(
        self,
        company_id: int,
    ) -> list[dict]:
        """이 회사 소유 계정만 반환한다(2026-08-15 V7 Gate 2) — 보통
        0개 또는 1개(FundingAccount.company_id는 UNIQUE)다."""

        account = self.repository.get_account_by_company(company_id)

        if account is None:
            return []

        return [self._to_response_dict(account)]

    def add_funding(
        self,
        account_id: int,
        data: FundingAmountUpdate,
        company_id: int,
    ) -> dict:
        """사업 운영자금 추가 투입."""

        account = self._get_account_required_for_company(
            account_id, company_id,
        )
        amount = float(data.amount)

        account.total_funding = float(account.total_funding) + amount
        account = self.repository.save_account(account)

        self._append_ledger(
            account.id,
            company_id,
            amount,
            self.TYPE_ADD,
            reference_type="account",
            reference_id=account.id,
            memo=data.memo or "사업 운영자금 추가",
        )

        return self._to_response_dict(account)

    def remove_funding(
        self,
        account_id: int,
        data: FundingAmountUpdate,
        company_id: int,
    ) -> dict:
        """사업 운영자금 회수 (Hold보다 낮출 수 없음)."""

        account = self._get_account_required_for_company(
            account_id, company_id,
        )
        amount = float(data.amount)
        new_total = float(account.total_funding) - amount

        if new_total < float(account.held_amount):
            raise BadRequestException(
                "회수 후 사업 운영자금이 "
                "예약(Hold) 금액보다 작아질 수 없습니다.",
            )

        if new_total < 0:
            raise BadRequestException(
                "사업 운영자금은 0 미만이 될 수 없습니다.",
            )

        account.total_funding = new_total
        account = self.repository.save_account(account)

        self._append_ledger(
            account.id,
            company_id,
            amount,
            self.TYPE_REMOVE,
            reference_type="account",
            reference_id=account.id,
            memo=data.memo or "사업 운영자금 회수",
        )

        return self._to_response_dict(account)

    def apply_settlement_deposit(
        self,
        account_id: int,
        settlement_id: int,
        net_amount: float,
        memo: str | None = None,
    ) -> FundingLedger:
        """
        마켓 정산 입금 → 사업 운영자금 증가.

        멱등: settlement:{id}:deposit
        Ledger: FUNDING_ADD / reference_type=settlement

        주의: 기존 Ledger가 있어도 여기서 그대로 반환하지 않는다.
        Service가 이미 DEPOSITED인 Settlement는 진입 전에 조기
        반환하므로, PENDING인 채로 이 메서드가 호출됐는데 기존
        Ledger가 발견되는 것은 역사적 부분 commit 또는 경쟁
        요청일 수 있어 신뢰할 수 없다. 항상 새 INSERT를 시도해
        부분 유일 인덱스가 IntegrityError로 판단하게 하고,
        검증(reference/account/amount 일치)은 SettlementService의
        rollback 이후 재조회 경로에서 수행한다.
        """

        amount = float(net_amount)

        if amount <= 0:
            raise BadRequestException(
                "정산 입금 금액은 0보다 커야 합니다.",
            )

        account = self._get_account_required(account_id)
        deposit_key = self.settlement_deposit_key(
            settlement_id,
        )

        # Ledger INSERT + flush로 중복(동시 요청)을 먼저 검출한다.
        # 여기서 부분 유일 인덱스 위반 시 IntegrityError가 발생하며,
        # Account 잔액은 아직 변경하지 않은 상태이다.
        ledger = FundingLedger(
            company_id=account.company_id,
            account_id=account.id,
            amount=amount,
            type=self.TYPE_ADD,
            reference_type="settlement",
            reference_id=settlement_id,
            memo=memo or (f"마켓 정산 입금 ({deposit_key})"),
        )
        ledger = self.repository.add_ledger_no_commit(ledger)

        if ledger is None or ledger.id is None:
            raise BadRequestException(
                "Funding Ledger 생성에 실패했습니다.",
            )

        # flush 성공 후에만 Account 잔액을 변경한다.
        account.total_funding = (
            float(account.total_funding) + amount
        )
        self.repository.save_account_no_commit(account)

        return ledger

    def apply_settlement_reverse(
        self,
        account_id: int,
        settlement_id: int,
        net_amount: float,
        memo: str | None = None,
    ) -> FundingLedger:
        """
        마켓 정산 환수 → 사업 운영자금 차감.

        멱등: settlement:{id}:reverse
        Ledger: FUNDING_REMOVE / reference_type=settlement

        주의: apply_settlement_deposit와 동일한 이유로, 기존 Ledger를
        발견해도 그대로 반환하지 않고 항상 새 INSERT를 시도한다.
        """

        amount = float(net_amount)

        if amount <= 0:
            raise BadRequestException(
                "정산 환수 금액은 0보다 커야 합니다.",
            )

        account = self._get_account_required(account_id)
        reverse_key = self.settlement_reverse_key(
            settlement_id,
        )
        new_total = float(account.total_funding) - amount

        if new_total < float(account.held_amount):
            raise BadRequestException(
                "환수 후 사업 운영자금이 "
                "예약(Hold) 금액보다 작아질 수 없습니다.",
            )

        if new_total < 0:
            raise BadRequestException(
                "사업 운영자금은 0 미만이 될 수 없습니다.",
            )

        # Ledger INSERT + flush로 중복(동시 요청)을 먼저 검출한다.
        # Account 잔액은 아직 변경하지 않은 상태이다.
        ledger = FundingLedger(
            company_id=account.company_id,
            account_id=account.id,
            amount=amount,
            type=self.TYPE_REMOVE,
            reference_type="settlement",
            reference_id=settlement_id,
            memo=memo or (f"마켓 정산 환수 ({reverse_key})"),
        )
        ledger = self.repository.add_ledger_no_commit(ledger)

        if ledger is None or ledger.id is None:
            raise BadRequestException(
                "Funding Reverse Ledger 생성에 실패했습니다.",
            )

        # flush 성공 후에만 Account 잔액을 변경한다.
        account.total_funding = new_total
        self.repository.save_account_no_commit(account)

        return ledger

    def list_ledgers(
        self,
        account_id: int,
        company_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> list[FundingLedger]:

        self._get_account_required_for_company(account_id, company_id)

        return self.repository.list_ledgers(
            account_id,
            skip,
            limit,
        )

    # --------------------------------------------------
    # Hold
    # --------------------------------------------------

    def _resolve_account_for_hold(
        self,
        company_id: int | None,
    ) -> FundingAccount:
        """
        ensure_supply_hold()의 계정 결정 로직(2026-08-15 V7 Gate 2).

        company_id가 주어지면(요청 원문이 가리키는 정상 흐름) 그 회사의
        계정을 결정론적으로 사용한다 — 추측 없음, 없으면 fail-closed
        404. company_id가 주어지지 않으면(현재 유일한 실제 호출자인
        order/purchase 경로 — 이 두 도메인 자체에 company_id 컬럼이
        없어 호출 시점에 넘길 수 없다, Gate 2 범위 밖) Gate R13의 기존
        보수적 fail-closed(계정 0/1개는 그대로, 2개 이상이면 차단)를
        그대로 유지한다.
        """

        if company_id is not None:
            account = self.repository.get_account_by_company(company_id)

            if account is None:
                raise NotFoundException(
                    "이 회사의 Funding Account(사업 운영자금)가 "
                    "없습니다.",
                )

            return account

        # 2026-08-14 테넌트 격리 감사(Gate R13) — get_default_account()는
        # company_id를 구분하지 않는다(호출 체인에 company_id 자체가
        # 없음, app/domains/funding/repository.py 참고). 계정이 2개
        # 이상이면 "첫 계정"을 추측하지 않고 여기서 즉시 차단한다.
        if self.repository.count_accounts() > 1:
            raise BadRequestException(
                "FundingAccount가 2개 이상 존재해 발주 Hold에 사용할 "
                "기본 계정을 안전하게 결정할 수 없습니다 — 이 발주 "
                "경로(Order/Purchase)는 아직 company_id로 계정을 "
                "지정할 수 없는 상태입니다(2026-08-14 테넌트 격리 "
                "감사, 별도 재설계 필요).",
            )

        account = self.repository.get_default_account()

        if account is None:
            raise BadRequestException(
                "등록된 Funding Account(사업 운영자금)가 "
                "없습니다.",
            )

        return account

    def ensure_supply_hold(
        self,
        order_id: int,
        amount: float,
        purchase_id: int | None = None,
        company_id: int | None = None,
    ) -> FundingHold:
        """
        주문 공급 비용 Hold.

        Available Funding 부족 시 거부.
        동일 주문 active Hold는 재사용(중복 방지).

        2026-08-15 V7 Gate 2 — company_id는 선택적이다(위
        _resolve_account_for_hold() docstring 참고).
        """

        required = float(amount)

        if required <= 0:
            raise BadRequestException(
                "공급 필요 자금은 0보다 커야 합니다.",
            )

        existing = self.repository.get_active_hold_by_order(
            order_id,
        )

        if existing is not None:
            if purchase_id is not None and existing.purchase_id is None:
                existing.purchase_id = purchase_id
                return self.repository.save_hold(existing)

            return existing

        account = self._resolve_account_for_hold(company_id)

        available = self.available_amount(account)

        if available < required:
            raise BadRequestException(
                "Available Funding(운영 가능 금액)이 "
                "부족하여 발주를 진행할 수 없습니다. "
                f"필요={required}, 가능={available}",
            )

        key = self.hold_idempotency_key(order_id)
        prior = self.repository.get_hold_by_idempotency(
            account.company_id, key,
        )

        if prior is not None and prior.status == self.STATUS_HELD:
            return prior

        if prior is not None and prior.status == self.STATUS_COMMITTED:
            raise BadRequestException(
                "이미 공급처 지급이 확정(COMMITTED)된 "
                "주문 Hold입니다.",
            )

        if prior is not None and prior.status == self.STATUS_RELEASED:
            available = self.available_amount(account)

            if available < required:
                raise BadRequestException(
                    "Available Funding(운영 가능 금액)이 "
                    "부족하여 발주를 진행할 수 없습니다. "
                    f"필요={required}, 가능={available}",
                )

            prior.amount = required
            prior.status = self.STATUS_HELD
            prior.purchase_id = purchase_id
            prior.company_id = account.company_id
            hold = self.repository.save_hold(prior)

            account.held_amount = (
                float(account.held_amount) + required
            )
            self.repository.save_account(account)

            self._append_ledger(
                account.id,
                account.company_id,
                required,
                self.TYPE_HOLD_CREATE,
                reference_type="order",
                reference_id=order_id,
                memo="공급 비용 Hold 재활성화",
            )

            return hold

        hold = FundingHold(
            company_id=account.company_id,
            account_id=account.id,
            order_id=order_id,
            purchase_id=purchase_id,
            amount=required,
            status=self.STATUS_HELD,
            idempotency_key=key,
        )

        hold = self.repository.add_hold(hold)

        account.held_amount = float(account.held_amount) + required
        self.repository.save_account(account)

        self._append_ledger(
            account.id,
            account.company_id,
            required,
            self.TYPE_HOLD_CREATE,
            reference_type="order",
            reference_id=order_id,
            memo="공급 비용 Hold",
        )

        return hold

    def attach_purchase_to_hold(
        self,
        order_id: int,
        purchase_id: int,
    ) -> FundingHold | None:

        hold = self.repository.get_active_hold_by_order(
            order_id,
        )

        if hold is None:
            return None

        hold.purchase_id = purchase_id

        return self.repository.save_hold(hold)

    def release_hold_for_order(
        self,
        order_id: int,
    ) -> FundingHold | None:
        """주문 Hold 해제 — Available Funding 복구 (HELD만)."""

        hold = self.repository.get_active_hold_by_order(
            order_id,
        )

        if hold is None:
            return None

        account = self._get_account_required(hold.account_id)

        hold.status = self.STATUS_RELEASED
        hold = self.repository.save_hold(hold)

        account.held_amount = max(
            0.0,
            float(account.held_amount) - float(hold.amount),
        )
        self.repository.save_account(account)

        self._append_ledger(
            account.id,
            account.company_id,
            float(hold.amount),
            self.TYPE_HOLD_RELEASE,
            reference_type="order",
            reference_id=order_id,
            memo="공급 비용 Hold 해제",
        )

        return hold

    # --------------------------------------------------
    # Supplier Payment Commit
    # --------------------------------------------------

    def confirm_supplier_payment(
        self,
        purchase_id: int,
        data: SupplierPaymentConfirm | None = None,
        company_id: int | None = None,
    ) -> SupplierPayment:
        """
        공급처 지급 수동 확정.

        HELD → COMMITTED
        held_amount↓ + total_funding↓
        Ledger: HOLD_COMMIT + FUNDING_PAYOUT

        complete_purchase와 연결하지 않는다.

        2026-08-15 V7 Gate 2 — company_id는 선택적이다. Purchase/Order
        Model에는 company_id 컬럼이 아직 없어(Gate 2 범위 밖, V7 Gate
        3/4에서 설계 예정) 이 메서드의 유일한 실제 호출자(Router)는
        여전히 company_id 없이 호출한다 — 이 잔존 격차를 그대로
        투명하게 남긴다. company_id가 주어지면(향후 호출자를 위한
        방어적 확장) 조회된 계정이 그 회사 소유인지 추가로 검증한다.
        """

        # 순환 import 방지 (PurchaseService → FundingService)
        from app.domains.purchase.model import Purchase

        purchase = (
            self.db.query(Purchase)
            .filter(Purchase.id == purchase_id)
            .first()
        )

        if purchase is None:
            raise NotFoundException(
                "Purchase를 찾을 수 없습니다.",
            )

        payment_key = self.payment_idempotency_key(purchase_id)
        existing_payment = self.repository.get_payment_by_purchase(
            purchase_id,
        )

        if existing_payment is None and company_id is not None:
            existing_payment = self.repository.get_payment_by_idempotency(
                company_id, payment_key,
            )

        if existing_payment is not None:
            raise BadRequestException(
                "해당 Purchase에 대한 공급처 지급이 "
                "이미 확정되어 있습니다.",
            )

        hold = self.repository.get_held_by_purchase(purchase_id)

        if hold is None:
            hold = self.repository.get_active_hold_by_order(
                purchase.order_id,
            )

        if hold is None:
            latest = self.repository.get_hold_by_purchase(
                purchase_id,
            )

            if (
                latest is not None
                and latest.status == self.STATUS_COMMITTED
            ):
                raise BadRequestException(
                    "이미 COMMITTED된 Hold는 "
                    "재처리할 수 없습니다.",
                )

            raise BadRequestException(
                "HELD 상태의 Funding Hold가 없어 "
                "공급처 지급을 확정할 수 없습니다.",
            )

        if hold.status != self.STATUS_HELD:
            raise BadRequestException(
                "Hold가 HELD 상태가 아니어서 "
                "지급을 확정할 수 없습니다. "
                f"현재 상태: {hold.status}",
            )

        amount = float(hold.amount)

        if amount <= 0:
            raise BadRequestException(
                "Hold 금액이 유효하지 않습니다.",
            )

        memo = None

        if data is not None:
            memo = data.memo

        # 계정을 먼저 확보한다 — company_id가 주어졌으면 그 회사
        # 소유가 맞는지 여기서 검증하고(2026-08-15 V7 Gate 2), 이어서
        # SupplierPayment.company_id도 이 계정에서 비정규화한다.
        if company_id is not None:
            account = self._get_account_required_for_company(
                hold.account_id, company_id,
            )
        else:
            account = self._get_account_required(hold.account_id)

        # SupplierPayment.amount == FundingHold.amount
        payment = SupplierPayment(
            company_id=account.company_id,
            purchase_id=purchase.id,
            order_id=purchase.order_id,
            supplier_id=purchase.supplier_id,
            account_id=hold.account_id,
            hold_id=hold.id,
            amount=amount,
            status=self.PAYMENT_PAID,
            paid_at=datetime.utcnow(),
            idempotency_key=payment_key,
            memo=memo,
        )

        payment = self.repository.add_payment(payment)

        hold.status = self.STATUS_COMMITTED

        if hold.purchase_id is None:
            hold.purchase_id = purchase.id

        hold = self.repository.save_hold(hold)

        account.held_amount = max(
            0.0,
            float(account.held_amount) - amount,
        )
        account.total_funding = max(
            0.0,
            float(account.total_funding) - amount,
        )
        self.repository.save_account(account)

        self._append_ledger(
            account.id,
            account.company_id,
            amount,
            self.TYPE_HOLD_COMMIT,
            reference_type="purchase",
            reference_id=purchase.id,
            memo="공급 비용 Hold 확정(COMMITTED)",
        )

        self._append_ledger(
            account.id,
            account.company_id,
            amount,
            self.TYPE_FUNDING_PAYOUT,
            reference_type="supplier_payment",
            reference_id=payment.id,
            memo="공급처 지급 — 사업 운영자금 차감",
        )

        return payment


__all__ = [
    "FundingService",
]
