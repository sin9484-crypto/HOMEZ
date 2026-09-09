"""
=========================================================
Homez OS

File : app/domains/coupang/repository.py

쓰기 메서드는 commit하지 않고 flush만 수행한다(*_no_commit).
Transaction 경계(commit/rollback)는 CoupangIntegrationService가
소유한다 — product_candidate/repository.py와 동일한 패턴이다.

2026-08-14 테넌트 격리 감사(Gate R13) — 회사 스코프 조회로 전환:
CoupangMarketplaceProduct/CoupangProductOption/CoupangProductNotice/
CoupangProfitEstimate/CoupangDryRunAttempt/CoupangIntegrationDecision의
모든 단건 조회·목록 조회·조건부 UPDATE에 company_id 필터를 추가했다.
company_id 없이 id만으로 접근하는 옛 메서드는 삭제했다(절반짜리 수정
방지 — settlement/marketplace_listing 선례와 동일한 원칙).
CoupangPolicySet/CoupangPolicyRule은 전역 플랫폼 설정이라 예외다.
=========================================================
"""

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domains.coupang.model import CoupangDryRunAttempt
from app.domains.coupang.model import CoupangIntegrationDecision
from app.domains.coupang.model import CoupangMarketplaceProduct
from app.domains.coupang.model import CoupangPolicyRule
from app.domains.coupang.model import CoupangPolicySet
from app.domains.coupang.model import CoupangProductNotice
from app.domains.coupang.model import CoupangProductOption
from app.domains.coupang.model import CoupangProfitEstimate


class CoupangRepository:

    def __init__(self, db: Session):

        self.db = db

    # --------------------------------------------------
    # CoupangMarketplaceProduct (회사 스코프)
    # --------------------------------------------------

    def get_for_company(
        self, product_id: int, company_id: int,
    ) -> CoupangMarketplaceProduct | None:

        return (
            self.db.query(CoupangMarketplaceProduct)
            .filter(CoupangMarketplaceProduct.id == product_id)
            .filter(CoupangMarketplaceProduct.company_id == company_id)
            .first()
        )

    def get_by_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> CoupangMarketplaceProduct | None:

        return (
            self.db.query(CoupangMarketplaceProduct)
            .filter(CoupangMarketplaceProduct.company_id == company_id)
            .filter(
                CoupangMarketplaceProduct.idempotency_key == idempotency_key,
            )
            .first()
        )

    def list_products_for_company(
        self,
        company_id: int,
        status: str | None = None,
        sales_method: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[CoupangMarketplaceProduct]:

        query = self.db.query(CoupangMarketplaceProduct).filter(
            CoupangMarketplaceProduct.company_id == company_id,
        )

        if status is not None:
            query = query.filter(CoupangMarketplaceProduct.status == status)

        if sales_method is not None:
            query = query.filter(
                CoupangMarketplaceProduct.sales_method == sales_method,
            )

        return (
            query.order_by(CoupangMarketplaceProduct.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def add_no_commit(
        self, product: CoupangMarketplaceProduct,
    ) -> CoupangMarketplaceProduct:

        self.db.add(product)
        self.db.flush()

        return product

    def save_no_commit(
        self, product: CoupangMarketplaceProduct,
    ) -> CoupangMarketplaceProduct:

        self.db.add(product)
        self.db.flush()

        return product

    def update_status_conditional(
        self,
        product_id: int,
        company_id: int,
        expected_statuses: tuple[str, ...],
        new_status: str,
    ) -> int:
        """id 일치 + 회사 일치 + 현재 status가 기대값 중 하나일 때만 조건부 갱신."""

        stmt = (
            update(CoupangMarketplaceProduct)
            .where(CoupangMarketplaceProduct.id == product_id)
            .where(CoupangMarketplaceProduct.company_id == company_id)
            .where(CoupangMarketplaceProduct.status.in_(expected_statuses))
            .values(status=new_status)
        )

        result = self.db.execute(stmt)

        return result.rowcount

    def finalize_validation_conditional(
        self,
        product_id: int,
        company_id: int,
        expected_status: str,
        new_status: str,
        validation_status: str,
        validation_errors_json: str | None,
        risk_level: str | None,
        policy_version_applied: str | None,
    ) -> int:
        """
        정책 검증(또는 마진 기준 재검증) 결과를 status와 함께 단일 UPDATE로
        반영한다. ORM 객체를 조회 후 mutate하는 대신 Core 단일 UPDATE로
        처리해 rowcount 기반 동시성 검증을 그대로 유지한다.
        """

        stmt = (
            update(CoupangMarketplaceProduct)
            .where(CoupangMarketplaceProduct.id == product_id)
            .where(CoupangMarketplaceProduct.company_id == company_id)
            .where(CoupangMarketplaceProduct.status == expected_status)
            .values(
                status=new_status,
                validation_status=validation_status,
                validation_errors=validation_errors_json,
                risk_level=risk_level,
                policy_version_applied=policy_version_applied,
            )
        )

        result = self.db.execute(stmt)

        return result.rowcount

    # --------------------------------------------------
    # CoupangProductOption (회사 스코프, 부모에서 비정규화)
    # --------------------------------------------------

    def add_option_no_commit(
        self, option: CoupangProductOption,
    ) -> CoupangProductOption:

        self.db.add(option)
        self.db.flush()

        return option

    def list_options_for_company(
        self, coupang_product_id: int, company_id: int,
    ) -> list[CoupangProductOption]:

        return (
            self.db.query(CoupangProductOption)
            .filter(
                CoupangProductOption.coupang_product_id == coupang_product_id,
            )
            .filter(CoupangProductOption.company_id == company_id)
            .order_by(CoupangProductOption.id.asc())
            .all()
        )

    # --------------------------------------------------
    # CoupangPolicySet (전역 — company_id 없음)
    # --------------------------------------------------

    def add_policy_set_no_commit(
        self, policy_set: CoupangPolicySet,
    ) -> CoupangPolicySet:

        self.db.add(policy_set)
        self.db.flush()

        return policy_set

    def get_policy_set(self, policy_set_pk: int) -> CoupangPolicySet | None:

        return (
            self.db.query(CoupangPolicySet)
            .filter(CoupangPolicySet.id == policy_set_pk)
            .first()
        )

    def get_policy_set_by_business_id(
        self, policy_set_id: str,
    ) -> CoupangPolicySet | None:

        return (
            self.db.query(CoupangPolicySet)
            .filter(CoupangPolicySet.policy_set_id == policy_set_id)
            .first()
        )

    def list_policy_sets(self) -> list[CoupangPolicySet]:

        return (
            self.db.query(CoupangPolicySet)
            .order_by(CoupangPolicySet.id.desc())
            .all()
        )

    def list_active_policy_sets(self) -> list[CoupangPolicySet]:
        """
        is_active=True인 세트만 반환한다. VERIFIED/완전성/유효기간 등
        "사용 가능" 여부의 나머지 판단은 서비스 레벨에서 수행한다(이
        Repository는 단순 조회만 담당).
        """

        return (
            self.db.query(CoupangPolicySet)
            .filter(CoupangPolicySet.is_active.is_(True))
            .all()
        )

    # --------------------------------------------------
    # CoupangPolicyRule (전역 — company_id 없음)
    # --------------------------------------------------

    def add_policy_rule_no_commit(
        self, rule: CoupangPolicyRule,
    ) -> CoupangPolicyRule:

        self.db.add(rule)
        self.db.flush()

        return rule

    def list_active_policy_rules_for_sets(
        self, policy_set_ids: set[int],
    ) -> list[CoupangPolicyRule]:

        if not policy_set_ids:
            return []

        return (
            self.db.query(CoupangPolicyRule)
            .filter(CoupangPolicyRule.is_active.is_(True))
            .filter(CoupangPolicyRule.policy_set_id.in_(policy_set_ids))
            .all()
        )

    def list_rules_for_policy_set(
        self, policy_set_pk: int,
    ) -> list[CoupangPolicyRule]:

        return (
            self.db.query(CoupangPolicyRule)
            .filter(CoupangPolicyRule.policy_set_id == policy_set_pk)
            .all()
        )

    # --------------------------------------------------
    # CoupangProductNotice (회사 스코프, 부모에서 비정규화)
    # --------------------------------------------------

    def add_notice_no_commit(
        self, notice: CoupangProductNotice,
    ) -> CoupangProductNotice:

        self.db.add(notice)
        self.db.flush()

        return notice

    def list_notices_for_company(
        self, coupang_product_id: int, company_id: int,
    ) -> list[CoupangProductNotice]:

        return (
            self.db.query(CoupangProductNotice)
            .filter(
                CoupangProductNotice.coupang_product_id
                == coupang_product_id,
            )
            .filter(CoupangProductNotice.company_id == company_id)
            .order_by(CoupangProductNotice.id.asc())
            .all()
        )

    def list_verified_notices_for_company(
        self, coupang_product_id: int, company_id: int,
    ) -> list[CoupangProductNotice]:
        """
        status == VERIFIED이고 content가 비어있지 않은 고시정보만
        반환한다 — Dry Run의 고시정보 완전성 판단에 사용된다.
        """

        return [
            notice
            for notice in self.list_notices_for_company(
                coupang_product_id, company_id,
            )
            if notice.status == "VERIFIED" and notice.content.strip()
        ]

    # --------------------------------------------------
    # CoupangProfitEstimate (append-only, 회사 스코프, 부모에서 비정규화)
    # --------------------------------------------------

    def add_profit_estimate_no_commit(
        self, estimate: CoupangProfitEstimate,
    ) -> CoupangProfitEstimate:

        self.db.add(estimate)
        self.db.flush()

        return estimate

    def get_latest_profit_estimate_for_company(
        self, coupang_product_id: int, company_id: int,
    ) -> CoupangProfitEstimate | None:

        return (
            self.db.query(CoupangProfitEstimate)
            .filter(
                CoupangProfitEstimate.coupang_product_id
                == coupang_product_id,
            )
            .filter(CoupangProfitEstimate.company_id == company_id)
            .order_by(CoupangProfitEstimate.id.desc())
            .first()
        )

    def list_profit_estimates_for_company(
        self, coupang_product_id: int, company_id: int,
    ) -> list[CoupangProfitEstimate]:

        return (
            self.db.query(CoupangProfitEstimate)
            .filter(
                CoupangProfitEstimate.coupang_product_id
                == coupang_product_id,
            )
            .filter(CoupangProfitEstimate.company_id == company_id)
            .order_by(CoupangProfitEstimate.id.asc())
            .all()
        )

    # --------------------------------------------------
    # CoupangDryRunAttempt (append-only, 회사 스코프)
    # --------------------------------------------------

    def add_dry_run_attempt_no_commit(
        self, attempt: CoupangDryRunAttempt,
    ) -> CoupangDryRunAttempt:

        self.db.add(attempt)
        self.db.flush()

        return attempt

    def get_dry_run_attempt_by_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> CoupangDryRunAttempt | None:

        return (
            self.db.query(CoupangDryRunAttempt)
            .filter(CoupangDryRunAttempt.company_id == company_id)
            .filter(
                CoupangDryRunAttempt.idempotency_key == idempotency_key,
            )
            .first()
        )

    # --------------------------------------------------
    # CoupangIntegrationDecision (append-only, 회사 스코프)
    # --------------------------------------------------

    def add_decision_no_commit(
        self, decision: CoupangIntegrationDecision,
    ) -> CoupangIntegrationDecision:

        self.db.add(decision)
        self.db.flush()

        return decision

    def get_decision_by_idempotency_key(
        self, company_id: int, idempotency_key: str,
    ) -> CoupangIntegrationDecision | None:

        return (
            self.db.query(CoupangIntegrationDecision)
            .filter(CoupangIntegrationDecision.company_id == company_id)
            .filter(
                CoupangIntegrationDecision.idempotency_key
                == idempotency_key,
            )
            .first()
        )

    def list_decisions_for_company(
        self, coupang_product_id: int, company_id: int,
    ) -> list[CoupangIntegrationDecision]:

        return (
            self.db.query(CoupangIntegrationDecision)
            .filter(
                CoupangIntegrationDecision.coupang_product_id
                == coupang_product_id,
            )
            .filter(CoupangIntegrationDecision.company_id == company_id)
            .order_by(CoupangIntegrationDecision.id.asc())
            .all()
        )


__all__ = [
    "CoupangRepository",
]
