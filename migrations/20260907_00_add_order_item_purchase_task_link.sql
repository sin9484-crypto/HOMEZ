-- HOMEZ V7 통합 매입 감사 후속(HOMEZ_V7_PROCUREMENT_LEDGER_AUDIT_20260907.md
-- §8, 사용자 확정 "purchase_task 중심") — additive only.
-- order_items.purchase_task_id: purchase_tasks.id 논리 참조. 기존
-- purchase_id(purchases.id 논리 참조)와 별개 컬럼이다 — 두 값을 한
-- 컬럼에 섞으면 어느 테이블을 참조하는지 값만 보고 구분할 수 없어
-- 위험하다. purchase_task/service.py::record_tracking()이 실제
-- 발송 시점(courier+tracking_number 둘 다 확보됐을 때)에 채운다.
ALTER TABLE order_items
    ADD COLUMN purchase_task_id INTEGER;

CREATE INDEX ix_order_items_purchase_task_id ON order_items (purchase_task_id);
