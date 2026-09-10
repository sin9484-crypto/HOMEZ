"""
=========================================================
Homez OS

File : tests/test_audit_log_description_masking.py

2026-09-10 Phase 11(HOMEZ_USER_OPERATION_SETTINGS.md 11번 — "로그에는
누가 언제 어떤 작업을 했는지는 기록하되 전화번호, 주소, API 키와
카드정보는 항상 가린다") — `write_audit_log()`가 이제 `description`
을 저장 직전에 항상 `redact_free_text()`로 통과시키는지 검증한다
(이전에는 이 게이트가 없었다, Medium 결함으로 기록됨).
=========================================================
"""

import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.audit_db import write_audit_log

AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50), created_at DATETIME"
    ")"
)


class AuditLogDescriptionMaskingTestCase(unittest.TestCase):

    def setUp(self):

        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as conn:
            conn.execute(text(AUDIT_LOGS_DDL))

        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

    def tearDown(self):

        self.db.close()
        self.engine.dispose()

    def _last_description(self) -> str:

        row = self.db.execute(
            text("SELECT description FROM audit_logs ORDER BY id DESC LIMIT 1"),
        ).fetchone()
        return row[0]

    def test_phone_shaped_text_in_description_is_masked(self):

        write_audit_log(
            self.db, user_id=1, action="TEST_ACTION", entity="test",
            entity_id="1",
            description="고객 연락처 010-1234-5678로 재발송 요청",
        )
        self.db.commit()

        stored = self._last_description()
        self.assertNotIn("010-1234-5678", stored)

    def test_jwt_shaped_text_in_description_is_masked(self):

        token = (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        write_audit_log(
            self.db, user_id=1, action="TEST_ACTION", entity="test",
            entity_id="1", description=f"token={token} 갱신됨",
        )
        self.db.commit()

        stored = self._last_description()
        self.assertNotIn(token, stored)
        self.assertIn("***REDACTED_TOKEN***", stored)

    def test_ordinary_description_is_unaffected(self):
        """일반적인 설명 문구(전화번호·JWT 패턴이 아닌 텍스트)는 이
        게이트를 통과해도 바뀌지 않아야 한다 — 기존 호출부의 감사
        로그 가독성을 해치지 않는다."""

        write_audit_log(
            self.db, user_id=1, action="UNLOCK_USER", entity="user",
            entity_id="5", description="user(id=5) 계정 잠금 해제됨",
        )
        self.db.commit()

        self.assertEqual(self._last_description(), "user(id=5) 계정 잠금 해제됨")

    def test_none_description_still_rejected_by_not_null_semantics(self):
        """description 파라미터 자체는 여전히 str 타입 계약을 따른다
        — 이 테스트는 마스킹 게이트가 빈 문자열을 예외 없이 그대로
        통과시키는지만 확인한다(redact_free_text가 falsy 값을 그대로
        반환하는 계약과 일치)."""

        write_audit_log(
            self.db, user_id=1, action="TEST_ACTION", entity="test",
            entity_id="1", description="",
        )
        self.db.commit()

        self.assertEqual(self._last_description(), "")


if __name__ == "__main__":
    unittest.main()
