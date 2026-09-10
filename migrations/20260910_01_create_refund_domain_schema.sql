-- Purpose: HOMEZ 사용자 운영 기준(HOMEZ_USER_OPERATION_SETTINGS.md)
-- 8·9번 — "취소·반품·교환·환불을 별도 상태로 관리한다", "반품은
-- 사용자 안내와 승인을 거친 뒤에만 진행한다." Phase 8 구현.
-- Source of Truth: app/domains/refund/model.py
--                   (Refund, RefundStatusEvent),
--                   app/domains/refund/constants.py
--                   (RefundType, RefundStatus)
--
-- app/domains/return_order(물류 — 회수/검수)와 완전히 별개 테이블
-- 이다. Marketplace Settlement/Funding Account/Funding Hold/Supplier
-- Payment 테이블은 이 Migration이 전혀 참조하지 않는다(CLAUDE.md
-- Domain 경계) — order_id/return_order_id는 전부 논리 참조(FK 없음).
--
-- refund_status_events는 return_order_status_events와 동일한
-- append-only 감사 이력 패턴이다.
--
-- 완전히 새로운 테이블만 추가한다(ALTER 없음) — 기존 테이블은 전혀
-- 건드리지 않는다.

BEGIN;

CREATE TABLE refunds (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    return_order_id INTEGER,
    refund_type VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'AWAITING_APPROVAL',
    amount FLOAT NOT NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'KRW',
    reason VARCHAR(500) NOT NULL,
    idempotency_key VARCHAR(150) NOT NULL,
    requested_by INTEGER NOT NULL,
    approved_by INTEGER,
    requested_at DATETIME NOT NULL,
    approved_at DATETIME,
    executed_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE (company_id, idempotency_key)
);

CREATE INDEX ix_refunds_id ON refunds (id);
CREATE INDEX ix_refunds_company_id ON refunds (company_id);
CREATE INDEX ix_refunds_order_id ON refunds (order_id);
CREATE INDEX ix_refunds_return_order_id ON refunds (return_order_id);
CREATE INDEX ix_refunds_refund_type ON refunds (refund_type);
CREATE INDEX ix_refunds_status ON refunds (status);

CREATE TABLE refund_status_events (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    refund_id INTEGER NOT NULL,
    previous_status VARCHAR(20),
    new_status VARCHAR(20) NOT NULL,
    reason VARCHAR(500),
    triggered_by INTEGER,
    created_at DATETIME NOT NULL
);

CREATE INDEX ix_refund_status_events_id ON refund_status_events (id);
CREATE INDEX ix_refund_status_events_company_id ON refund_status_events (company_id);
CREATE INDEX ix_refund_status_events_refund_id ON refund_status_events (refund_id);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 완전히 새로운 테이블이라 DROP TABLE로 데이터 손실 없이
-- 되돌릴 수 있다 — 단, 이미 실제 환불 이력이 쌓였다면 그 이력
-- 자체는 DROP TABLE로 사라진다):
-- BEGIN;
-- DROP TABLE refund_status_events;
-- DROP TABLE refunds;
-- COMMIT;
