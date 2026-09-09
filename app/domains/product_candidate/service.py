"""
=========================================================
Homez OS

File : app/domains/product_candidate/service.py

V3 ProductCandidate Service

수집 원본 → 근거 → AI 판단 → 운영자 결정을 계약과 Event로 연결한다.
Funding/Order/Purchase Model을 import하지 않는다. 실제 상품 등록·구매·
주문을 실행하지 않는다 — 후보 상태 전이와 Event 생성만 수행한다.
운영자 승인 없이 APPROVED로 자동 전환하지 않는다.

Hardening(감사 대응):
  - Repository는 commit하지 않는다(no_commit/flush) — discover, AI 분석
    반영, recommend, approve/hold/reject 각각이 이 서비스에서 정확히
    commit 1회로 끝나고, 어떤 예외에서도 rollback된다.
  - 운영자 결정(approve/hold/reject)과 recommend는 "현재 status가
    기대값일 때만" 조건부 UPDATE + rowcount 검증으로 반영한다 — 동시에
    두 결정이 들어와도 하나만 성공한다.
  - candidate_key UNIQUE 제약 위반(동시 discover 경쟁)은 IntegrityError를
    잡아 rollback 후 승자를 재조회해 반환한다(자동 병합 없이 그대로).

2026-08-14 테넌트 격리 감사(Gate R13) — 승인 상태 회사별 분리:
  - approve/hold/reject는 이제 company_id를 필수로 받는다.
    ProductCandidate.status(전역)는 더 이상 이 결정으로 바뀌지 않는다
    — 대신 ProductCandidateSelection(candidate_id, company_id) 1행이
    이 회사의 "현재 판단"을 담는다. ProductCandidateDecision(append-only
    이력)에도 company_id를 함께 남긴다.
  - get_effective_status_for_company()가 "회사 X 관점의 candidate Y
    현재 상태"를 파생한다 — Selection 행이 있으면 그 status, 없으면
    ProductCandidate.status(아직 그 회사가 결정하지 않음) 그대로.
  - require_approved_for_company()는 coupang/marketplace_listing/
    listing_package 등 "APPROVED 상태여야 진행 가능" 게이트를 쓰는
    다른 Domain이 공유하는 단일 진입점이다 — 각 Domain이 개별적으로
    ProductCandidate.status를 직접 비교하던 옛 패턴(회사 무관 전역
    비교라 다른 회사의 승인에 무임승차 가능했던 결함)을 대체한다.
=========================================================
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.domains.product_candidate.constants import CandidateStatus
from app.domains.product_candidate.constants import CandidateVisibility
from app.domains.product_candidate.constants import DecisionAction
from app.domains.product_candidate.constants import EvidenceType
from app.domains.product_candidate.events import EventType
from app.domains.product_candidate.events import build_event
from app.domains.product_candidate.model import ProductCandidate
from app.domains.product_candidate.model import ProductCandidateDecision
from app.domains.product_candidate.model import ProductCandidateEvidence
from app.domains.product_candidate.model import ProductCandidateSelection
from app.domains.product_candidate.repository import ProductCandidateRepository
from app.domains.product_candidate.schema import ProductCandidateDiscover
from app.domains.product_candidate.schema import ProductCandidatePrivateCreate
from app.domains.notification_center.operational_events import (
    dispatch_operational_event,
)


class ProductCandidateService:

    def __init__(
        self,
        db: Session,
    ):

        self.db = db
        self.repository = ProductCandidateRepository(db)

    # --------------------------------------------------
    # 키/조회
    # --------------------------------------------------

    @staticmethod
    def build_candidate_key(
        source_type: str,
        market: str,
        source_reference: str,
    ) -> str:

        return f"{source_type}:{market}:{source_reference}"

    @staticmethod
    def build_private_candidate_key(
        company_id: int,
        source_type: str,
        market: str,
        source_reference: str,
    ) -> str:
        """
        비공개(PRIVATE) 후보 전용 candidate_key(2026-08-15 V7 Gate 2).

        candidate_key UNIQUE는 여전히 전역(테이블) 범위다 — 회사별
        네임스페이스를 이 key 문자열 자체에 접두사로 넣어(예:
        "PRIVATE:1:MANUAL:COUPANG:my-sku") 서로 다른 회사가 동일한
        source_reference로 각자 비공개 후보를 등록해도 충돌하지
        않게 한다. GLOBAL 발견 카탈로그(build_candidate_key)의 key
        공간과도 겹치지 않는다("PRIVATE:" 접두사가 GLOBAL 쪽
        source_type에는 절대 나타나지 않는 예약 접두사이기 때문).
        """

        return f"PRIVATE:{company_id}:{source_type}:{market}:{source_reference}"

    def get(
        self,
        candidate_id: int,
    ) -> ProductCandidate:
        """
        가시성 검사 없는 전역 조회 — 호출자가 candidate_id의 출처를
        스스로 신뢰할 수 있는 극히 제한된 내부 용도로만 남겨둔다
        (2026-08-19 CTO 보완 지시로 apply_trend_analysis/
        apply_new_product_analysis도 company_id를 받아
        get_visible_for_company()를 거치도록 바뀌어, 이 메서드의
        실제 내부 호출부는 현재 없다). 사용자가 회사 컨텍스트로
        요청한 조회(승인/거절/근거/이력/분석 반영 등)는 반드시
        get_visible_for_company()를 써야 한다 — PRIVATE 후보를
        candidate_id 추측만으로 열람할 수 있는 경로가 되지 않도록.
        """

        candidate = self.repository.get(candidate_id)

        if candidate is None:
            raise NotFoundException("ProductCandidate를 찾을 수 없습니다.")

        return candidate

    def get_visible_for_company(
        self,
        candidate_id: int,
        company_id: int,
    ) -> ProductCandidate:
        """
        이 회사가 볼 수 있는 후보(GLOBAL 전체 + 자사 PRIVATE)만 반환
        한다(2026-08-15 V7 Gate 2). 다른 회사의 PRIVATE 후보는
        candidate_id를 알아도 404다 — 존재 자체를 노출하지 않는다.
        """

        candidate = self.repository.get_visible_for_company(
            candidate_id, company_id,
        )

        if candidate is None:
            raise NotFoundException("ProductCandidate를 찾을 수 없습니다.")

        return candidate

    def list_candidates(
        self,
        status: str | None = None,
        market: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ProductCandidate]:
        """가시성 검사 없는 전역 목록 — 내부/시스템 전용(위 get() 참고)."""

        return self.repository.list_candidates(
            status=status, market=market, skip=skip, limit=limit,
        )

    def list_candidates_for_company(
        self,
        company_id: int,
        status: str | None = None,
        market: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ProductCandidate]:
        """이 회사가 볼 수 있는 후보(GLOBAL 전체 + 자사 PRIVATE)만
        목록 조회한다(2026-08-15 V7 Gate 2)."""

        return self.repository.list_candidates_for_company(
            company_id, status=status, market=market, skip=skip, limit=limit,
        )

    # --------------------------------------------------
    # 발견 (멱등 + 동시 생성 경쟁 복구)
    # --------------------------------------------------

    def discover(
        self,
        data: ProductCandidateDiscover,
        correlation_id: str,
    ) -> tuple[ProductCandidate, list]:
        """
        동일 (source_type, market, source_reference) 조합은 candidate_key로
        중복 방지된다. 이미 존재하면 기존 후보를 그대로 반환하고(자동 병합·
        덮어쓰기 없음) 신규 RAW_SOURCE 근거만 추가로 append한다.

        동시에 같은 candidate_key로 최초 생성을 시도하는 경쟁이 발생하면
        (두 요청 모두 사전 조회에서는 "없음"으로 보임) UNIQUE 제약
        위반(IntegrityError)이 발생하고, 이 경우 rollback 후 실제로
        만들어진 승자 행을 재조회해 그대로 반환한다(병합하지 않음).
        """

        key = self.build_candidate_key(
            data.source_type, data.market, data.source_reference,
        )

        existing = self.repository.get_by_candidate_key(key)

        if existing is not None:
            try:
                self.repository.add_evidence_no_commit(
                    ProductCandidateEvidence(
                        candidate_id=existing.id,
                        evidence_type=EvidenceType.RAW_SOURCE,
                        payload_summary=(
                            f"재수집: product_name={data.product_name}"
                        ),
                    ),
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

            return existing, []

        candidate = ProductCandidate(
            candidate_key=key,
            source_type=data.source_type,
            source_reference=data.source_reference,
            market=data.market,
            product_name=data.product_name,
            category_hint=data.category_hint,
            brand_hint=data.brand_hint,
            release_date=data.release_date,
            status=CandidateStatus.DISCOVERED,
        )

        try:
            candidate = self.repository.add_no_commit(candidate)

            self.repository.add_evidence_no_commit(
                ProductCandidateEvidence(
                    candidate_id=candidate.id,
                    evidence_type=EvidenceType.RAW_SOURCE,
                    payload_summary=(
                        f"최초 발견: product_name={data.product_name}"
                    ),
                ),
            )

            events = [
                build_event(
                    EventType.PRODUCT_SOURCE_DISCOVERED,
                    candidate.id,
                    source=data.source_type,
                    correlation_id=correlation_id,
                ),
                build_event(
                    EventType.PRODUCT_CANDIDATE_CREATED,
                    candidate.id,
                    source="product_candidate",
                    correlation_id=correlation_id,
                ),
            ]

            self.db.commit()

            return candidate, events

        except IntegrityError:

            self.db.rollback()

            winner = self.repository.get_by_candidate_key(key)

            if winner is None:
                # 승자를 찾을 수 없으면(이례적) 원래 예외를 숨기지 않는다.
                raise

            try:
                self.repository.add_evidence_no_commit(
                    ProductCandidateEvidence(
                        candidate_id=winner.id,
                        evidence_type=EvidenceType.RAW_SOURCE,
                        payload_summary=(
                            "재수집(동시 생성 경쟁 패자): "
                            f"product_name={data.product_name}"
                        ),
                    ),
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

            return winner, []

        except Exception:
            self.db.rollback()
            raise

    # --------------------------------------------------
    # 비공개 후보 등록 (2026-08-15 V7 Gate 2, 요구사항 2 — 신규 기능)
    # --------------------------------------------------

    def register_private_candidate(
        self,
        data: ProductCandidatePrivateCreate,
        company_id: int,
        correlation_id: str,
    ) -> tuple[ProductCandidate, list]:
        """
        회사가 직접 등록하는 비공개(PRIVATE) 후보 — 다른 회사에게는
        존재 자체가 노출되지 않는다. discover()와 동일한 멱등/동시
        생성 경쟁 복구 패턴을 따르되, candidate_key를 회사별로
        네임스페이스 분리된 build_private_candidate_key()로 만든다.
        """

        source_type = data.source_type or "PRIVATE_MANUAL"
        key = self.build_private_candidate_key(
            company_id, source_type, data.market, data.source_reference,
        )

        existing = self.repository.get_by_candidate_key(key)

        if existing is not None:
            try:
                self.repository.add_evidence_no_commit(
                    ProductCandidateEvidence(
                        candidate_id=existing.id,
                        evidence_type=EvidenceType.RAW_SOURCE,
                        payload_summary=(
                            f"재등록: product_name={data.product_name}"
                        ),
                    ),
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

            return existing, []

        candidate = ProductCandidate(
            candidate_key=key,
            source_type=source_type,
            source_reference=data.source_reference,
            market=data.market,
            product_name=data.product_name,
            category_hint=data.category_hint,
            brand_hint=data.brand_hint,
            release_date=data.release_date,
            status=CandidateStatus.DISCOVERED,
            visibility=CandidateVisibility.PRIVATE,
            owner_company_id=company_id,
        )

        try:
            candidate = self.repository.add_no_commit(candidate)

            self.repository.add_evidence_no_commit(
                ProductCandidateEvidence(
                    candidate_id=candidate.id,
                    evidence_type=EvidenceType.RAW_SOURCE,
                    payload_summary=(
                        f"비공개 등록: product_name={data.product_name}"
                    ),
                ),
            )

            events = [
                build_event(
                    EventType.PRODUCT_SOURCE_DISCOVERED,
                    candidate.id,
                    source=source_type,
                    correlation_id=correlation_id,
                ),
                build_event(
                    EventType.PRODUCT_CANDIDATE_CREATED,
                    candidate.id,
                    source="product_candidate",
                    correlation_id=correlation_id,
                ),
            ]

            self.db.commit()

            return candidate, events

        except IntegrityError:

            self.db.rollback()

            winner = self.repository.get_by_candidate_key(key)

            if winner is None:
                raise

            try:
                self.repository.add_evidence_no_commit(
                    ProductCandidateEvidence(
                        candidate_id=winner.id,
                        evidence_type=EvidenceType.RAW_SOURCE,
                        payload_summary=(
                            "재등록(동시 생성 경쟁 패자): "
                            f"product_name={data.product_name}"
                        ),
                    ),
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

            return winner, []

        except Exception:
            self.db.rollback()
            raise

    # --------------------------------------------------
    # AI 분석 결과 반영 (Trend AI / New Product AI가 각각 독립적으로 호출)
    # --------------------------------------------------

    def apply_trend_analysis(
        self,
        candidate_id: int,
        company_id: int,
        trend_score: float,
        confidence: float,
        evidence_text: str,
        correlation_id: str,
    ) -> list:
        """
        [2026-08-19 CTO 보완 지시, "잔존 위험" 항목 대응] 이전 버전은
        self.get(candidate_id)(가시성 검사 없음)를 썼다 — router가
        없어 현재는 도달 불가능하지만, 미래에 이 메서드를 호출하는
        경로가 생기면 다른 회사 PRIVATE 후보를 candidate_id 추측만
        으로 건드릴 수 있는 잠재적 IDOR였다. company_id를 필수로 받아
        get_visible_for_company()를 거치도록 고쳤다 — recommend()/
        verify_private_candidate_info()와 동일한 패턴이다.

        처음에는 이 메서드가 GLOBAL 카탈로그 전용(시스템 파이프라인이라
        회사 컨텍스트가 없음)이라고 가정하고 GLOBAL만 허용하는 안을
        시도했으나, 기존 테스트(test_product_candidate_private_
        visibility.py::test_owner_company_can_approve_private_
        candidate)가 PRIVATE 후보에도 실제 AI 트렌드 분석이 적용되는
        흐름을 이미 검증하고 있어 그 가정이 틀렸음을 확인했다 — PRIVATE
        후보도 이 파이프라인을 거칠 수 있다는 것이 기존의 실제 계약
        이다. 향후 실제 배경 Job이 이 메서드를 호출할 때는, GLOBAL
        발견이면 그 Job이 알고 있는 임의 company_id로(get_visible_for_
        company()가 GLOBAL은 항상 통과시킴), PRIVATE 후보 재분석이면
        그 후보의 소유 회사 id로 호출해야 한다.
        """

        candidate = self.get_visible_for_company(candidate_id, company_id)

        try:
            self.repository.add_evidence_no_commit(
                ProductCandidateEvidence(
                    candidate_id=candidate_id,
                    evidence_type=EvidenceType.TREND_ANALYSIS,
                    payload_summary=evidence_text,
                    score=trend_score,
                    confidence=confidence,
                ),
            )

            candidate.trend_score = trend_score
            self._advance_to_analyzed(candidate)
            self.repository.save_no_commit(candidate)

            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        return [
            build_event(
                EventType.TREND_ANALYSIS_COMPLETED,
                candidate_id,
                source="trend_discovery",
                correlation_id=correlation_id,
                payload={"trend_score": trend_score},
            ),
        ]

    def apply_new_product_analysis(
        self,
        candidate_id: int,
        company_id: int,
        is_new_product: bool,
        novelty_score: float,
        confidence: float,
        evidence_text: str,
        correlation_id: str,
    ) -> list:
        """
        [2026-08-19 CTO 보완 지시, "잔존 위험" 항목 대응]
        apply_trend_analysis()와 동일한 이유로 company_id를 필수로
        받아 get_visible_for_company()를 거친다 — 상세 근거는
        apply_trend_analysis() docstring 참고.
        """

        candidate = self.get_visible_for_company(candidate_id, company_id)

        try:
            self.repository.add_evidence_no_commit(
                ProductCandidateEvidence(
                    candidate_id=candidate_id,
                    evidence_type=EvidenceType.NEW_PRODUCT_ANALYSIS,
                    payload_summary=evidence_text,
                    score=novelty_score,
                    confidence=confidence,
                ),
            )

            candidate.is_new_product = is_new_product
            candidate.novelty_score = novelty_score
            self._advance_to_analyzed(candidate)
            self.repository.save_no_commit(candidate)

            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        return [
            build_event(
                EventType.NEW_PRODUCT_ANALYSIS_COMPLETED,
                candidate_id,
                source="new_product_discovery",
                correlation_id=correlation_id,
                payload={
                    "is_new_product": is_new_product,
                    "novelty_score": novelty_score,
                },
            ),
        ]

    @staticmethod
    def _advance_to_analyzed(candidate: ProductCandidate) -> None:

        if candidate.status == CandidateStatus.DISCOVERED:
            candidate.status = CandidateStatus.ANALYZED

    # --------------------------------------------------
    # 수동(PRIVATE_MANUAL) 후보 기본 정보 확인 — 회사 소유자 본인이
    # 직접 트리거하는 유일한 사용자용 API(2026-08-19, V7 워크플로우
    # 완성 / 2026-08-19 CTO 보완 지시로 추측성 점수 제거)
    # --------------------------------------------------

    def verify_private_candidate_info(
        self,
        candidate_id: int,
        company_id: int,
        correlation_id: str,
        actor_user_id: int | None = None,
    ) -> tuple[ProductCandidate, list]:
        """
        직접 등록(PRIVATE_MANUAL)한 후보를 DISCOVERED → ANALYZED로
        전환하는 유일한 사용자용 API다. new_product_discovery의 내부
        파이프라인(apply_new_product_analysis)은 실제 시장 스캔 Job이
        candidate_id를 이미 알고 호출하는 시스템 전용 경로라 수동
        후보에는 연결되어 있지 않다.

        [2026-08-19 CTO 보완 지시] 이전 버전은 여기서 novelty_score=
        0.4+필드수*0.2, confidence=0.3을 계산해 candidate.novelty_score/
        confidence에 저장했다. 이 값은 어떤 공식 데이터·검증된 모델·
        확정된 제품 규칙에서도 나오지 않은, 입력 필드 개수를 그대로
        숫자로 바꾼 값이었다 — "실제 분석이 없다"는 evidence 문구로도
        정당화되지 않는 추측성 점수였다(model.py의 score 필드 docstring:
        "AI 산출 객관적 점수... 회사 주관적 판단이 아니라 원본 카탈로그
        데이터"). 저장소 전체를 감사한 결과 PRIVATE_MANUAL 후보를 위한
        기존 검증 규칙(Option A)은 존재하지 않았다 — 따라서 CTO 지시의
        Option B를 그대로 적용한다: 점수를 만들어내지 않고, "기본 정보가
        등록되어 있음을 확인했다"는 사실 자체만 근거로 남긴다.
        novelty_score/confidence는 Model에서 이미 nullable이므로(Migration
        불필요) 이 경로에서는 절대 쓰지 않고 None으로 남긴다.

        [정책 A 채택, 2026-08-19 CTO 정책 결정 — 앞선 보고의
        DECISION_REQUIRED 해소됨] ANALYZED라는 상태명이 곧 "AI 분석
        완료"를 뜻한다는 과거 가정을 폐기했다. 이제 ANALYZED는
        "시스템 검토 단계를 완료해 관리자의 판단이 가능한 상태"로
        공식 정의된다(constants.py::CandidateStatus docstring이 단일
        진실 공급원). 이 메서드가 실제 AI 분석 없이 DISCOVERED→
        ANALYZED로 전이시키는 것은 더 이상 상태명과의 불일치가
        아니다 — "기본 정보 확인"도 "시스템 검토"의 한 형태로
        명시적으로 인정된다. DB 상태값·컬럼·Migration은 이 정책
        결정으로 전혀 바뀌지 않는다(이름 그대로, 의미만 문서로 고정).
        어떤 근거로 ANALYZED가 됐는지는 evidence_type(MANUAL_INFO_CHECK
        vs NEW_PRODUCT_ANALYSIS/TREND_ANALYSIS)과 trend_score/
        novelty_score 존재 여부로 구분되며, recommend()는 실제 점수가
        없으면 API 레벨에서 차단한다(recommend() docstring 참고).
        """

        candidate = self.get_visible_for_company(candidate_id, company_id)

        if candidate.status != CandidateStatus.DISCOVERED:
            raise BadRequestException(
                "DISCOVERED 상태의 후보만 정보 확인을 진행할 수 "
                f"있습니다. (현재: {candidate.status})",
            )

        evidence_text = (
            "수동 등록 후보 — 사용자가 입력한 기본 정보(상품명/출처/"
            "마켓 등)가 등록되어 있음을 확인했습니다. 시장 분석이나 "
            "외부 AI 판단은 실행되지 않았으며, 이 후보에는 추천 점수가 "
            "없습니다. 운영자가 상품 정보를 직접 검토한 뒤 승인/보류/"
            "거절을 결정해야 합니다."
        )

        try:
            self.repository.add_evidence_no_commit(
                ProductCandidateEvidence(
                    candidate_id=candidate_id,
                    evidence_type=EvidenceType.MANUAL_INFO_CHECK,
                    payload_summary=evidence_text,
                    score=None,
                    confidence=None,
                ),
            )

            rowcount = self.repository.update_status_conditional(
                candidate_id,
                (CandidateStatus.DISCOVERED,),
                CandidateStatus.ANALYZED,
            )

            if rowcount != 1:
                raise ConflictException(
                    "후보 상태가 동시에 변경되어 정보 확인을 진행할 "
                    "수 없습니다(다른 운영자가 먼저 처리했을 수 "
                    "있습니다).",
                )

            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        candidate = self.repository.get(candidate_id)

        dispatch_operational_event(
            self.db, "CANDIDATE_REVIEW_NEEDED",
            company_id=company_id, user_id=actor_user_id,
            idempotency_key=f"candidate-review:{candidate_id}:ANALYZED",
            title="상품 후보 검토가 필요합니다",
            message=f"상품 후보 #{candidate_id}의 기본정보 확인이 끝났습니다.",
            link_path="candidates", entity_ref=f"product_candidate:{candidate_id}",
            reason="운영자 승인·보류·거절 결정이 필요합니다.",
            entity_summary=f"상품 후보 #{candidate_id}",
        )

        return candidate, [
            build_event(
                EventType.PRODUCT_CANDIDATE_INFO_VERIFIED,
                candidate_id,
                source="product_candidate_manual_info_check",
                correlation_id=correlation_id,
                payload={"score_computed": False},
            ),
        ]

    # --------------------------------------------------
    # 추천 (시스템, 승인은 아님) — 조건부 UPDATE
    # --------------------------------------------------

    def recommend(
        self,
        candidate_id: int,
        company_id: int,
        correlation_id: str,
        actor_user_id: int | None = None,
    ) -> tuple[ProductCandidate, list]:
        """
        [2026-08-19 CTO 보완 지시] 이전 버전은 self.get(candidate_id)
        (가시성 검사 없는 전역 조회)를 써서, 다른 회사의 PRIVATE 후보의
        candidate_id를 추측하면 Service 레벨에서 아무 방어 없이 상태를
        조회·전이시킬 수 있었다(호출자인 Router가 매번 사전에
        get_visible_for_company()를 별도 호출해야만 막히는 구조 —
        "Router 사전검사만으로 회사 격리를 보장하지 않는다"는 지적
        그대로). company_id를 필수 인자로 받아 get_visible_for_company()
        를 이 메서드 안에서 직접 거치도록 고쳤다 — 다른 회사 PRIVATE
        후보는 이제 Service 자체가 NotFoundException(404)을 던진다.
        GLOBAL 후보는 어느 company_id로 호출해도 그대로 통과한다
        (get_visible_for_company()의 기존 계약과 동일 — 실사용 호출부
        (router.py)는 여전히 방어적 중복으로 사전 검사를 유지한다).

        [정책 A, 2026-08-19] ANALYZED는 "시스템 검토 완료"만 뜻하고
        AI 분석을 보장하지 않는다(constants.py::CandidateStatus
        docstring 참고) — 그래서 ANALYZED라는 상태만으로 recommend()를
        허용하면, MANUAL_INFO_CHECK만 거친(점수 없는) 후보도 아무
        근거 없이 "시스템이 추천함"으로 전환될 수 있다. 이는 UI에서
        버튼을 숨기는 것과 별개로 API 자체가 반드시 차단해야 하는
        계약이므로, 실제 AI 산출 점수(trend_score 또는 novelty_score)
        가 있을 때만 통과시킨다.
        """

        candidate = self.get_visible_for_company(candidate_id, company_id)

        if candidate.status != CandidateStatus.ANALYZED:
            raise BadRequestException(
                "ANALYZED 상태의 후보만 추천으로 전환할 수 있습니다. "
                f"(현재: {candidate.status})",
            )

        if candidate.trend_score is None and candidate.novelty_score is None:
            raise BadRequestException(
                "실제 분석 점수(trend_score/novelty_score)가 없는 "
                "후보는 추천으로 전환할 수 없습니다 — 이 후보는 "
                "기본 정보 확인만 거쳤을 수 있습니다. 운영자가 직접 "
                "승인/보류/거절을 결정해야 합니다.",
            )

        try:
            rowcount = self.repository.update_status_conditional(
                candidate_id,
                (CandidateStatus.ANALYZED,),
                CandidateStatus.RECOMMENDED,
            )

            if rowcount != 1:
                raise ConflictException(
                    "후보 상태가 동시에 변경되어 추천으로 전환할 수 "
                    "없습니다.",
                )

            self.db.commit()

        except Exception:
            self.db.rollback()
            raise

        candidate = self.repository.get(candidate_id)

        dispatch_operational_event(
            self.db, "CANDIDATE_REVIEW_NEEDED",
            company_id=company_id, user_id=actor_user_id,
            idempotency_key=f"candidate-review:{candidate_id}:RECOMMENDED",
            title="추천 상품 후보 검토가 필요합니다",
            message=f"상품 후보 #{candidate_id}가 추천 상태로 전환됐습니다.",
            link_path="candidates", entity_ref=f"product_candidate:{candidate_id}",
            reason="운영자 최종 결정이 필요합니다.",
            entity_summary=f"상품 후보 #{candidate_id}",
        )

        events = [
            build_event(
                EventType.PRODUCT_CANDIDATE_RECOMMENDED,
                candidate_id,
                source="product_candidate",
                correlation_id=correlation_id,
            ),
        ]

        return candidate, events

    # --------------------------------------------------
    # 회사별 현재 상태 파생 (2026-08-14 테넌트 격리 감사)
    # --------------------------------------------------

    def get_effective_status_for_company(
        self,
        candidate_id: int,
        company_id: int,
    ) -> str:
        """
        "회사 X 관점에서 candidate Y의 현재 상태". Selection 행이 있으면
        그 status(APPROVED/HELD/REJECTED), 없으면 아직 그 회사가
        결정하지 않은 것이므로 ProductCandidate.status(전역 워크플로우
        상태) 그대로 반환한다.
        """

        candidate = self.get_visible_for_company(candidate_id, company_id)

        selection = self.repository.get_selection(candidate_id, company_id)

        if selection is not None:
            return selection.status

        return candidate.status

    def require_approved_for_company(
        self,
        candidate_id: int,
        company_id: int,
    ) -> ProductCandidate:
        """
        coupang/marketplace_listing/listing_package 등 "이 회사가 이미
        승인한 후보"만 다음 단계로 넘길 수 있는 Domain이 공유하는 단일
        게이트. ProductCandidate가 없으면 404, 이 회사의 파생 상태가
        APPROVED가 아니면 400을 던진다. 반환값은 원본 ProductCandidate
        (상품명 등 전역 필드를 그대로 쓰기 위함) — 회사별 상태 판단은
        이미 이 메서드 안에서 끝났다.

        get_effective_status_for_company()의 fallback(Selection 행이
        없으면 candidate.status를 그대로 보여줌)을 그대로 재사용한다.
        실제 운영 경로에서는 이 fallback이 절대 "APPROVED"를 반환하지
        않는다 — approve()/reject()가 이제 오직 ProductCandidateSelection
        에만 쓰기 때문에, 서비스 코드 경로로는 ProductCandidate.status
        (전역)가 구조적으로 DISCOVERED/ANALYZED/RECOMMENDED/EXPIRED
        중 하나 이상으로 벗어날 수 없다. 이 fallback이 "APPROVED"를
        보게 되는 유일한 경로는 테스트 코드가 직접 raw ORM으로
        candidate.status="APPROVED"를 주입하는 경우뿐이다(마켓플레이스
        리스팅 등 이 감사 범위 밖 Domain의 기존 테스트 fixture 다수가
        이 방식에 의존한다 — 전부 다시 쓰는 것은 이번 Whitelist 밖).
        """

        candidate = self.get_visible_for_company(candidate_id, company_id)
        effective_status = self.get_effective_status_for_company(
            candidate_id, company_id,
        )

        if effective_status != CandidateStatus.APPROVED:
            raise BadRequestException(
                "ProductCandidate가 이 회사 관점에서 APPROVED 상태여야 "
                f"진행할 수 있습니다. (현재: {effective_status})",
            )

        return candidate

    # --------------------------------------------------
    # 운영자 결정 (승인 없이는 APPROVED로 가지 않는다) — 조건부 UPDATE
    # --------------------------------------------------

    def approve(
        self,
        candidate_id: int,
        company_id: int,
        operator_id: int,
        is_admin: bool,
        memo: str | None,
        correlation_id: str,
    ) -> tuple[str, list]:

        return self._decide(
            candidate_id, company_id,
            DecisionAction.APPROVE, CandidateStatus.APPROVED,
            operator_id, is_admin, memo, correlation_id,
            EventType.PRODUCT_CANDIDATE_APPROVED,
        )

    def hold(
        self,
        candidate_id: int,
        company_id: int,
        operator_id: int,
        is_admin: bool,
        memo: str | None,
        correlation_id: str,
    ) -> tuple[str, list]:

        return self._decide(
            candidate_id, company_id,
            DecisionAction.HOLD, CandidateStatus.HELD,
            operator_id, is_admin, memo, correlation_id,
            event_type=None,
        )

    def reject(
        self,
        candidate_id: int,
        company_id: int,
        operator_id: int,
        is_admin: bool,
        memo: str | None,
        correlation_id: str,
    ) -> tuple[str, list]:

        return self._decide(
            candidate_id, company_id,
            DecisionAction.REJECT, CandidateStatus.REJECTED,
            operator_id, is_admin, memo, correlation_id,
            EventType.PRODUCT_CANDIDATE_REJECTED,
        )

    def _decide(
        self,
        candidate_id: int,
        company_id: int,
        action: str,
        new_status: str,
        operator_id: int,
        is_admin: bool,
        memo: str | None,
        correlation_id: str,
        event_type: str | None,
    ) -> tuple[str, list]:
        """
        회사별 승인/보류/거절(Gate R13, Option B).

        ProductCandidate.status(전역)는 더 이상 이 메서드로 바뀌지
        않는다 — ProductCandidateSelection(candidate_id, company_id)
        UNIQUE 행 1개가 "이 회사의 현재 판단"이다. 동시성 가드는 두
        경로로 나뉜다:
          - 이 회사의 최초 결정: UNIQUE(candidate_id, company_id)
            제약(IntegrityError)이 "동시에 두 명이 최초 결정" 경쟁의
            유일한 승자를 가른다.
          - 이 회사가 이미 HOLD로 결정한 뒤 재결정: 조건부 UPDATE(WHERE
            status='HELD')의 rowcount가 판정 근거다.
        """

        if not is_admin:
            raise ForbiddenException(
                "ProductCandidate 승인/보류/거절은 관리자만 가능합니다.",
            )

        # 존재 + 가시성 확인(2026-08-15 V7 Gate 2) — 다른 회사의
        # PRIVATE 후보는 candidate_id를 추측해도 여기서 404가 된다.
        candidate = self.get_visible_for_company(candidate_id, company_id)

        existing_selection = self.repository.get_selection(
            candidate_id, company_id,
        )

        try:
            self.repository.add_decision_no_commit(
                ProductCandidateDecision(
                    candidate_id=candidate_id,
                    company_id=company_id,
                    action=action,
                    operator_id=operator_id,
                    memo=memo,
                ),
            )

            if existing_selection is None:
                # 이 회사의 최초 결정 — 전역 워크플로우가 아직
                # 결정 가능한 상태(ANALYZED/RECOMMENDED)여야 한다.
                if candidate.status not in CandidateStatus.DECIDABLE:
                    raise BadRequestException(
                        "이 상태에서는 운영자 결정을 내릴 수 없습니다. "
                        f"(후보 상태: {candidate.status})",
                    )

                self.repository.add_selection_no_commit(
                    ProductCandidateSelection(
                        candidate_id=candidate_id,
                        company_id=company_id,
                        status=new_status,
                        memo=memo,
                    ),
                )

            else:
                # 사전 확인(비-경쟁, 순차적 상태 위반) — 이미 이 회사가
                # 최종 결정(APPROVED/REJECTED)을 내렸다면 재결정 자체가
                # 업무 규칙 위반이다(경쟁 상황이 아니므로 BadRequest).
                if (
                    existing_selection.status
                    not in CandidateStatus.SELECTION_REDECIDABLE
                ):
                    raise BadRequestException(
                        "이 상태에서는 운영자 결정을 내릴 수 없습니다. "
                        f"(현재: {existing_selection.status})",
                    )

                rowcount = (
                    self.repository.update_selection_status_conditional(
                        candidate_id,
                        company_id,
                        CandidateStatus.SELECTION_REDECIDABLE,
                        new_status,
                        memo=memo,
                    )
                )

                if rowcount != 1:
                    # 위 사전 확인을 통과한 뒤에만 도달한다 — 즉 이
                    # 시점에는 진짜 동시 변경(레이스)만 남는다.
                    raise ConflictException(
                        "이 회사의 결정이 동시에 변경되어 반영할 "
                        "수 없습니다(다른 운영자가 먼저 처리했을 "
                        "수 있습니다).",
                    )

            self.db.commit()

        except IntegrityError:
            self.db.rollback()

            # 이 회사의 최초 결정끼리 경쟁(UNIQUE(candidate_id,
            # company_id) 위반) — 패자는 결과를 병합하지 않고 충돌로
            # 처리한다.
            raise ConflictException(
                "이 회사의 결정이 동시에 처리되어 반영할 수 없습니다"
                "(다른 운영자가 먼저 처리했을 수 있습니다).",
            )

        except OperationalError as e:
            self.db.rollback()

            # Gate AI-F3(2026-08-22) 플레이키 회귀 조사 — 실제로
            # 재현·확인한 결함: 두 운영자가 동시에 이 회사의 최초 결정을
            # 내릴 때(위 IntegrityError 경로와 같은 경쟁) SQLite가 잠금
            # 경합을 UNIQUE 위반이 아니라 "database is locked"
            # OperationalError로 표면화하는 경우가 있었다(같은 프로세스
            # 안에서 다른 스레드 기반 동시성 테스트들이 동시에 SQLite
            # 파일을 열고 닫으며 발생시키는 OS 디스크 I/O 경합 하에서
            # 재현됨 — 단독 실행 시에는 0/90 재현). 이전에는 이 예외가
            # 그대로 새어나가 호출자가 ConflictException을 받지 못했다
            # (같은 동시 결정 경쟁 상황인데 예외 타입만 달랐던 것 —
            # IntegrityError 처리와 동일한 원칙으로 정정한다). 잠금 관련
            # 메시지가 아니면(진짜 예상 밖 DB 오류) 원래 예외를 그대로
            # 재발생시킨다 — 무조건 충돌로 위장하지 않는다.
            message = str(e).lower()
            if "locked" in message:
                raise ConflictException(
                    "이 회사의 결정이 동시에 처리되어 반영할 수 없습니다"
                    "(다른 운영자가 먼저 처리했을 수 있습니다).",
                )
            raise

        except Exception:
            self.db.rollback()
            raise

        events = []

        if event_type is not None:
            events.append(
                build_event(
                    event_type,
                    candidate_id,
                    source="operator_console",
                    correlation_id=correlation_id,
                    payload={
                        "operator_id": operator_id,
                        "company_id": company_id,
                    },
                ),
            )

        return new_status, events

    # --------------------------------------------------
    # 근거 조회
    # --------------------------------------------------

    def list_evidence(
        self,
        candidate_id: int,
        company_id: int,
    ) -> list[ProductCandidateEvidence]:
        """
        근거는 원래 전역(evidence 자체는 회사별로 나뉘지 않는다)이지만,
        PRIVATE 후보의 근거가 다른 회사에 노출되지 않도록 존재/가시성
        확인은 반드시 거친다(2026-08-15 V7 Gate 2).
        """

        self.get_visible_for_company(candidate_id, company_id)

        return self.repository.list_evidence(candidate_id)

    def list_decisions(
        self,
        candidate_id: int,
        company_id: int,
    ) -> list[ProductCandidateDecision]:
        """이 회사가 이 후보에 대해 남긴 결정 이력만 반환한다."""

        self.get_visible_for_company(candidate_id, company_id)

        return self.repository.list_decisions_for_company(
            candidate_id, company_id,
        )


__all__ = [
    "ProductCandidateService",
]
