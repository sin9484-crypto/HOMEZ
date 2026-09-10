-- Purpose: HOMEZ 사용자 운영 기준(HOMEZ_USER_OPERATION_SETTINGS.md)
-- 2·3·4·13·14번 — "전역 단일 자동화 상태를 기능별 상태로 분리한다.
-- 각 기능에 수동, 반자동, 자동, 중지 모드를 둔다." Phase 3 구현.
-- Source of Truth: app/domains/automation_safety/model.py
--                   (FunctionAutomationState),
--                   app/domains/automation_safety/constants.py
--                   (FunctionCode, FunctionMode)
--
-- 기존 `automation_mode_states`(전역 단일, company_id 없음)와는 별개
-- 신규 테이블이다 — 기존 테이블·데이터는 전혀 건드리지 않는다(기존
-- 테이블은 여전히 app/web/router.py의 레거시 safety-status 화면이
-- 참조할 수 있으므로 남겨둔다).
--
-- company_id + function_code 조합별로 append-only 이력을 쌓는다
-- (automation_mode_states와 동일한 패턴 — 가장 최근 행이 현재 상태,
-- 모드 변경 이력 자체가 감사 기록). 회사가 아직 없거나(개인 베타
-- 극초기) 특정 기능에 대해 한 번도 값을 설정한 적이 없으면 코드
-- 레벨에서 안전한 기본값(MANUAL)으로 처리한다 — 이 테이블에 굳이
-- "기본값 행"을 미리 채워 넣지 않는다(추측으로 채우지 않는다는
-- 원칙과 동일).
--
-- 완전히 새로운 테이블만 추가한다(ALTER 없음) — 기존 테이블은 전혀
-- 건드리지 않는다.

BEGIN;

CREATE TABLE function_automation_states (
    id INTEGER NOT NULL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    function_code VARCHAR(40) NOT NULL,
    mode VARCHAR(20) NOT NULL,
    set_by INTEGER NOT NULL,
    reason VARCHAR(500),
    set_at DATETIME NOT NULL
);

CREATE INDEX ix_function_automation_states_id ON function_automation_states (id);
CREATE INDEX ix_function_automation_states_company_id ON function_automation_states (company_id);
CREATE INDEX ix_function_automation_states_function_code ON function_automation_states (function_code);
CREATE INDEX ix_function_automation_states_company_function ON function_automation_states (company_id, function_code);
CREATE INDEX ix_function_automation_states_set_at ON function_automation_states (set_at);

COMMIT;

-- Rollback (실행하지 않음 — 실제 적용 시 필요하면 아래를 그대로
-- 실행. 완전히 새로운 테이블이라 DROP TABLE로 데이터 손실 없이
-- 되돌릴 수 있다 — 단, 이미 실제 모드 변경 이력이 쌓였다면 그
-- 이력 자체는 DROP TABLE로 사라진다):
-- BEGIN;
-- DROP TABLE function_automation_states;
-- COMMIT;
