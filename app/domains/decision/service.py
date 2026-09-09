"""
=========================================================
Homez OS

File : app/domains/decision/service.py

HOMEZ V4 Decision AI — Service

흐름: ProductCandidate(V3 산출물) → 정책 로드(fail-closed) →
Emergency Stop 확인(최우선) → 결정적 축별 평가 → DecisionEvaluation
+ DecisionScore 저장 → 사람의 검토(승인/보류/거절/override).

고정 규칙(요청 원문 그대로 구현):
  - AI 출력만으로 candidate/evaluation을 APPROVED 상태로 전환하지
    않는다 — review()가 호출되어야만 평가가 REVIEWED로 바뀐다.
    ProductCandidate 자체의 상태 전이는 이 Domain이 건드리지 않는다
    (app/domains/product_candidate의 기존 승인 API를 통해서만
    가능 — Domain 경계 유지).
  - 정책 위반(POLICY_PROHIBITED_RISK 축)이나 안전 차단(Emergency
    Stop)을 점수로 상쇄하지 않는다.
  - Emergency Stop이 항상 최우선이다 — 점수 계산 자체를 시작하지
    않는다.
  - 동일 idempotency_key 요청은 멱등 처리(기존 행 반환).
  - 평가 함수 자체는 순수 함수라 동일 입력은 항상 동일 점수를
    만든다(결정적).
  - NaN/Infinity/음수/비정상 비율은 입력 검증에서 차단한다.
  - 금액/점수 계산은 Decimal만 사용한다.
  - 평가 생성/상태 갱신은 단일 Transaction, 조건부 UPDATE+rowcount로
    경쟁을 감지한다.

2026-08-14 테넌트 격리 감사(Gate R13) — DecisionEvaluation/Score/
Review/AuditLog는 이제 company_id를 필수로 받는다. 평가 입력·점수·
예상매출·가용자금·승인/보류/거절/override는 전부 그 평가를 요청한
회사만의 데이터다 — 다른 회사는 존재 자체를 모른다(404). idempotency_key
는 회사별로 독립이다. DecisionPolicy는 전역으로 유지한다(model.py의
코드 근거 참고, CTO 확인 대기 항목으로 별도 보고).
=========================================================
"""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.ai_governance.constants import CapabilityCode
from app.domains.ai_governance.service import require_active_capability
from app.domains.automation_safety.service import SafetyService
from app.domains.decision.constants import SCORE_MAX
from app.domains.decision.constants import SCORE_MIN
from app.domains.decision.constants import AuditEventType
from app.domains.decision.constants import DecisionPolicyStatus
from app.domains.decision.constants import EvaluationRecommendation
from app.domains.decision.constants import EvaluationStatus
from app.domains.decision.constants import PolicySource
from app.domains.decision.constants import ReviewAction
from app.domains.decision.constants import ScoreAxis
from app.domains.decision.model import CompanyDecisionPolicy
from app.domains.decision.model import DecisionAuditLog
from app.domains.decision.model import DecisionEvaluation
from app.domains.decision.model import DecisionPolicy
from app.domains.decision.model import DecisionReview
from app.domains.decision.model import DecisionScore
from app.domains.decision.repository import DecisionRepository
from app.domains.decision.schema import DecisionCompanyPolicyCreateRequest
from app.domains.decision.schema import DecisionPolicyCreateRequest
from app.domains.decision.schema import DecisionSupplementaryInputs
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.service import ProductCandidateService

EVALUATOR_KIND = "deterministic"
EVALUATOR_VERSION = "1.0.0"

_SCORE_Q = Decimal("0.0001")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

# 이 축이 심각하게 낮으면(정책/금지상품 위험) 총점이 높아도 자동으로
# REVIEW_REQUIRED로 강등한다 — 점수로 상쇄되지 않는다.
_POLICY_HARD_BLOCK_THRESHOLD = Decimal("40")

# 사용 가능한(데이터가 있는) 축이 이 개수 미만이면 전체를
# INSUFFICIENT_DATA로 처리한다(12개 중 절반).
_MIN_SUFFICIENT_AXES = 6


def _q_score(value) -> Decimal:

    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BadRequestException(f"숫자로 변환할 수 없는 값입니다: {value!r}") from exc

    if not decimal_value.is_finite():
        raise BadRequestException("NaN/Infinity 값은 허용되지 않습니다.")

    return decimal_value.quantize(_SCORE_Q)


def _clamp_score(value: Decimal) -> Decimal:

    if value < SCORE_MIN:
        return Decimal(SCORE_MIN)

    if value > SCORE_MAX:
        return Decimal(SCORE_MAX)

    return value


class AxisResult:

    __slots__ = (
        "axis", "raw_score", "confidence", "data_sufficient",
        "risk_flag", "evidence_text",
    )

    def __init__(
        self, axis: str, raw_score: Decimal, confidence: Decimal,
        data_sufficient: bool, risk_flag: bool, evidence_text: str,
    ):

        self.axis = axis
        self.raw_score = raw_score
        self.confidence = confidence
        self.data_sufficient = data_sufficient
        self.risk_flag = risk_flag
        self.evidence_text = evidence_text


def _insufficient(axis: str, reason: str) -> AxisResult:

    return AxisResult(
        axis=axis, raw_score=_ZERO, confidence=_ZERO,
        data_sufficient=False, risk_flag=False,
        evidence_text=f"데이터 부족: {reason}",
    )


def _candidate_score_axis(
    axis: str,
    candidate_value,
    candidate_confidence,
    field_label: str,
    invert: bool = False,
) -> AxisResult:

    if candidate_value is None:
        return _insufficient(axis, f"ProductCandidate.{field_label} 없음")

    value = _q_score(candidate_value)
    if value < 0 or value > 100:
        raise BadRequestException(
            f"{field_label} 값은 0~100 범위여야 합니다: {value}",
        )

    score = _clamp_score(_HUNDRED - value if invert else value)
    confidence = (
        _q_score(candidate_confidence)
        if candidate_confidence is not None
        else Decimal("0.5000")
    )

    return AxisResult(
        axis=axis, raw_score=score, confidence=_clamp_score(confidence),
        data_sufficient=True,
        risk_flag=(axis in ScoreAxis.HARD_BLOCK_AXES and score < _POLICY_HARD_BLOCK_THRESHOLD),
        evidence_text=f"ProductCandidate.{field_label}={value} 기반 산출",
    )


def _supplementary_score_axis(
    axis: str, value: Decimal | None, label: str,
) -> AxisResult:

    if value is None:
        return _insufficient(axis, f"보강 입력 {label} 미제공")

    score = _clamp_score(_q_score(value))

    return AxisResult(
        axis=axis, raw_score=score, confidence=Decimal("0.9000"),
        data_sufficient=True, risk_flag=False,
        evidence_text=f"운영자 제공 {label}={score} 기반 산출",
    )


def evaluate_axes(
    candidate: ProductCandidate,
    supplementary: DecisionSupplementaryInputs,
) -> list[AxisResult]:
    """
    순수 함수 — 동일한 candidate 필드값과 supplementary 입력이 주어지면
    항상 동일한 결과를 반환한다(결정적 평가). 외부 AI 호출이 준비되지
    않아 fixture/deterministic evaluator를 사용한다(가짜 AI를 실제 AI로
    보고하지 않기 위해 evaluator_kind="deterministic"으로 명시한다).
    """

    results: list[AxisResult] = []

    # 매출 가능성 — 보강 입력(gross_revenue) 필요
    if supplementary.gross_revenue is not None:
        gross = _q_score(supplementary.gross_revenue)
        if gross < 0:
            raise BadRequestException("gross_revenue는 음수일 수 없습니다.")
        score = _clamp_score(gross / Decimal("1000"))
        results.append(AxisResult(
            axis=ScoreAxis.REVENUE_POTENTIAL, raw_score=score,
            confidence=Decimal("0.9000"), data_sufficient=True,
            risk_flag=False,
            evidence_text=f"예상 매출 {gross} 기반 산출",
        ))
    else:
        results.append(_insufficient(
            ScoreAxis.REVENUE_POTENTIAL, "gross_revenue 미제공",
        ))

    # 마진과 수수료 — fulfillment_mode가 지정된 채널별 평가라면(app/
    # domains/marketplace_listing), 그 모드에 대응하는
    # fulfillment_specific_cost가 반드시 함께 있어야 한다. 없으면
    # INSUFFICIENT_DATA로 처리한다(0으로 자동 보정 금지, 다른 모드의
    # 비용으로 대체하지 않는다 — 채널·비용 혼합 방지).
    if supplementary.fulfillment_mode is not None:
        if supplementary.fulfillment_specific_cost is None:
            results.append(_insufficient(
                ScoreAxis.MARGIN_AND_FEES,
                f"fulfillment_mode={supplementary.fulfillment_mode} "
                "지정되었으나 fulfillment_specific_cost 미제공",
            ))
        else:
            cost = _q_score(supplementary.fulfillment_specific_cost)
            if cost < 0:
                raise BadRequestException(
                    "fulfillment_specific_cost는 음수일 수 없습니다.",
                )
            score = _clamp_score(_HUNDRED - cost / Decimal("100"))
            results.append(AxisResult(
                axis=ScoreAxis.MARGIN_AND_FEES, raw_score=score,
                confidence=Decimal("0.9000"), data_sufficient=True,
                risk_flag=False,
                evidence_text=(
                    f"{supplementary.fulfillment_mode} 방식 비용 "
                    f"{cost} 기반 산출"
                ),
            ))

    # 보강 입력(expected_margin_rate) 우선, 없으면
    # ProductCandidate.margin_score로 대체.
    elif supplementary.expected_margin_rate is not None:
        rate = _q_score(supplementary.expected_margin_rate)
        if rate < 0:
            raise BadRequestException(
                "expected_margin_rate는 음수일 수 없습니다.",
            )
        score = _clamp_score(rate * Decimal("400"))
        results.append(AxisResult(
            axis=ScoreAxis.MARGIN_AND_FEES, raw_score=score,
            confidence=Decimal("0.9000"), data_sufficient=True,
            risk_flag=False,
            evidence_text=f"예상 마진율 {rate} 기반 산출",
        ))
    else:
        results.append(_candidate_score_axis(
            ScoreAxis.MARGIN_AND_FEES, candidate.margin_score,
            candidate.confidence, "margin_score",
        ))

    results.append(_supplementary_score_axis(
        ScoreAxis.PRICE_COMPETITIVENESS,
        supplementary.price_competitiveness_score,
        "price_competitiveness_score",
    ))

    results.append(_candidate_score_axis(
        ScoreAxis.DEMAND_TREND_STRENGTH,
        candidate.demand_score if candidate.demand_score is not None
        else candidate.trend_score,
        candidate.confidence, "demand_score/trend_score",
    ))

    results.append(_candidate_score_axis(
        ScoreAxis.COMPETITION_INTENSITY, candidate.competition_score,
        candidate.confidence, "competition_score",
    ))

    results.append(_supplementary_score_axis(
        ScoreAxis.SUPPLY_STABILITY, supplementary.supply_stability_score,
        "supply_stability_score",
    ))

    results.append(_supplementary_score_axis(
        ScoreAxis.INVENTORY_SHIPPING_RISK,
        supplementary.inventory_shipping_risk_score,
        "inventory_shipping_risk_score",
    ))

    results.append(_supplementary_score_axis(
        ScoreAxis.RETURN_CLAIM_RISK, supplementary.return_claim_risk_score,
        "return_claim_risk_score",
    ))

    # 정책·금지상품 위험 — risk_score(위험할수록 높음)를 반전해
    # "안전할수록 높음" 축으로 통일한다. HARD_BLOCK 축.
    results.append(_candidate_score_axis(
        ScoreAxis.POLICY_PROHIBITED_RISK, candidate.risk_score,
        candidate.confidence, "risk_score", invert=True,
    ))

    results.append(_supplementary_score_axis(
        ScoreAxis.BRAND_IP_RISK, supplementary.brand_ip_risk_score,
        "brand_ip_risk_score",
    ))

    if candidate.confidence is not None:
        conf_score = _clamp_score(_q_score(candidate.confidence) * _HUNDRED)
        results.append(AxisResult(
            axis=ScoreAxis.DATA_RELIABILITY, raw_score=conf_score,
            confidence=_q_score(candidate.confidence),
            data_sufficient=True, risk_flag=False,
            evidence_text=f"ProductCandidate.confidence={candidate.confidence} 기반 산출",
        ))
    else:
        results.append(_insufficient(
            ScoreAxis.DATA_RELIABILITY, "ProductCandidate.confidence 없음",
        ))

    if (
        supplementary.required_funding is not None
        and supplementary.available_funding is not None
    ):
        required = _q_score(supplementary.required_funding)
        available = _q_score(supplementary.available_funding)

        if required < 0 or available < 0:
            raise BadRequestException("funding 값은 음수일 수 없습니다.")

        if available == 0:
            score = _ZERO if required > 0 else _HUNDRED
        elif required <= available:
            score = _HUNDRED
        else:
            deficit_ratio = (required - available) / available
            score = _clamp_score(_HUNDRED - (deficit_ratio * _HUNDRED))

        results.append(AxisResult(
            axis=ScoreAxis.FUNDING_LIMIT_IMPACT, raw_score=score,
            confidence=Decimal("0.9000"), data_sufficient=True,
            risk_flag=(required > available),
            evidence_text=(
                f"필요자금 {required} / 가용자금 {available} 기반 산출"
            ),
        ))
    else:
        results.append(_insufficient(
            ScoreAxis.FUNDING_LIMIT_IMPACT,
            "required_funding/available_funding 미제공",
        ))

    return results


class DecisionService:

    def __init__(self, db: Session):

        self.db = db
        self.repository = DecisionRepository(db)

    # --------------------------------------------------
    # 정책 관리 (admin 전용, 전역 — Coupang과 동일한 철학)
    # --------------------------------------------------

    @staticmethod
    def _validate_axis_weights(axis_weights: dict) -> None:

        weight_sum = sum(
            (Decimal(str(w)) for w in axis_weights.values()), Decimal(0),
        )
        if set(axis_weights.keys()) != set(ScoreAxis.ALL):
            raise BadRequestException(
                "axis_weights는 12개 축을 전부 포함해야 합니다.",
            )
        if weight_sum != Decimal("1.0000").quantize(_SCORE_Q) and (
            weight_sum.quantize(_SCORE_Q) != Decimal("1.0000")
        ):
            raise BadRequestException(
                f"axis_weights 합계는 1.0000이어야 합니다(현재: {weight_sum}).",
            )

    def create_policy(
        self, data: DecisionPolicyCreateRequest,
    ) -> DecisionPolicy:

        if data.status not in DecisionPolicyStatus.ALL:
            raise BadRequestException(f"알 수 없는 정책 상태: {data.status}")

        self._validate_axis_weights(data.axis_weights)

        existing = self.repository.get_policy_by_business_id(
            data.policy_set_id,
        )
        if existing is not None:
            raise ConflictException(
                f"policy_set_id={data.policy_set_id}가 이미 존재합니다.",
            )

        policy = DecisionPolicy(
            policy_set_id=data.policy_set_id,
            policy_version=data.policy_version,
            source_reference=data.source_reference,
            status=data.status,
            is_complete=data.is_complete,
            axis_weights_json=json.dumps(
                {k: str(v) for k, v in data.axis_weights.items()},
                ensure_ascii=False,
            ),
            min_approve_total_score=_q_score(data.min_approve_total_score),
            min_confidence_for_recommendation=_q_score(
                data.min_confidence_for_recommendation,
            ),
            checked_at=data.checked_at,
            effective_at=data.effective_at,
            expires_at=data.expires_at,
        )

        try:
            policy = self.repository.add_policy_no_commit(policy)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise ConflictException(
                f"policy_set_id={data.policy_set_id}가 이미 존재합니다.",
            )
        except Exception:
            self.db.rollback()
            raise

        return policy

    def list_policies(self) -> list[DecisionPolicy]:

        return self.repository.list_policies()

    # --------------------------------------------------
    # 회사별 적용 정책 (2026-08-15 V7 Gate 2, 요구사항 1)
    # --------------------------------------------------

    def create_company_policy(
        self, data: DecisionCompanyPolicyCreateRequest, company_id: int,
    ) -> CompanyDecisionPolicy:
        """
        회사가 전역 템플릿을 커스터마이징한 자기 회사 전용 정책을
        만든다. UNIQUE(company_id, policy_set_id)라 같은 회사 안에서
        policy_set_id가 중복되면 거부된다(다른 회사는 동일한
        policy_set_id 문자열을 독립적으로 쓸 수 있다).
        """

        if data.status not in DecisionPolicyStatus.ALL:
            raise BadRequestException(f"알 수 없는 정책 상태: {data.status}")

        self._validate_axis_weights(data.axis_weights)

        if data.base_policy_id is not None:
            base = self.repository.get_policy(data.base_policy_id)
            if base is None:
                raise NotFoundException(
                    "base_policy_id에 해당하는 전역 템플릿을 찾을 수 "
                    "없습니다.",
                )

        existing = self.repository.get_company_policy_by_business_id(
            company_id, data.policy_set_id,
        )
        if existing is not None:
            raise ConflictException(
                f"policy_set_id={data.policy_set_id}가 이 회사에 이미 "
                "존재합니다.",
            )

        policy = CompanyDecisionPolicy(
            company_id=company_id,
            policy_set_id=data.policy_set_id,
            policy_version=data.policy_version,
            source_reference=data.source_reference,
            status=data.status,
            is_complete=data.is_complete,
            axis_weights_json=json.dumps(
                {k: str(v) for k, v in data.axis_weights.items()},
                ensure_ascii=False,
            ),
            min_approve_total_score=_q_score(data.min_approve_total_score),
            min_confidence_for_recommendation=_q_score(
                data.min_confidence_for_recommendation,
            ),
            checked_at=data.checked_at,
            effective_at=data.effective_at,
            expires_at=data.expires_at,
            base_policy_id=data.base_policy_id,
        )

        try:
            policy = self.repository.add_company_policy_no_commit(policy)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise ConflictException(
                f"policy_set_id={data.policy_set_id}가 이 회사에 이미 "
                "존재합니다.",
            )
        except Exception:
            self.db.rollback()
            raise

        return policy

    def list_company_policies(
        self, company_id: int,
    ) -> list[CompanyDecisionPolicy]:

        return self.repository.list_company_policies(company_id)

    @staticmethod
    def _is_policy_usable(policy, now: datetime) -> bool:
        """
        DecisionPolicy/CompanyDecisionPolicy 둘 다에 duck-typing으로
        적용된다(두 클래스가 완전히 동일한 필드 shape를 갖기 때문).
        """

        return (
            policy.status == DecisionPolicyStatus.VERIFIED
            and policy.is_active
            and policy.is_complete
            and bool(policy.policy_version)
            and bool(policy.source_reference)
            and policy.effective_at <= now
            and (policy.expires_at is None or policy.expires_at > now)
        )

    def _get_usable_policy(self) -> DecisionPolicy:

        now = datetime.utcnow()
        usable = [
            p for p in self.repository.list_active_policies()
            if self._is_policy_usable(p, now)
        ]

        if not usable:
            raise BadRequestException(
                "사용 가능한(VERIFIED·활성·완전·유효기간 내) 평가 정책이 "
                "없습니다 — 정책이 준비될 때까지 평가를 진행할 수 "
                "없습니다.",
            )

        # 가장 최근에 생성된 사용 가능한 정책을 채택한다.
        return max(usable, key=lambda p: p.id)

    def _get_usable_company_policy(
        self, company_id: int,
    ) -> CompanyDecisionPolicy | None:
        """None이면 이 회사에 사용 가능한 커스텀 정책이 없다는 뜻 —
        예외를 던지지 않는다(호출자가 전역 템플릿으로 폴백한다)."""

        now = datetime.utcnow()
        usable = [
            p for p in self.repository.list_active_company_policies(
                company_id,
            )
            if self._is_policy_usable(p, now)
        ]

        if not usable:
            return None

        return max(usable, key=lambda p: p.id)

    def _resolve_policy(
        self, company_id: int,
    ) -> tuple[object, str]:
        """
        이 회사에 사용 가능한 CompanyDecisionPolicy가 있으면 그것을
        우선 사용하고("COMPANY"), 없으면 전역 DecisionPolicy로
        폴백한다("GLOBAL" — 사용 가능한 전역 정책도 없으면
        _get_usable_policy()가 fail-closed로 차단한다).
        """

        company_policy = self._get_usable_company_policy(company_id)

        if company_policy is not None:
            return company_policy, PolicySource.COMPANY

        return self._get_usable_policy(), PolicySource.GLOBAL

    # --------------------------------------------------
    # Emergency Stop 확인 (최우선, 점수로 상쇄되지 않음)
    # --------------------------------------------------

    def _check_safety_gate(self) -> str | None:
        """
        None이면 안전(평가 진행 가능). 문자열이면 그 사유로 차단해야
        한다는 뜻이다.
        """

        inspector = inspect(self.db.get_bind())
        existing_tables = set(inspector.get_table_names())

        if "emergency_stops" not in existing_tables:
            return (
                "automation_safety 스키마가 적용되지 않아 Emergency "
                "Stop 상태를 확인할 수 없습니다 — 안전하게 평가를 "
                "차단합니다."
            )

        safety_service = SafetyService(self.db)

        if safety_service.is_emergency_stop_active():
            return "Emergency Stop이 활성화되어 있어 평가를 진행할 수 없습니다."

        return None

    # --------------------------------------------------
    # 평가 실행 (멱등, 회사 스코프)
    # --------------------------------------------------

    def evaluate_candidate(
        self,
        candidate_id: int,
        company_id: int,
        idempotency_key: str,
        supplementary_inputs: DecisionSupplementaryInputs,
    ) -> tuple[DecisionEvaluation, list[DecisionScore], bool]:

        existing = self.repository.get_evaluation_by_idempotency_key(
            company_id, idempotency_key,
        )
        if existing is not None:
            return (
                existing,
                self.repository.list_scores_for_company(
                    existing.id, company_id,
                ),
                True,
            )

        safety_block_reason = self._check_safety_gate()

        if safety_block_reason is not None:
            try:
                self.repository.add_audit_log_no_commit(
                    DecisionAuditLog(
                        company_id=company_id,
                        evaluation_id=None,
                        candidate_id=candidate_id,
                        event_type=AuditEventType.EVALUATION_BLOCKED_BY_SAFETY,
                        actor="AI",
                        payload_summary=safety_block_reason,
                    ),
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

            raise ForbiddenException(safety_block_reason)

        # Audit(2026-08-21, CTO 후속 지시) — AI Capability Registry
        # 강제. EStop(위 _check_safety_gate) 확인 이후, 실제 평가
        # 로직 이전에 연결한다 — 미등록·비활성 capability는 여기서
        # 즉시 차단된다(fail-closed). 이 호출은 실행을 승인하지
        # 않는다 — evaluate_candidate() 자체도 상태를 자동 전이하지
        # 않고 점수·근거만 반환한다(승인/보류/거절은 항상 운영자의
        # 별도 결정).
        require_active_capability(CapabilityCode.PRODUCT_SELECTION)

        policy, policy_source = self._resolve_policy(company_id)

        # 2026-08-15 V7 Gate 2 — 원래는 company_id 구분 없이 id만으로
        # ProductCandidate를 직접 조회했다. product_candidate 도메인에
        # PRIVATE(비공개) 후보가 도입된 뒤에는, 이 직접 조회가 다른
        # 회사의 비공개 후보를 candidate_id 추측만으로 경제성 평가
        # (예상매출·가용자금 등 민감 입력을 포함)할 수 있게 하는 구멍이
        # 된다 — product_candidate 자체의 조회 API(GET)는 이미
        # 가시성으로 막혀 있었지만, 이 evaluate_candidate() 경로는
        # 그 가시성 검사를 우회했다. ProductCandidateService의
        # 가시성 인지 조회로 교체해 동일한 규칙을 적용한다.
        candidate = ProductCandidateService(
            self.db,
        ).get_visible_for_company(candidate_id, company_id)

        axis_results = evaluate_axes(candidate, supplementary_inputs)

        weights = {
            k: Decimal(v)
            for k, v in json.loads(policy.axis_weights_json).items()
        }

        total_score = _ZERO
        weighted_confidence = _ZERO
        sufficient_count = 0
        hard_block_triggered = False

        for axis_result in axis_results:
            weight = weights[axis_result.axis]
            weighted_score = _clamp_score(
                axis_result.raw_score * weight,
            ).quantize(_SCORE_Q)
            total_score += weighted_score
            weighted_confidence += (axis_result.confidence * weight)

            if axis_result.data_sufficient:
                sufficient_count += 1

            if axis_result.risk_flag:
                hard_block_triggered = True

        total_score = total_score.quantize(_SCORE_Q)
        confidence = weighted_confidence.quantize(_SCORE_Q)

        recommendation, reason = self._decide_recommendation(
            total_score=total_score,
            confidence=confidence,
            sufficient_count=sufficient_count,
            hard_block_triggered=hard_block_triggered,
            policy=policy,
        )

        snapshot = {
            "candidate": {
                "trend_score": _str_or_none(candidate.trend_score),
                "novelty_score": _str_or_none(candidate.novelty_score),
                "demand_score": _str_or_none(candidate.demand_score),
                "competition_score": _str_or_none(candidate.competition_score),
                "margin_score": _str_or_none(candidate.margin_score),
                "risk_score": _str_or_none(candidate.risk_score),
                "confidence": _str_or_none(candidate.confidence),
            },
            "supplementary": json.loads(
                supplementary_inputs.model_dump_json(),
            ),
        }
        snapshot_json = json.dumps(
            snapshot, sort_keys=True, ensure_ascii=False,
        )
        input_fingerprint = hashlib.sha256(
            snapshot_json.encode("utf-8"),
        ).hexdigest()

        evaluation = DecisionEvaluation(
            company_id=company_id,
            candidate_id=candidate_id,
            policy_id=policy.id if policy_source == PolicySource.GLOBAL else None,
            company_policy_id=(
                policy.id if policy_source == PolicySource.COMPANY else None
            ),
            policy_source=policy_source,
            policy_version=policy.policy_version,
            input_snapshot_json=snapshot_json,
            input_fingerprint=input_fingerprint,
            total_score=total_score,
            confidence=confidence,
            recommendation=recommendation,
            recommendation_reason=reason,
            blocked_by_safety=False,
            safety_block_reason=None,
            status=EvaluationStatus.PENDING_REVIEW,
            idempotency_key=idempotency_key,
            created_by="AI",
            evaluator_kind=EVALUATOR_KIND,
            evaluator_version=EVALUATOR_VERSION,
        )

        try:
            evaluation = self.repository.add_evaluation_no_commit(evaluation)

            scores: list[DecisionScore] = []
            for axis_result in axis_results:
                weight = weights[axis_result.axis]
                weighted_score = _clamp_score(
                    axis_result.raw_score * weight,
                ).quantize(_SCORE_Q)

                score_row = DecisionScore(
                    company_id=company_id,
                    evaluation_id=evaluation.id,
                    axis=axis_result.axis,
                    raw_score=axis_result.raw_score,
                    weight=weight,
                    weighted_score=weighted_score,
                    confidence=axis_result.confidence,
                    data_sufficient=axis_result.data_sufficient,
                    risk_flag=axis_result.risk_flag,
                    evidence_text=axis_result.evidence_text,
                )
                scores.append(
                    self.repository.add_score_no_commit(score_row),
                )

            self.repository.add_audit_log_no_commit(
                DecisionAuditLog(
                    company_id=company_id,
                    evaluation_id=evaluation.id,
                    candidate_id=candidate_id,
                    event_type=AuditEventType.EVALUATION_CREATED,
                    actor="AI",
                    payload_summary=(
                        f"평가 생성: total_score={total_score}, "
                        f"recommendation={recommendation}"
                    ),
                ),
            )

            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_evaluation_by_idempotency_key(
                company_id, idempotency_key,
            )
            if winner is None:
                raise

            return (
                winner,
                self.repository.list_scores_for_company(
                    winner.id, company_id,
                ),
                True,
            )

        except Exception:
            self.db.rollback()
            raise

        return evaluation, scores, False

    @staticmethod
    def _decide_recommendation(
        total_score: Decimal,
        confidence: Decimal,
        sufficient_count: int,
        hard_block_triggered: bool,
        policy: DecisionPolicy,
    ) -> tuple[str, str]:

        if sufficient_count < _MIN_SUFFICIENT_AXES:
            return (
                EvaluationRecommendation.INSUFFICIENT_DATA,
                f"평가 가능한 축이 {sufficient_count}/12개뿐입니다 "
                f"(최소 {_MIN_SUFFICIENT_AXES}개 필요) — 임의의 긍정 "
                "점수를 만들지 않고 데이터 부족으로 처리합니다.",
            )

        if hard_block_triggered:
            return (
                EvaluationRecommendation.REVIEW_REQUIRED,
                "정책·금지상품 위험 축이 임계값 미만입니다 — 총점이 "
                "높아도 점수로 상쇄하지 않고 운영자 검토가 필요합니다.",
            )

        if confidence < policy.min_confidence_for_recommendation:
            return (
                EvaluationRecommendation.INSUFFICIENT_DATA,
                f"평가 신뢰도({confidence})가 정책 최소 기준"
                f"({policy.min_confidence_for_recommendation}) 미만입니다.",
            )

        if total_score >= policy.min_approve_total_score:
            return (
                EvaluationRecommendation.RECOMMEND_APPROVE,
                f"총점 {total_score}점 — 정책 기준"
                f"({policy.min_approve_total_score}점) 이상으로 승인을 "
                "추천합니다. 최종 승인은 운영자가 결정해야 합니다.",
            )

        if total_score >= Decimal("50.0000"):
            return (
                EvaluationRecommendation.RECOMMEND_HOLD,
                f"총점 {total_score}점 — 승인 기준에는 못 미치나 거절할 "
                "정도는 아니라 보류를 추천합니다.",
            )

        if total_score >= Decimal("30.0000"):
            return (
                EvaluationRecommendation.REVIEW_REQUIRED,
                f"총점 {total_score}점 — 점수가 낮아 운영자 직접 검토가 "
                "필요합니다.",
            )

        return (
            EvaluationRecommendation.RECOMMEND_REJECT,
            f"총점 {total_score}점 — 정책 기준에 크게 미달해 거절을 "
            "추천합니다. 최종 거절도 운영자가 결정해야 합니다.",
        )

    # --------------------------------------------------
    # 조회 (전부 회사 스코프)
    # --------------------------------------------------

    def get_evaluation(
        self, evaluation_id: int, company_id: int,
    ) -> DecisionEvaluation:

        evaluation = self.repository.get_evaluation_for_company(
            evaluation_id, company_id,
        )

        if evaluation is None:
            raise NotFoundException("DecisionEvaluation을 찾을 수 없습니다.")

        return evaluation

    def list_scores(
        self, evaluation_id: int, company_id: int,
    ) -> list[DecisionScore]:

        self.get_evaluation(evaluation_id, company_id)

        return self.repository.list_scores_for_company(
            evaluation_id, company_id,
        )

    def list_pending_review(
        self, company_id: int, skip: int = 0, limit: int = 100,
    ) -> list[DecisionEvaluation]:

        return self.repository.list_pending_review_for_company(
            company_id, skip=skip, limit=limit,
        )

    def list_evaluations(
        self, company_id: int, skip: int = 0, limit: int = 100,
    ) -> list[DecisionEvaluation]:

        return self.repository.list_evaluations_for_company(
            company_id, skip=skip, limit=limit,
        )

    def list_reviews(
        self, evaluation_id: int, company_id: int,
    ) -> list[DecisionReview]:

        self.get_evaluation(evaluation_id, company_id)

        return self.repository.list_reviews_for_company(
            evaluation_id, company_id,
        )

    def list_audit_log_for_candidate(
        self, candidate_id: int, company_id: int,
    ) -> list[DecisionAuditLog]:

        return self.repository.list_audit_log_for_candidate_and_company(
            candidate_id, company_id,
        )

    # --------------------------------------------------
    # 사람의 검토 (승인/보류/거절/override, 회사 스코프)
    # --------------------------------------------------

    def review_evaluation(
        self,
        evaluation_id: int,
        company_id: int,
        action: str,
        operator_id: int,
        is_admin: bool,
        idempotency_key: str,
        memo: str | None = None,
        override_reason: str | None = None,
        new_value: str | None = None,
    ) -> tuple[DecisionEvaluation, bool]:

        if not is_admin:
            raise ForbiddenException("평가 검토는 관리자만 가능합니다.")

        if action not in ReviewAction.ALL:
            raise BadRequestException(f"알 수 없는 action: {action}")

        if action == ReviewAction.OVERRIDE and (
            not override_reason or not new_value
        ):
            raise BadRequestException(
                "override는 override_reason과 new_value가 모두 필요합니다.",
            )

        existing_review = self.repository.get_review_by_idempotency_key(
            company_id, idempotency_key,
        )
        if existing_review is not None:
            return self.get_evaluation(evaluation_id, company_id), True

        evaluation = self.get_evaluation(evaluation_id, company_id)

        if evaluation.status != EvaluationStatus.PENDING_REVIEW:
            existing_review = self.repository.get_review_by_idempotency_key(
                company_id, idempotency_key,
            )
            if existing_review is not None:
                return self.get_evaluation(evaluation_id, company_id), True

            raise BadRequestException(
                "PENDING_REVIEW 상태의 평가만 검토할 수 있습니다. "
                f"(현재: {evaluation.status})",
            )

        previous_value = evaluation.recommendation if action == ReviewAction.OVERRIDE else None

        try:
            self.repository.add_review_no_commit(
                DecisionReview(
                    company_id=company_id,
                    evaluation_id=evaluation_id,
                    action=action,
                    reviewer_id=operator_id,
                    memo=memo,
                    override_reason=override_reason,
                    previous_value=previous_value,
                    new_value=new_value,
                    idempotency_key=idempotency_key,
                ),
            )

            rowcount = self.repository.update_status_conditional(
                evaluation_id,
                company_id,
                (EvaluationStatus.PENDING_REVIEW,),
                EvaluationStatus.REVIEWED,
            )

            if rowcount != 1:
                raise ConflictException(
                    "평가 상태가 동시에 변경되어 이 검토를 반영할 수 "
                    "없습니다(다른 운영자가 먼저 처리했을 수 있습니다).",
                )

            if action == ReviewAction.OVERRIDE:
                evaluation.recommendation = new_value

            self.repository.add_audit_log_no_commit(
                DecisionAuditLog(
                    company_id=company_id,
                    evaluation_id=evaluation_id,
                    candidate_id=evaluation.candidate_id,
                    event_type=(
                        AuditEventType.OVERRIDE_APPLIED
                        if action == ReviewAction.OVERRIDE
                        else AuditEventType.REVIEW_DECIDED
                    ),
                    actor=str(operator_id),
                    payload_summary=(
                        f"action={action}, previous={previous_value}, "
                        f"new={new_value}"
                        if action == ReviewAction.OVERRIDE
                        else f"action={action}"
                    ),
                ),
            )

            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            winner = self.repository.get_review_by_idempotency_key(
                company_id, idempotency_key,
            )
            if winner is None:
                raise

            return self.get_evaluation(evaluation_id, company_id), True

        except Exception:
            self.db.rollback()
            raise

        return self.get_evaluation(evaluation_id, company_id), False


def _str_or_none(value) -> str | None:

    return str(value) if value is not None else None


__all__ = [
    "DecisionService",
    "evaluate_axes",
    "AxisResult",
    "EVALUATOR_KIND",
    "EVALUATOR_VERSION",
]
