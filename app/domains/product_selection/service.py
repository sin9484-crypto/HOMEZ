"""
=========================================================
Homez OS

File : app/domains/product_selection/service.py

상품 선별 통합 화면(CA-2, 2026-08-21 CTO 지시) — 9단계를 하나의
사용자 흐름으로 연결한다. 이 서비스는 새 데이터를 쓰지 않는다 —
기존 도메인(채널 정책·공급처 연결·이미지·상품 후보 승인)을 읽기
전용으로 조회해 단계별 상태만 계산한다. 정책 차단과 경제성 미달을
절대 같은 판정으로 섞지 않는다 — CHANNEL_POLICY 단계와 PROFITABILITY_
RISK 단계는 서로 다른 원본 데이터에서 독립적으로 계산된다.
=========================================================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundException
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.service import require_active_capability
from app.domains.channel_policy.constants import ChannelPolicyResult
from app.domains.channel_policy.schema import MarginEstimateInput
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.media_asset.constants import MediaAssetOwnerType
from app.domains.media_asset.model import MediaAsset
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_selection.constants import SelectionItemStatus
from app.domains.product_selection.constants import SelectionStepCode
from app.domains.product_selection.schema import ProductSelectionOverviewResponse
from app.domains.product_selection.schema import ProfitabilityQuery
from app.domains.product_selection.schema import SelectionStepResult
from app.domains.source.repository import SourceRepository


class ProductSelectionService:

    def __init__(self, db: Session):

        self.db = db
        self.channel_policy_service = ChannelPolicyService(db)
        self.source_repository = SourceRepository(db)

    def get_overview(
        self, *, company_id: int, product_candidate_id: int,
        channel: str | None, profitability_query: ProfitabilityQuery | None,
    ) -> ProductSelectionOverviewResponse:

        require_active_capability(CapabilityCode.PRODUCT_SELECTION)

        candidate = self.db.get(ProductCandidate, product_candidate_id)
        if candidate is None:
            raise NotFoundException("상품 후보를 찾을 수 없습니다.")

        steps: list[SelectionStepResult] = []

        policy_step, policy_blocked = self._channel_policy_step(
            company_id, product_candidate_id, channel,
        )
        steps.append(policy_step)
        steps.append(
            self._legal_certification_step(policy_step),
        )
        steps.append(self._resale_rights_step())
        steps.append(
            self._image_rights_step(product_candidate_id),
        )

        links = self.source_repository.list_links_for_candidate(
            company_id, product_candidate_id,
        )
        steps.append(self._supplier_evidence_step(links))
        steps.append(self._inventory_moq_lead_time_step(links))
        steps.append(self._shipping_return_step(links))

        profitability_step, meets_target = self._profitability_step(
            company_id, profitability_query,
        )
        steps.append(profitability_step)

        steps.append(
            self._user_approval_step(company_id, product_candidate_id),
        )

        return ProductSelectionOverviewResponse(
            product_candidate_id=product_candidate_id,
            channel=channel,
            steps=steps,
            policy_blocked=policy_blocked,
            profitability_meets_target=meets_target,
        )

    # --------------------------------------------------
    # 1) 판매채널 정책
    # --------------------------------------------------

    def _channel_policy_step(
        self, company_id: int, product_candidate_id: int, channel: str | None,
    ) -> tuple[SelectionStepResult, bool]:

        if channel is None:
            return SelectionStepResult(
                step_code=SelectionStepCode.CHANNEL_POLICY,
                status=SelectionItemStatus.DATA_REQUIRED,
                detail_codes=["CHANNEL_NOT_SELECTED"],
                navigate_to="channels",
            ), False

        status = self.channel_policy_service.get_current_status(
            company_id, product_candidate_id, channel,
        )
        if status is None:
            return SelectionStepResult(
                step_code=SelectionStepCode.CHANNEL_POLICY,
                status=SelectionItemStatus.DATA_REQUIRED,
                detail_codes=["CHANNEL_POLICY_NOT_EVALUATED"],
                navigate_to="channels",
            ), False

        mapping = {
            ChannelPolicyResult.CHANNEL_ELIGIBLE: SelectionItemStatus.PASS,
            ChannelPolicyResult.CHANNEL_ELIGIBLE_WITH_ACTIONS:
                SelectionItemStatus.NEEDS_IMPROVEMENT,
            ChannelPolicyResult.CHANNEL_DATA_REQUIRED:
                SelectionItemStatus.DATA_REQUIRED,
            ChannelPolicyResult.CHANNEL_POLICY_BLOCKED:
                SelectionItemStatus.BLOCKED,
            ChannelPolicyResult.CHANNEL_POLICY_STALE:
                SelectionItemStatus.STALE,
        }
        item_status = mapping[status.result]
        blocked = status.result == ChannelPolicyResult.CHANNEL_POLICY_BLOCKED

        return SelectionStepResult(
            step_code=SelectionStepCode.CHANNEL_POLICY,
            status=item_status,
            detail_codes=[
                r.rule_code for r in status.rule_results
                if r.applies and not r.satisfied
            ],
            navigate_to="channels",
        ), blocked

    # --------------------------------------------------
    # 2) 법률·인증·안전 — 채널 정책의 인증 관련 규칙만 별도로 요약
    # --------------------------------------------------

    def _legal_certification_step(
        self, policy_step: SelectionStepResult,
    ) -> SelectionStepResult:

        cert_related = [
            code for code in policy_step.detail_codes
            if "CERTIFICATION" in code or "NOTICE_INFO" in code
        ]
        if not cert_related:
            status = SelectionItemStatus.PASS
        elif policy_step.status == SelectionItemStatus.BLOCKED:
            status = SelectionItemStatus.BLOCKED
        else:
            status = SelectionItemStatus.NEEDS_IMPROVEMENT

        return SelectionStepResult(
            step_code=SelectionStepCode.LEGAL_CERTIFICATION_SAFETY,
            status=status, detail_codes=cert_related,
            navigate_to="channels",
        )

    # --------------------------------------------------
    # 3) 재판매 권리 — 이 코드베이스에 별도 재판매 권리 도메인이
    #    없다(2026-08-21 확인). 추정하지 않고 항상 자료 필요로
    #    표시한다.
    # --------------------------------------------------

    def _resale_rights_step(self) -> SelectionStepResult:

        return SelectionStepResult(
            step_code=SelectionStepCode.RESALE_RIGHTS,
            status=SelectionItemStatus.DATA_REQUIRED,
            detail_codes=["RESALE_RIGHTS_DOMAIN_NOT_IMPLEMENTED"],
            navigate_to=None,
        )

    # --------------------------------------------------
    # 4) 이미지 권리
    # --------------------------------------------------

    def _image_rights_step(
        self, product_candidate_id: int,
    ) -> SelectionStepResult:

        assets = (
            self.db.query(MediaAsset)
            .filter(
                MediaAsset.owner_type == MediaAssetOwnerType.PRODUCT_CANDIDATE,
                MediaAsset.owner_id == product_candidate_id,
            )
            .all()
        )
        if not assets:
            return SelectionStepResult(
                step_code=SelectionStepCode.IMAGE_RIGHTS,
                status=SelectionItemStatus.DATA_REQUIRED,
                detail_codes=["NO_MEDIA_ASSETS"], navigate_to="media",
            )

        unverified = [a for a in assets if a.rights_status != "VERIFIED"]
        if unverified:
            return SelectionStepResult(
                step_code=SelectionStepCode.IMAGE_RIGHTS,
                status=SelectionItemStatus.NEEDS_IMPROVEMENT,
                detail_codes=["IMAGE_RIGHTS_UNVERIFIED"], navigate_to="media",
            )

        return SelectionStepResult(
            step_code=SelectionStepCode.IMAGE_RIGHTS,
            status=SelectionItemStatus.PASS, navigate_to="media",
        )

    # --------------------------------------------------
    # 5) 공급처와 공급 증빙 / 6) 재고·MOQ·리드타임 / 7) 배송·반품
    # --------------------------------------------------

    def _supplier_evidence_step(self, links: list) -> SelectionStepResult:

        if not links:
            return SelectionStepResult(
                step_code=SelectionStepCode.SUPPLIER_EVIDENCE,
                status=SelectionItemStatus.DATA_REQUIRED,
                detail_codes=["NO_SUPPLIER_LINK"], navigate_to="supplier-sourcing",
            )
        return SelectionStepResult(
            step_code=SelectionStepCode.SUPPLIER_EVIDENCE,
            status=SelectionItemStatus.PASS, navigate_to="supplier-sourcing",
        )

    def _inventory_moq_lead_time_step(self, links: list) -> SelectionStepResult:

        if not links:
            return SelectionStepResult(
                step_code=SelectionStepCode.INVENTORY_MOQ_LEAD_TIME,
                status=SelectionItemStatus.DATA_REQUIRED,
                detail_codes=["NO_SUPPLIER_LINK"], navigate_to="supplier-sourcing",
            )
        missing = [
            link for link in links
            if link.lead_time_days is None or link.stock_available is None
        ]
        status = (
            SelectionItemStatus.NEEDS_IMPROVEMENT if missing
            else SelectionItemStatus.PASS
        )
        return SelectionStepResult(
            step_code=SelectionStepCode.INVENTORY_MOQ_LEAD_TIME,
            status=status,
            detail_codes=(
                ["LEAD_TIME_OR_STOCK_MISSING"] if missing else []
            ),
            navigate_to="supplier-sourcing",
        )

    def _shipping_return_step(self, links: list) -> SelectionStepResult:

        if not links:
            return SelectionStepResult(
                step_code=SelectionStepCode.SHIPPING_RETURN_FEASIBILITY,
                status=SelectionItemStatus.DATA_REQUIRED,
                detail_codes=["NO_SUPPLIER_LINK"], navigate_to="supplier-sourcing",
            )
        missing = [
            link for link in links
            if link.shipping_cost is None or not link.return_policy
        ]
        status = (
            SelectionItemStatus.NEEDS_IMPROVEMENT if missing
            else SelectionItemStatus.PASS
        )
        return SelectionStepResult(
            step_code=SelectionStepCode.SHIPPING_RETURN_FEASIBILITY,
            status=status,
            detail_codes=(
                ["SHIPPING_OR_RETURN_POLICY_MISSING"] if missing else []
            ),
            navigate_to="supplier-sourcing",
        )

    # --------------------------------------------------
    # 8) 예상 수익성과 위험 — 정책 판정과 절대 섞지 않는다.
    # --------------------------------------------------

    def _profitability_step(
        self, company_id: int, query: ProfitabilityQuery | None,
    ) -> tuple[SelectionStepResult, bool | None]:

        if query is None or query.sale_price is None:
            return SelectionStepResult(
                step_code=SelectionStepCode.PROFITABILITY_RISK,
                status=SelectionItemStatus.DATA_REQUIRED,
                detail_codes=["SALE_PRICE_NOT_PROVIDED"],
                navigate_to="economics",
            ), None

        result = self.channel_policy_service.estimate_margin(
            company_id,
            MarginEstimateInput(
                sale_price=query.sale_price,
                cost_of_goods=query.cost_of_goods,
            ),
        )
        if result.is_provisional:
            status = SelectionItemStatus.DATA_REQUIRED
            detail_codes = ["MARGIN_PROVISIONAL"] + result.missing_cost_fields
        elif result.meets_company_target is False:
            status = SelectionItemStatus.NEEDS_IMPROVEMENT
            detail_codes = ["BELOW_COMPANY_TARGET_MARGIN"]
        else:
            status = SelectionItemStatus.PASS
            detail_codes = []

        return SelectionStepResult(
            step_code=SelectionStepCode.PROFITABILITY_RISK,
            status=status, detail_codes=detail_codes, navigate_to="economics",
        ), result.meets_company_target

    # --------------------------------------------------
    # 9) 사용자 최종 승인
    # --------------------------------------------------

    def _user_approval_step(
        self, company_id: int, product_candidate_id: int,
    ) -> SelectionStepResult:

        selection = (
            self.db.query(ProductCandidateSelection)
            .filter(
                ProductCandidateSelection.company_id == company_id,
                ProductCandidateSelection.candidate_id == product_candidate_id,
            )
            .first()
        )
        if selection is None or selection.status == "HELD":
            return SelectionStepResult(
                step_code=SelectionStepCode.USER_FINAL_APPROVAL,
                status=SelectionItemStatus.DATA_REQUIRED,
                detail_codes=["NOT_YET_DECIDED"], navigate_to="candidates",
            )
        if selection.status == "REJECTED":
            return SelectionStepResult(
                step_code=SelectionStepCode.USER_FINAL_APPROVAL,
                status=SelectionItemStatus.BLOCKED,
                detail_codes=["REJECTED_BY_OPERATOR"], navigate_to="candidates",
            )
        return SelectionStepResult(
            step_code=SelectionStepCode.USER_FINAL_APPROVAL,
            status=SelectionItemStatus.PASS, navigate_to="candidates",
        )


__all__ = ["ProductSelectionService"]
