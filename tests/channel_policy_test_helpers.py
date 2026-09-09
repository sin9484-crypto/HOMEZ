"""
=========================================================
Homez OS

File : tests/channel_policy_test_helpers.py

CA-1(2026-08-21) — 제출 직전 채널 정책 강제 게이트 도입으로,
`SubmissionService.submit()`을 호출하는 기존 테스트들은 이제 실제
유효한 `ChannelPolicyEvaluation`(SUBMITTABLE 결과 + 지금 상태와
일치하는 입력 지문)이 미리 있어야 한다. 이 헬퍼는 그 준비 단계를
한 곳에 모아, 각 테스트 파일이 반복하지 않게 한다 — 실제
`ChannelPolicyService.evaluate_and_record()`를 그대로 호출한다
(정책 판정 로직을 흉내내지 않고 진짜 엔진을 그대로 통과시킨다).
=========================================================
"""

from sqlalchemy import text

from app.domains.channel_policy.model import ChannelPolicyRule
from app.domains.channel_policy.service import ChannelPolicyService
from app.domains.marketplace_listing.category_metadata import (
    notice_input_fingerprint,
)


def _ensure_audit_logs_table(db) -> None:
    """2026-08-24 Section 2 — seed_rule_catalog()/evaluate_and_record()가
    이제 write_audit_log()를 호출한다. 이 헬퍼를 쓰는 여러 테스트
    파일이 필요한 테이블만 선별적으로 만드는 Base.metadata.create_all
    (tables=[...]) 방식을 쓰기 때문에 audit_logs(별도 ORM Model이
    없는 raw 테이블)가 없을 수 있다 — IF NOT EXISTS로 멱등하게
    보강한다(MigrationRunner로 전체 Migration을 적용한 DB에는 이미
    있으므로 그 경우는 그대로 no-op)."""

    db.execute(text(
        "CREATE TABLE IF NOT EXISTS audit_logs ("
        "id INTEGER NOT NULL PRIMARY KEY, "
        "company_id INTEGER, user_id INTEGER, "
        "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
        "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
        "ip_address VARCHAR(50)"
        ")",
    ))
    db.commit()


def seed_channel_eligible_policy(
    db, *, company_id: int, product_candidate_id: int, channel: str,
    selection_id: int, evaluated_by: int = 1,
):
    """
    Audit(2026-08-21, CTO 후속 지시) — 이전에는 카탈로그를 시딩하지
    않으면 활성 규칙이 0건이라 자동으로 CHANNEL_ELIGIBLE이 나오는
    fail-open 결함에 기대어 이 헬퍼가 동작했다. 그 결함을
    `ChannelPolicyService.evaluate_and_record()`에서 수정했으므로
    (카탈로그가 이 채널에 대해 한 번도 시딩되지 않았으면 이제
    CHANNEL_DATA_REQUIRED로 fail-closed 처리) 이 헬퍼도 실제
    `seed_rule_catalog()`를 먼저 호출한다(멱등 — 여러 테스트가 같은
    DB에서 반복 호출해도 안전). 실제 쿠팡 규칙이 요구하는 구조화
    필드(원산지·브랜드·구매옵션·상품식별번호)까지 아래
    `product_attributes`가 전부 채우므로, 카탈로그가 실제로 시딩된
    뒤에도 이 헬퍼는 여전히 CHANNEL_ELIGIBLE(또는 통과 가능한
    ELIGIBLE_WITH_ACTIONS)을 반환한다.
    """

    _ensure_audit_logs_table(db)

    service = ChannelPolicyService(db)
    service.seed_rule_catalog()

    return service.evaluate_and_record(
        company_id=company_id,
        product_candidate_id=product_candidate_id,
        channel=channel,
        category_hint_override=None,
        product_attributes={
            "origin_country": "KOREA",
            "brand": "테스트브랜드",
            "purchase_options": {"opt": "1"},
            "product_identifier": "TEST-SKU-001",
            "deliveryType": "DELIVERY",
            "deliveryAttributeType": "NORMAL",
            "official_category_code": "99999",
            "category_metadata_version": "test-meta-v1",
            "category_metadata_fingerprint": "test-meta-fingerprint",
            "notice_information": {
                "기타 재화::품명 및 모델명": "테스트 상품",
                "__notice_category_name": "기타 재화",
            },
            "notice_required_field_keys": ["기타 재화::품명 및 모델명"],
            "notice_confirmed_at": "2026-08-24T00:00:00",
            "notice_confirmed_by_user_id": evaluated_by,
            "notice_input_fingerprint": notice_input_fingerprint(
                "99999", "test-meta-v1", "test-meta-fingerprint",
                {
                    "기타 재화::품명 및 모델명": "테스트 상품",
                    "__notice_category_name": "기타 재화",
                },
            ),
        },
        confirmed_evidence_rule_codes=[
            r.rule_code for r in
            db.query(ChannelPolicyRule).filter(
                ChannelPolicyRule.channel == channel,
                ChannelPolicyRule.active.is_(True),
            ).all()
        ],
        evaluated_by=evaluated_by,
        selection_id=selection_id,
    )


__all__ = ["seed_channel_eligible_policy"]
