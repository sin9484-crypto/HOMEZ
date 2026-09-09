"""
=========================================================
Homez OS

File : app/domains/settlement/service.py

Marketplace Settlement Service
=========================================================
"""

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.domains.funding.service import FundingService
from app.domains.settlement.model import MarketplaceSettlement
from app.domains.settlement.repository import SettlementRepository
from app.domains.settlement.schema import SettlementCreate
from app.domains.settlement.schema import SettlementMemoUpdate


class SettlementService:
    """
    마켓 정산 → 사업 운영자금 유입.

    Payment/PG와 분리하며, Funding Account에만 연결한다.
    """

    STATUS_PENDING = "PENDING"
    STATUS_DEPOSITED = "DEPOSITED"
    STATUS_CANCELLED = "CANCELLED"
    STATUS_REVERSED = "REVERSED"
    # 2026-08-15 V7 Gate 5(요구사항 4) — PENDING 전후로만 오가는
    # 곁가지 상태. DEPOSITED/REVERSED Fixed Rules와 독립적이다.
    STATUS_HELD = "HELD"
    STATUS_MISMATCH = "MISMATCH"

    def __init__(
        self,
        db: Session,
    ):

        self.db = db
        self.repository = SettlementRepository(db)
        self.funding = FundingService(db)

    # --------------------------------------------------
    # 회사 격리 (2026-08-14 테넌트 격리 감사)
    # --------------------------------------------------
    #
    # MarketplaceSettlement 테이블 자체에는 company_id 컬럼이 없다.
    # Source of Truth는 FundingAccount.company_id다(FundingAccount는
    # company_id에 UNIQUE 제약이 있어 회사당 최대 1개 계정만 가진다).
    # 이 Service의 모든 공개 메서드(get/list/create/confirm_deposit/
    # cancel/reverse)는 반드시 이 헬퍼로 얻은 account_id를 거쳐서만
    # Settlement에 접근한다 — settlement_id만으로 직접 조회하는 경로는
    # 없다(과거에는 repository.get(settlement_id)를 스코프 없이 호출해
    # 어떤 회사의 admin이든 다른 회사의 정산 기록을 id 추측만으로
    # 열람·입금확인·취소·환수(실제 자금 이동)할 수 있었다).

    def _require_company_account_id(
        self,
        company_id: int,
    ) -> int:

        account = self.funding.repository.get_account_by_company(
            company_id,
        )

        if account is None:
            raise NotFoundException(
                "Settlement를 찾을 수 없습니다.",
            )

        return account.id

    # --------------------------------------------------
    # 검증 / 키
    # --------------------------------------------------

    @staticmethod
    def validate_amounts(
        gross_amount: float,
        fee_amount: float,
        net_amount: float,
    ) -> None:
        """net == gross - fee. 불일치 시 차단 (자동 보정 금지)."""

        gross = float(gross_amount)
        fee = float(fee_amount)
        net = float(net_amount)
        expected = gross - fee

        if fee < 0:
            raise BadRequestException(
                "fee_amount는 0 이상이어야 합니다.",
            )

        if gross < 0:
            raise BadRequestException(
                "gross_amount는 0 이상이어야 합니다.",
            )

        if net <= 0:
            raise BadRequestException(
                "net_amount는 0보다 커야 합니다.",
            )

        if net != expected:
            raise BadRequestException(
                "net_amount는 gross_amount - fee_amount와 "
                "일치해야 합니다.",
            )

    @staticmethod
    def default_create_key(
        market: str,
        market_order_id: str,
    ) -> str:

        return f"settlement:create:{market}:{market_order_id}"

    # --------------------------------------------------
    # CRUD
    # --------------------------------------------------

    def create(
        self,
        data: SettlementCreate,
        company_id: int,
    ) -> MarketplaceSettlement:
        """정산 예정(PENDING) 생성. data.account_id는 반드시 호출자 회사
        소유의 Funding Account여야 한다(타사 account_id 지정 차단)."""

        self.validate_amounts(
            data.gross_amount,
            data.fee_amount,
            data.net_amount,
        )

        # Funding Account 존재 확인 + 이 회사 소유인지 확인. 존재하지만
        # 타사 소유인 경우에도 "존재하지 않음"과 동일한 메시지로 응답해
        # 다른 회사의 account_id 존재 여부를 드러내지 않는다.
        account = self.funding._get_account_required(data.account_id)

        if account.company_id != company_id:
            raise NotFoundException(
                "Funding Account를 찾을 수 없습니다.",
            )

        key = data.idempotency_key or self.default_create_key(
            data.market,
            data.market_order_id,
        )

        # (company_id, idempotency_key) 복합 조회 — 다른 회사가 같은
        # 문자열의 key를 쓰더라도 서로 간섭하지 않는다(2026-08-14
        # 테넌트 격리 감사).
        existing = self.repository.get_by_idempotency(company_id, key)

        if existing is not None and existing.account_id == account.id:
            return existing

        if existing is not None:
            # 동일 회사 안에서 동일 idempotency_key가 이미 다른
            # account_id로 존재하는 이례적 경우(FundingAccount는
            # company_id UNIQUE라 실제로는 발생할 수 없지만, 방어적으로
            # 유지) — store_connection에서 확립된 원칙과 동일하게
            # 타사 행을 반환하지 않고 명시적으로 충돌 처리한다.
            raise ConflictException(
                "idempotency_key가 이미 다른 요청에서 사용되었습니다.",
            )

        settlement = MarketplaceSettlement(
            company_id=company_id,
            market=data.market,
            market_order_id=data.market_order_id,
            order_id=data.order_id,
            account_id=data.account_id,
            gross_amount=float(data.gross_amount),
            fee_amount=float(data.fee_amount),
            net_amount=float(data.net_amount),
            status=self.STATUS_PENDING,
            idempotency_key=key,
            memo=data.memo,
        )

        return self.repository.add(settlement)

    def get(
        self,
        settlement_id: int,
        company_id: int,
    ) -> MarketplaceSettlement:

        account_id = self._require_company_account_id(company_id)

        settlement = self.repository.get_for_account(
            settlement_id,
            account_id,
        )

        if settlement is None:
            raise NotFoundException(
                "Settlement를 찾을 수 없습니다.",
            )

        return settlement

    def list(
        self,
        company_id: int,
        market: str | None = None,
        status: str | None = None,
        order_id: int | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[MarketplaceSettlement]:

        account = self.funding.repository.get_account_by_company(
            company_id,
        )

        if account is None:
            # 이 회사에 Funding Account 자체가 없으면 Settlement도
            # 존재할 수 없다 — 404 대신 빈 목록(list 엔드포인트의
            # 자연스러운 "없음" 표현).
            return []

        return self.repository.list_for_account(
            account.id,
            market=market,
            status=status,
            order_id=order_id,
            skip=skip,
            limit=limit,
        )

    # --------------------------------------------------
    # confirm_deposit
    # --------------------------------------------------

    def confirm_deposit(
        self,
        settlement_id: int,
        company_id: int,
        data: SettlementMemoUpdate | None = None,
    ) -> MarketplaceSettlement:
        """
        입금 확인 + Funding 반영.

        순서 (고정, 변경 금지):
        1) Settlement 조회
        2) DEPOSITED → 기존 반환 (Funding Add 금지)
        3) PENDING 확인
        4) Ledger INSERT/flush (apply_settlement_deposit)
        5) Account 반영/flush (apply_settlement_deposit 내부)
        6) Ledger 확인
        7) 조건부 상태 UPDATE (PENDING → DEPOSITED, rowcount==1 필수)
        8) commit 1회

        Account/Ledger/Settlement 변경은 하나의 Transaction으로
        묶여 최종 commit 1회로 처리된다.

        Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
        강제(SETTLEMENT). 이 메서드가 실제 대사·Funding 반영을
        수행하는 진입점이다.

        기존 Settlement Ledger를 발견해 그대로 상태만 완료하는
        경로는 없다(apply_settlement_deposit가 항상 새 INSERT를
        시도). 동시 요청으로 Ledger INSERT flush 단계에서 부분
        유일 인덱스(uq_funding_ledger_settlement_type) 위반이
        발생하거나, 조건부 UPDATE의 rowcount가 1이 아니면(그 사이
        상태가 바뀜) 전체 rollback한다. IntegrityError 후에는
        새 쿼리로 재조회해 Settlement 상태·Ledger 타입·참조·
        계좌·금액·funding_ledger_id가 모두 일치할 때만 경쟁
        승자의 결과를 반환하고, 하나라도 어긋나면(역사적 불일치
        포함) 원래 예외를 그대로 재발생시킨다.

        Audit(2026-08-21, AG-0) — 되돌림: 입금 확인은 운영자가 직접
        수행하는 핵심 재무 업무다(AI가 대신 확정하면 안 된다는
        원칙과도 부합) — AI Capability Registry로 게이트하지 않는다.
        SETTLEMENT은 향후 "예상·실제 정산 차이 분석" 같은 AI 추천
        기능(아직 미구현)에만 적용한다.
        """

        # 1) Settlement 조회 (회사 범위 — 타사 settlement_id는 404)
        settlement = self.get(settlement_id, company_id)

        # 2) DEPOSITED → 기존 반환, Funding Add 금지
        if settlement.status == self.STATUS_DEPOSITED:
            return settlement

        # 3) PENDING 확인
        if settlement.status != self.STATUS_PENDING:
            raise BadRequestException(
                f"PENDING 상태에서만 입금 확인할 수 있습니다. "
                f"(현재: {settlement.status})",
            )

        memo = None

        if data is not None and data.memo:
            memo = data.memo

        settlement_id_value = settlement.id
        account_id_value = settlement.account_id
        net_amount_value = float(settlement.net_amount)

        try:

            # 4~5) Funding 반영 (Ledger INSERT/flush → Account 반영/flush)
            ledger = self.funding.apply_settlement_deposit(
                account_id=account_id_value,
                settlement_id=settlement_id_value,
                net_amount=net_amount_value,
                memo=memo,
            )

            # 6) Ledger 확인
            if ledger is None or ledger.id is None:
                raise BadRequestException(
                    "Funding Ledger 생성에 실패했습니다.",
                )

            if ledger.type != FundingService.TYPE_ADD:
                raise BadRequestException(
                    "Funding Ledger 타입이 FUNDING_ADD가 아닙니다.",
                )

            if (
                ledger.reference_type != "settlement"
                or ledger.reference_id != settlement_id_value
            ):
                raise BadRequestException(
                    "Funding Ledger 참조가 Settlement와 "
                    "일치하지 않습니다.",
                )

            # 7) 조건부 상태 UPDATE (오래된 ORM 객체를 save하지 않는다)
            #    account_id도 함께 조건에 걸어 회사 경계를 이중으로
            #    강제한다(위 1단계 조회가 이미 회사 범위지만, 방어적
            #    depth로 UPDATE 자체도 스코프한다).
            updated_rows = self.repository.confirm_deposit_conditional(
                settlement_id=settlement_id_value,
                account_id=account_id_value,
                funding_ledger_id=ledger.id,
                deposited_at=datetime.utcnow(),
                memo=memo,
            )

            if updated_rows != 1:
                raise ConflictException(
                    "Settlement 상태가 동시에 변경되어 "
                    "갱신할 수 없습니다.",
                )

            # 8) 최상위(Settlement Service)에서 commit 1회
            self.db.commit()

        except IntegrityError:

            self.db.rollback()

            # rollback 이전 객체는 재사용하지 않고 새 쿼리로 조회한다.
            existing_ledger = self.funding._get_settlement_ledger(
                settlement_id_value,
                FundingService.TYPE_ADD,
            )
            refreshed = self.repository.get(settlement_id_value)

            if (
                existing_ledger is not None
                and refreshed is not None
                and refreshed.status == self.STATUS_DEPOSITED
                and existing_ledger.type == FundingService.TYPE_ADD
                and existing_ledger.reference_type == "settlement"
                and existing_ledger.reference_id == refreshed.id
                and existing_ledger.account_id == refreshed.account_id
                and float(existing_ledger.amount)
                == float(refreshed.net_amount)
                and refreshed.funding_ledger_id == existing_ledger.id
            ):
                return refreshed

            # 경쟁 승자를 완전히 확인할 수 없으면(역사적 불일치 포함)
            # 원래 예외를 숨기지 않는다. 자동 보정하지 않는다.
            raise

        except Exception:

            self.db.rollback()

            raise

        # rollback 이전 객체를 재사용하지 않고, commit 이후 상태로 재조회한다.
        return self.repository.get(settlement_id_value)

    # --------------------------------------------------
    # cancel / reverse
    # --------------------------------------------------

    def cancel(
        self,
        settlement_id: int,
        company_id: int,
        data: SettlementMemoUpdate | None = None,
    ) -> MarketplaceSettlement:
        """PENDING → CANCELLED. Funding 무변경."""

        settlement = self.get(settlement_id, company_id)

        if settlement.status == self.STATUS_CANCELLED:
            return settlement

        if settlement.status != self.STATUS_PENDING:
            raise BadRequestException(
                "PENDING 상태에서만 취소할 수 있습니다. "
                f"(현재: {settlement.status})",
            )

        settlement.status = self.STATUS_CANCELLED

        if data is not None and data.memo:
            settlement.memo = data.memo

        return self.repository.save(settlement)

    def reverse(
        self,
        settlement_id: int,
        company_id: int,
        data: SettlementMemoUpdate | None = None,
    ) -> MarketplaceSettlement:
        """
        DEPOSITED → REVERSED.

        Funding REMOVE + 멱등 settlement:{id}:reverse.
        이미 REVERSED면 재차감 금지, 기존 반환.

        순서 (고정, 변경 금지):
        1) Settlement 조회
        2) REVERSED → 기존 반환 (Funding Remove 금지)
        3) DEPOSITED 확인
        4) Ledger INSERT/flush (apply_settlement_reverse)
        5) Account 반영/flush (apply_settlement_reverse 내부)
        6) Ledger 확인
        7) 조건부 상태 UPDATE (DEPOSITED → REVERSED, rowcount==1 필수)
        8) commit 1회

        기존 Settlement Ledger를 발견해 그대로 상태만 완료하는
        경로는 없다(apply_settlement_reverse가 항상 새 INSERT를
        시도). 부분 유일 인덱스 위반이나 조건부 UPDATE의 rowcount
        불일치(그 사이 상태가 바뀜) 시 전체 rollback한다.
        IntegrityError 후에는 새 쿼리로 재조회해 상태·Ledger 타입·
        참조·계좌·금액이 모두 일치할 때만 경쟁 승자의 결과를
        반환하고, 어긋나면 원래 예외를 그대로 재발생시킨다.
        """

        settlement = self.get(settlement_id, company_id)

        # 이미 REVERSED → 재차감 금지
        if settlement.status == self.STATUS_REVERSED:
            return settlement

        if settlement.status != self.STATUS_DEPOSITED:
            raise BadRequestException(
                "DEPOSITED 상태에서만 환수(reverse)할 수 "
                f"있습니다. (현재: {settlement.status})",
            )

        memo = None

        if data is not None and data.memo:
            memo = data.memo

        settlement_id_value = settlement.id
        account_id_value = settlement.account_id
        net_amount_value = float(settlement.net_amount)

        try:

            # 4~5) Funding 반영 (Ledger INSERT/flush → Account 반영/flush)
            ledger = self.funding.apply_settlement_reverse(
                account_id=account_id_value,
                settlement_id=settlement_id_value,
                net_amount=net_amount_value,
                memo=memo,
            )

            # 6) Ledger 확인
            if ledger is None or ledger.id is None:
                raise BadRequestException(
                    "Funding Reverse Ledger 생성에 실패했습니다.",
                )

            if ledger.type != FundingService.TYPE_REMOVE:
                raise BadRequestException(
                    "Funding Ledger 타입이 FUNDING_REMOVE가 "
                    "아닙니다.",
                )

            if (
                ledger.reference_type != "settlement"
                or ledger.reference_id != settlement_id_value
            ):
                raise BadRequestException(
                    "Funding Ledger 참조가 Settlement와 "
                    "일치하지 않습니다.",
                )

            # 7) 조건부 상태 UPDATE (오래된 ORM 객체를 save하지 않는다)
            #    account_id도 함께 조건에 걸어 회사 경계를 이중으로
            #    강제한다.
            updated_rows = self.repository.reverse_conditional(
                settlement_id=settlement_id_value,
                account_id=account_id_value,
                memo=memo,
            )

            if updated_rows != 1:
                raise ConflictException(
                    "Settlement 상태가 동시에 변경되어 "
                    "갱신할 수 없습니다.",
                )

            # 8) 최상위(Settlement Service)에서 commit 1회
            self.db.commit()

        except IntegrityError:

            self.db.rollback()

            # rollback 이전 객체는 재사용하지 않고 새 쿼리로 조회한다.
            existing_ledger = self.funding._get_settlement_ledger(
                settlement_id_value,
                FundingService.TYPE_REMOVE,
            )
            refreshed = self.repository.get(settlement_id_value)

            if (
                existing_ledger is not None
                and refreshed is not None
                and refreshed.status == self.STATUS_REVERSED
                and existing_ledger.type == FundingService.TYPE_REMOVE
                and existing_ledger.reference_type == "settlement"
                and existing_ledger.reference_id == refreshed.id
                and existing_ledger.account_id == refreshed.account_id
                and float(existing_ledger.amount)
                == float(refreshed.net_amount)
            ):
                return refreshed

            # 경쟁 승자를 완전히 확인할 수 없으면(역사적 불일치 포함)
            # 원래 예외를 숨기지 않는다. 자동 보정하지 않는다.
            raise

        except Exception:

            self.db.rollback()

            raise

        # rollback 이전 객체를 재사용하지 않고, commit 이후 상태로 재조회한다.
        return self.repository.get(settlement_id_value)

    # --------------------------------------------------
    # HELD / MISMATCH (2026-08-15 V7 Gate 5, 요구사항 4)
    # --------------------------------------------------
    #
    # 정산 예정(PENDING)/완료(DEPOSITED)에 이어 불일치(MISMATCH)/
    # 보류(HELD) 상태를 추가한다. 둘 다 PENDING 전후로만 오가는
    # 곁가지 상태로 설계했다 — DEPOSITED/REVERSED Fixed Rules(입금
    # 확인 순서, Funding Ledger 멱등성 등)는 이번 추가로 전혀 바뀌지
    # 않는다. confirm_deposit()는 여전히 "PENDING 상태에서만" 입금
    # 확인이 가능하므로, HELD/MISMATCH 상태의 Settlement는 이미
    # 자동으로 입금 확인이 차단된다(별도 코드 변경 없이 fail-closed).

    def hold(
        self,
        settlement_id: int,
        company_id: int,
        data: SettlementMemoUpdate | None = None,
    ) -> MarketplaceSettlement:
        """PENDING → HELD. 조사를 위해 입금 확인을 잠시 막는다."""

        settlement = self.get(settlement_id, company_id)

        if settlement.status == self.STATUS_HELD:
            return settlement

        if settlement.status != self.STATUS_PENDING:
            raise BadRequestException(
                "PENDING 상태에서만 보류할 수 있습니다. "
                f"(현재: {settlement.status})",
            )

        memo = data.memo if data is not None else None

        rowcount = self.repository.hold_conditional(
            settlement.id, settlement.account_id, memo,
        )
        if rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "Settlement 상태가 동시에 변경되어 보류할 수 없습니다.",
            )

        self.db.commit()

        return self.repository.get(settlement_id)

    def release_hold(
        self,
        settlement_id: int,
        company_id: int,
    ) -> MarketplaceSettlement:
        """HELD → PENDING."""

        settlement = self.get(settlement_id, company_id)

        if settlement.status == self.STATUS_PENDING:
            return settlement

        if settlement.status != self.STATUS_HELD:
            raise BadRequestException(
                "HELD 상태에서만 보류를 해제할 수 있습니다. "
                f"(현재: {settlement.status})",
            )

        rowcount = self.repository.release_hold_conditional(
            settlement.id, settlement.account_id,
        )
        if rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "Settlement 상태가 동시에 변경되어 보류를 해제할 수 "
                "없습니다.",
            )

        self.db.commit()

        return self.repository.get(settlement_id)

    def flag_mismatch(
        self,
        settlement_id: int,
        company_id: int,
        data: SettlementMemoUpdate,
    ) -> MarketplaceSettlement:
        """
        PENDING/HELD → MISMATCH. 금액을 자동으로 고치지 않는다 —
        플래그만 한다(CLAUDE.md 최상위 규칙, 회귀 방지).
        """

        if data is None or not data.memo:
            raise BadRequestException(
                "불일치 사유(memo)를 반드시 기록해야 합니다.",
            )

        settlement = self.get(settlement_id, company_id)

        if settlement.status == self.STATUS_MISMATCH:
            return settlement

        if settlement.status not in (self.STATUS_PENDING, self.STATUS_HELD):
            raise BadRequestException(
                "PENDING 또는 HELD 상태에서만 불일치로 표시할 수 "
                f"있습니다. (현재: {settlement.status})",
            )

        rowcount = self.repository.flag_mismatch_conditional(
            settlement.id, settlement.account_id, data.memo,
        )
        if rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "Settlement 상태가 동시에 변경되어 불일치로 표시할 "
                "수 없습니다.",
            )

        self.db.commit()

        return self.repository.get(settlement_id)

    def resolve_mismatch(
        self,
        settlement_id: int,
        company_id: int,
        data: SettlementMemoUpdate,
    ) -> MarketplaceSettlement:
        """
        MISMATCH → PENDING. 금액을 자동으로 고치지 않는다 — 관리자가
        수동 조사를 마쳤다는 명시적 승인일 뿐이다(근거 memo 필수).
        """

        if data is None or not data.memo:
            raise BadRequestException(
                "해소 근거(memo)를 반드시 기록해야 합니다.",
            )

        settlement = self.get(settlement_id, company_id)

        if settlement.status == self.STATUS_PENDING:
            return settlement

        if settlement.status != self.STATUS_MISMATCH:
            raise BadRequestException(
                "MISMATCH 상태에서만 해소할 수 있습니다. "
                f"(현재: {settlement.status})",
            )

        rowcount = self.repository.resolve_mismatch_conditional(
            settlement.id, settlement.account_id, data.memo,
        )
        if rowcount != 1:
            self.db.rollback()
            raise ConflictException(
                "Settlement 상태가 동시에 변경되어 해소할 수 없습니다.",
            )

        self.db.commit()

        return self.repository.get(settlement_id)


__all__ = [
    "SettlementService",
]
