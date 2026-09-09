"""
=========================================================
Homez OS

File : app/domains/channel_policy/repository.py

채널 정책 엔진 — 순수 조회/쓰기 헬퍼(비즈니스 판단은 engine.py/
service.py가 전담). FK를 쓰지 않는 이 코드베이스 전역 컨벤션을
그대로 따른다.
=========================================================
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.channel_policy.model import ChannelPolicyEvaluation
from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.model import CompanyChannelPolicySettings


class ChannelPolicyRepository:

    def __init__(self, db: Session):

        self.db = db

    def get_rule(self, channel: str, rule_code: str) -> ChannelPolicyRule | None:

        return self.db.execute(
            select(ChannelPolicyRule).where(
                ChannelPolicyRule.channel == channel,
                ChannelPolicyRule.rule_code == rule_code,
            ),
        ).scalar_one_or_none()

    def list_active_rules(self, channel: str) -> list[ChannelPolicyRule]:

        return list(
            self.db.execute(
                select(ChannelPolicyRule)
                .where(
                    ChannelPolicyRule.channel == channel,
                    ChannelPolicyRule.active.is_(True),
                )
                .order_by(ChannelPolicyRule.id),
            ).scalars(),
        )

    def has_any_rule_row_for_channel(self, channel: str) -> bool:
        """이 채널에 규칙 행이 하나라도 있는지(active 무관) —
        Audit(2026-08-21, CTO 후속 지시) 발견: `active=True`인 행이
        0건인 것과 "이 채널 정책 카탈로그가 아예 시딩된 적 없음"은
        서로 다른 상태다. 전자는 관리자가 명시적으로 전부 비활성화한
        상태(정책 제약 없음이 의도된 사실)일 수 있지만, 후자는 신규
        설치·업그레이드에서 `seed_rule_catalog()`가 아직 실행되지
        않았거나 실패한 상태 — 이 경우 규칙이 없다는 이유로 무조건
        CHANNEL_ELIGIBLE을 내면 정책 게이트 자체가 무력화된다.
        `evaluate_and_record()`가 이 값으로 두 상태를 구분해 fail-
        closed 처리한다."""

        return (
            self.db.execute(
                select(ChannelPolicyRule.id)
                .where(ChannelPolicyRule.channel == channel)
                .limit(1),
            ).scalar_one_or_none()
            is not None
        )

    def add_rule_no_commit(self, rule: ChannelPolicyRule) -> ChannelPolicyRule:

        self.db.add(rule)
        self.db.flush()

        return rule

    def get_settings(
        self, company_id: int,
    ) -> CompanyChannelPolicySettings | None:

        return self.db.execute(
            select(CompanyChannelPolicySettings).where(
                CompanyChannelPolicySettings.company_id == company_id,
            ),
        ).scalar_one_or_none()

    def add_evaluation_no_commit(
        self, evaluation: ChannelPolicyEvaluation,
    ) -> ChannelPolicyEvaluation:

        self.db.add(evaluation)
        self.db.flush()

        return evaluation

    def get_latest_evaluation(
        self, company_id: int, product_candidate_id: int, channel: str,
    ) -> ChannelPolicyEvaluation | None:
        """
        Audit(2026-08-21) — `created_at`만으로 정렬하면 동시/경쟁
        평가가 같은 밀리초에 커밋될 때 "최신"이 비결정적이다. 이
        코드베이스의 다른 append-only 이력 조회(예: `Marketplace
        SubmissionApproval`)는 전부 auto-increment `id.desc()`를
        1차 기준으로 쓴다 — 동일 컨벤션으로 통일해 결정적으로 만든다.
        """

        return self.db.execute(
            select(ChannelPolicyEvaluation)
            .where(
                ChannelPolicyEvaluation.company_id == company_id,
                ChannelPolicyEvaluation.product_candidate_id
                == product_candidate_id,
                ChannelPolicyEvaluation.channel == channel,
            )
            .order_by(ChannelPolicyEvaluation.id.desc())
            .limit(1),
        ).scalar_one_or_none()

    def list_evaluation_history(
        self, company_id: int, product_candidate_id: int, channel: str,
    ) -> list[ChannelPolicyEvaluation]:

        return list(
            self.db.execute(
                select(ChannelPolicyEvaluation)
                .where(
                    ChannelPolicyEvaluation.company_id == company_id,
                    ChannelPolicyEvaluation.product_candidate_id
                    == product_candidate_id,
                    ChannelPolicyEvaluation.channel == channel,
                )
                .order_by(ChannelPolicyEvaluation.id.desc()),
            ).scalars(),
        )


__all__ = ["ChannelPolicyRepository"]
