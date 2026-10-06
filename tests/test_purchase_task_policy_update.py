"""
=========================================================
Homez OS

File : tests/test_purchase_task_policy_update.py

2026-10-04 — 매입 정책 저장(전체 폼 저장 계약) 집중 검증. 최소마진만
바꿔도 다른 한도가 바뀌지 않아야 하고, 생략·명시적 null·명시적 숫자가
서로 다르게 처리돼야 하며, 정상 변경에는 감사기록이 같은 Transaction으로
남아야 한다. 격리 임시 DB만 쓴다(실제 업무 정책으로 재현하지 않는다).
=========================================================
"""

import os
import tempfile
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ConflictException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.purchase_task.model import PurchaseTaskPolicySetting
from app.domains.purchase_task.policy_service import PurchaseTaskPolicyService
from app.domains.purchase_task.schema import PolicySettingUpdate
from app.domains.role.model import Role  # noqa: F401
from app.domains.user.model import User  # noqa: F401

_AUDIT_LOGS_DDL = (
    "CREATE TABLE audit_logs ("
    "id INTEGER NOT NULL PRIMARY KEY, "
    "company_id INTEGER, user_id INTEGER, "
    "action VARCHAR(100) NOT NULL, entity VARCHAR(100) NOT NULL, "
    "entity_id VARCHAR(100) NOT NULL, description VARCHAR(500), "
    "ip_address VARCHAR(50)"
    ")"
)


class PolicyUpdateTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(
            bind=self.engine,
            tables=[Company.__table__, PurchaseTaskPolicySetting.__table__],
        )
        with self.engine.begin() as conn:
            conn.execute(text(_AUDIT_LOGS_DDL))
        self.db = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )()
        self.company = Company(
            name="회사 A", business_number="111-11-11111", ceo="대표A",
            phone="02-000-0001", email="a@example.com", address="서울",
        )
        self.db.add(self.company)
        self.db.commit()
        self.service = PurchaseTaskPolicyService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def _token(self):
        return self.service.get_or_create_default_settings(
            self.company.id,
        ).updated_at.isoformat()

    def _update(self, fields, **kwargs):
        """화면이 보내는 것과 같은 방식: 스키마를 거쳐 exclude_unset으로 덤프하고,
        화면처럼 마지막으로 조회한 수정 시각을 함께 보낸다."""

        if "if_unmodified_since" not in kwargs:
            kwargs["if_unmodified_since"] = self._token()
        return self.service.update_settings(
            self.company.id, 7,
            PolicySettingUpdate(**fields).model_dump(exclude_unset=True),
            **kwargs,
        )

    def _audits(self):
        return self.db.execute(text(
            "SELECT user_id, company_id, action, entity, description "
            "FROM audit_logs ORDER BY id"
        )).fetchall()

    def _seed_limits(self):
        self._update({
            "per_order_max_amount": 50000, "daily_purchase_limit_amount": 100000,
            "monthly_purchase_budget_amount": 500000, "max_quantity_per_product": 5,
        })
        self.db.execute(text("DELETE FROM audit_logs"))
        self.db.commit()

    def test_changing_only_min_margin_preserves_every_limit(self):

        self._seed_limits()
        setting = self._update({"min_margin_rate": 0.18})

        self.assertEqual(setting.min_margin_rate, 0.18)
        self.assertEqual(setting.per_order_max_amount, 50000)
        self.assertEqual(setting.daily_purchase_limit_amount, 100000)
        self.assertEqual(setting.monthly_purchase_budget_amount, 500000)
        self.assertEqual(setting.max_quantity_per_product, 5)

    def test_omitted_explicit_null_and_explicit_number_are_different(self):

        self._seed_limits()
        # 생략(daily/monthly) → 유지, 명시적 null(per_order) → 미설정, 숫자 → 저장
        setting = self._update({
            "per_order_max_amount": None, "max_quantity_per_product": 9,
        })
        self.assertIsNone(setting.per_order_max_amount)
        self.assertEqual(setting.max_quantity_per_product, 9)
        self.assertEqual(setting.daily_purchase_limit_amount, 100000)
        self.assertEqual(setting.monthly_purchase_budget_amount, 500000)

    def test_null_restore_stores_null_not_a_recommended_number(self):

        self._seed_limits()
        setting = self._update({
            "per_order_max_amount": None, "daily_purchase_limit_amount": None,
            "monthly_purchase_budget_amount": None, "max_quantity_per_product": None,
        })
        # 권장 기본값(50,000/100,000/500,000)은 None일 때 코드가 적용하는 값이지
        # 저장값이 아니다 — 바꿔치기해 저장하지 않는다.
        self.assertIsNone(setting.per_order_max_amount)
        self.assertIsNone(setting.daily_purchase_limit_amount)
        self.assertIsNone(setting.monthly_purchase_budget_amount)
        self.assertIsNone(setting.max_quantity_per_product)

    def test_creating_default_settings_never_fills_limits(self):

        setting = self.service.get_or_create_default_settings(self.company.id)
        self.assertIsNone(setting.per_order_max_amount)
        self.assertIsNone(setting.daily_purchase_limit_amount)
        self.assertIsNone(setting.monthly_purchase_budget_amount)
        self.assertIsNone(setting.max_quantity_per_product)

    def test_intentional_limit_change_is_saved_and_audited_with_before_after(self):

        self._seed_limits()
        self._update({"per_order_max_amount": 1000000, "min_margin_rate": 0.18})

        rows = self._audits()
        self.assertEqual(len(rows), 1)
        user_id, company_id, action, entity, description = rows[0]
        self.assertEqual(user_id, 7)
        self.assertEqual(company_id, self.company.id)
        self.assertEqual(action, "PURCHASE_TASK_POLICY_UPDATED")
        self.assertEqual(entity, "purchase_task_policy")
        self.assertIn("per_order_max_amount: 50000.0 -> 1000000.0", description)
        self.assertIn("min_margin_rate: 0.0 -> 0.18", description)
        # 바뀌지 않은 항목은 기록하지 않는다.
        self.assertNotIn("daily_purchase_limit_amount", description)
        self.assertNotIn("password", description.lower())
        self.assertNotIn("token", description.lower())

    def test_no_audit_row_when_nothing_changes(self):

        self._seed_limits()
        self._update({"per_order_max_amount": 50000})
        self.assertEqual(self._audits(), [])

    def test_stale_form_is_rejected_and_nothing_changes(self):

        self._seed_limits()
        stale = "2000-01-01T00:00:00"
        with self.assertRaises(ConflictException):
            self._update({"min_margin_rate": 0.18}, if_unmodified_since=stale)
        self.db.rollback()
        setting = self.service.get_or_create_default_settings(self.company.id)
        self.assertEqual(setting.min_margin_rate, 0.0)
        self.assertEqual(self._audits(), [])

    def test_omitted_version_token_is_rejected_and_nothing_changes(self):
        """토큰 생략은 허용하지 않는다 — 허용하면 오래된 화면·동시 요청이 다른
        변경을 조용히 덮어쓴다."""

        self._seed_limits()
        with self.assertRaises(BadRequestException) as ctx:
            self.service.update_settings(
                self.company.id, 7, {"min_margin_rate": 0.18},
            )
        self.assertIn("PURCHASE_TASK_POLICY_VERSION_REQUIRED", str(ctx.exception))
        self.db.rollback()
        self.assertEqual(
            self.service.get_or_create_default_settings(self.company.id).min_margin_rate,
            0.0,
        )
        self.assertEqual(self._audits(), [])

    def test_two_requests_that_read_the_same_previous_value_do_not_lose_a_change(self):
        """같은 이전 값을 읽은 두 요청 중 하나만 성공한다(조건부 UPDATE). 뒤
        요청은 사전 비교를 통과하더라도(자기 세션이 낡은 값을 들고 있음)
        0행 갱신으로 거부돼야 하고, 앞 요청의 변경과 감사 한 건만 남는다."""

        self._seed_limits()
        token = self._token()

        other_db = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)()
        self.addCleanup(other_db.close)
        service_b = PurchaseTaskPolicyService(other_db)
        stale_row = service_b.get_or_create_default_settings(self.company.id)
        self.assertEqual(stale_row.updated_at.isoformat(), token)  # B도 같은 값을 읽음

        self._update({"per_order_max_amount": 111}, if_unmodified_since=token)  # A 성공

        # B 세션은 아직 낡은 값을 들고 있어 사전 비교(`updated_at != 기대값`)는
        # 통과한다 — 아래 충돌은 조건부 UPDATE(0행)가 막은 것이다.
        self.assertEqual(
            service_b.get_or_create_default_settings(self.company.id)
            .updated_at.isoformat(), token,
        )
        with self.assertRaises(ConflictException):
            service_b.update_settings(
                self.company.id, 8,
                PolicySettingUpdate(per_order_max_amount=222).model_dump(exclude_unset=True),
                if_unmodified_since=token,
            )
        other_db.rollback()

        self.db.expire_all()
        self.assertEqual(
            self.service.get_or_create_default_settings(self.company.id).per_order_max_amount,
            111,
        )
        rows = self._audits()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 7)  # 성공한 요청의 변경자만 기록

    def test_conflict_writes_neither_a_change_nor_an_audit_row(self):

        self._seed_limits()
        token = self._token()
        self._update({"per_order_max_amount": 111}, if_unmodified_since=token)
        before_audits = self._audits()

        with self.assertRaises(ConflictException):
            self._update({"min_margin_rate": 0.18}, if_unmodified_since=token)
        self.db.rollback()
        self.assertEqual(self._audits(), before_audits)
        self.assertEqual(
            self.service.get_or_create_default_settings(self.company.id).min_margin_rate,
            0.0,
        )

    def test_current_form_version_is_accepted(self):

        self._seed_limits()
        current = self.service.get_or_create_default_settings(
            self.company.id,
        ).updated_at.isoformat()
        setting = self._update({"min_margin_rate": 0.18}, if_unmodified_since=current)
        self.assertEqual(setting.min_margin_rate, 0.18)

    def test_audit_failure_rolls_back_the_policy_change_too(self):

        self._seed_limits()
        with mock.patch(
            "app.domains.purchase_task.policy_service.write_audit_log",
            side_effect=RuntimeError("audit down"),
        ):
            with self.assertRaises(RuntimeError):
                self._update({"min_margin_rate": 0.18, "per_order_max_amount": 1})
        self.db.expire_all()
        setting = self.service.get_or_create_default_settings(self.company.id)
        self.assertEqual(setting.min_margin_rate, 0.0)
        self.assertEqual(setting.per_order_max_amount, 50000)
        self.assertEqual(self._audits(), [])

    def test_company_isolation(self):

        other = Company(
            name="회사 B", business_number="222-22-22222", ceo="대표B",
            phone="02-000-0002", email="b@example.com", address="부산",
        )
        self.db.add(other)
        self.db.commit()
        self._update({"per_order_max_amount": 777})

        b = self.service.get_or_create_default_settings(other.id)
        self.assertIsNone(b.per_order_max_amount)


class PolicyDialogSourceTestCase(unittest.TestCase):
    """화면 보호 장치: 저장 전에 바뀌는 항목을 이전→이후로 확인받고, 비워 둔
    한도의 권장값 안내는 placeholder일 뿐 값으로 저장되지 않는다."""

    @classmethod
    def setUpClass(cls):

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "app", "web", "console.js"), encoding="utf-8") as f:
            js = f.read()
        start = js.index("function ptOpenPolicyDialog()")
        cls.dialog = js[start:start + 9000]
        cls.root = root

    def test_confirm_lists_changes_before_the_recent_auth_call(self):

        confirm = self.dialog.index("purchase_task.policy_confirm_changes")
        recent_auth = self.dialog.index('"/auth/recent-auth"')
        self.assertLess(confirm, recent_auth)
        self.assertIn("changedLines.push(", self.dialog)

    def test_recommended_defaults_are_placeholders_never_values(self):

        for field in ("per-order", "daily", "monthly"):
            self.assertIn(f"policy_placeholder_{field.replace('-', '_')}", self.dialog)
        # 한도 입력칸에 권장 숫자를 value로 대입하는 코드가 없다.
        for recommended in ("50000", "100000", "500000"):
            self.assertNotIn(f".value = {recommended}", self.dialog)

    def test_translations_exist_in_both_languages_and_label_key_is_not_duplicated(self):

        for catalog in ("ko-KR.js", "en-US.js"):
            with open(
                os.path.join(self.root, "app", "web", "i18n", catalog),
                encoding="utf-8",
            ) as f:
                text_ = f.read()
            for key in (
                "purchase_task.policy_confirm_changes",
                "purchase_task.policy_value_unset",
                "purchase_task.policy_placeholder_per_order",
                "purchase_task.field_task_fee_amount_estimate",
            ):
                self.assertEqual(text_.count(f'"{key}"'), 1, (catalog, key))
            self.assertEqual(
                text_.count('"purchase_task.field_coupang_fee_amount"'), 1, catalog,
            )


if __name__ == "__main__":
    unittest.main()
