-- Purpose: HOMEZ 사용자 운영 기준(HOMEZ_USER_OPERATION_SETTINGS.md)
-- 4·5·11번 — "카드·PayPal·계좌이체·가상계좌를 결제수단 종류로
-- 설계한다", "카드번호+CVC 원문을 저장하지 않는다", "자동결제는
-- 모드·한도·재인증으로 통제한다." Phase 7 구현.
-- Source of Truth: app/domains/payment/model.py
--                   (PaymentMethod, PaymentAutoLimit),
--                   app/domains/payment/constants.py
--                   (PaymentMethodType)
--
-- 완전히 새로운 테이블만 추가한다(ALTER 없음) — 기존 테이블은 전혀
-- 건드리지 않는다. Marketplace Settlement/Funding Account/Funding
-- Hold/Refund 테이블은 이 Migration이 전혀 참조하지 않는다
-- (CLAUDE.md Domain 경계). 이 결제수단이 Customer Payment와
-- Supplier Payment 중 어느 쪽에 속하는지는 아직 확정하지 않았다 —
-- app/domains/payment/model.py 상단 주석과
-- docs/HOMEZ_PROJECT_STATE.md Phase 7 절 참고.
--
-- payment_methods.credential_target_name은 Windows Credential
-- Manager 참조 문자열일 뿐이다 — 카드번호·CVC 원문이 들어갈 컬럼
-- 자체가 이 테이블에 없다.
--
-- payment_auto_limits는 function_automation_states와 동일하게
-- append-only다 — company_id별 가장 최근 행이 현재 한도.

BEGIN;

CREATE TABLE payment_methods (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    method_type VARCHAR(20) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    credential_target_name VARCHAR(200) NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    deactivated_at DATETIME,
    UNIQUE (credential_target_name)
);

CREATE INDEX ix_payment_methods_id ON payment_methods (id);
CREATE INDEX ix_payment_methods_company_id ON payment_methods (company_id);
CREATE INDEX ix_payment_methods_active ON payment_methods (active);

CREATE TABLE payment_auto_limits (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    per_transaction_limit_amount FLOAT NOT NULL,
    daily_limit_amount FLOAT NOT NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'KRW',
    set_by INTEGER NOT NULL,
    set_at DATETIME NOT NULL
);

CREATE INDEX ix_payment_auto_limits_id ON payment_auto_limits (id);
CREATE INDEX ix_payment_auto_limits_company_id ON payment_auto_limits (company_id);
CREATE INDEX ix_payment_auto_limits_set_at ON payment_auto_limits (set_at);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 완전히 새로운 테이블이라 DROP TABLE로 데이터 손실 없이
-- 되돌릴 수 있다 — 단, 이미 실제 결제수단/한도 이력이 쌓였다면 그
-- 이력 자체는 DROP TABLE로 사라진다. credential_target_name이
-- 가리키는 Windows Credential Manager 항목은 이 SQL로 삭제되지
-- 않는다 — 별도로 정리해야 한다):
-- BEGIN;
-- DROP TABLE payment_auto_limits;
-- DROP TABLE payment_methods;
-- COMMIT;
