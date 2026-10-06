"""
=========================================================
Homez OS

File : tests/test_product_attribute_match_service.py

2026-09-15 전면 감사 후속(Phase 9G, HOMEZ_USER_OPERATION_SETTINGS.md
10-4 — "상품 속성을 매입처·후보·판매채널 세 소스 사이에서 정규화해
비교하고, 필수 항목이 불일치하거나 확인 불가면 자동 등록/자동 발주를
막는다") — ProductAttributeMatchService 격리 테스트. 실제 homez.db·
실제 외부 API는 전혀 없다(임시 SQLite 파일 DB만 사용).
=========================================================
"""

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import ForbiddenException
from app.core.exceptions import NotFoundException
from app.database.bootstrap import bootstrap_environment
from app.domains.company.model import Company
from app.domains.product_attribute_match.constants import AttributeMatchStatus
from app.domains.product_attribute_match.constants import ComparisonRunStatus
from app.domains.product_attribute_match.constants import ProductAttributeField
from app.domains.product_attribute_match.service import ProductAttributeMatchService
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


def _blank(*fields):

    return {f: (None, None, None) for f in fields}


class ProductAttributeMatchServiceTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        self.db_path = Path(path)
        self.backups_dir = Path(tempfile.mkdtemp())

        result = bootstrap_environment(
            db_path=self.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=self.backups_dir,
        )
        self.assertTrue(result.is_new_install)

        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.company = Company(
            name="속성비교 테스트 회사", business_number="888-88-88881",
            ceo="테스트", phone="02-000-0000",
            email="pam@example.com", address="테스트",
        )
        self.db.add(self.company)
        self.db.commit()

        self.role = Role(name="Administrator", code="SUPER_ADMIN")
        self.db.add(self.role)
        self.db.commit()

        self.admin = User(
            company_id=self.company.id, username="pamadmin",
            email="pamadmin@example.com", password_hash="x",
            name="관리자", role_id=self.role.id, is_active=True,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.service = ProductAttributeMatchService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    def _all_fields_matched(self, name="테스트 상품"):

        values = {
            field: (name, "TEST", None) for field in ProductAttributeField.ALL
        }
        return values

    # ------------------------------
    # 공급처 상품 기준 조회(등록 직전 점검이 후보의 source_reference로 조회)
    # ------------------------------

    def _company_b(self):
        other = Company(
            name="다른 회사", business_number="888-88-88882", ceo="테스트",
            phone="02-000-0001", email="other@example.com", address="테스트",
        )
        self.db.add(other)
        self.db.commit()
        return other

    def _connection(self, company, mall_code="ONCHANNEL"):
        row = PurchaseChannelConnection(
            company_id=company.id, mall_code=mall_code,
            connection_method="CREDENTIAL", account_label="테스트 계정",
            status="CONNECTED",
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _run(self, company, code, connection, *, blocked=True, created_at=None):
        values = {} if blocked else self._all_fields_matched()
        run = self.service.run_comparison(
            company_id=company.id, product_identifier=code,
            connection_id=connection.id if connection is not None else None,
            supplier_values=values,
            sales_channel_values=values, homez_current_values=values,
        )
        if created_at is not None:
            run.created_at = created_at
            self.db.commit()
        return run

    def _blocking(self, company, ref="ONCHANNEL:CH-X"):
        return self.service.has_blocking_mismatch_for_supplier_source(company.id, ref)

    def test_parse_supplier_source_reference(self):

        parse = ProductAttributeMatchService.parse_supplier_source_reference
        self.assertEqual(parse("ONCHANNEL:CH1147184"), ("ONCHANNEL", "CH1147184"))
        for unclear in (None, "", "ONCHANNEL", ":CH1", "ONCHANNEL:", "  :  "):
            self.assertIsNone(parse(unclear), unclear)

    def test_blocked_comparison_of_the_suppliers_product_is_found(self):

        conn = self._connection(self.company)
        self._run(self.company, "CH-X", conn, blocked=True)
        self.assertTrue(self._blocking(self.company))

    def test_resolved_blocked_comparison_no_longer_blocks(self):

        conn = self._connection(self.company)
        run = self._run(self.company, "CH-X", conn, blocked=True)
        from datetime import datetime
        run.resolved_at = datetime.utcnow()
        run.resolved_by = self.admin.id
        self.db.commit()
        self.assertFalse(self._blocking(self.company))

    def test_latest_run_decides_not_an_older_one(self):

        from datetime import datetime, timedelta
        conn = self._connection(self.company)
        now = datetime.utcnow()
        self._run(self.company, "CH-X", conn, blocked=True, created_at=now - timedelta(days=2))
        self._run(self.company, "CH-X", conn, blocked=False, created_at=now - timedelta(days=1))
        self.assertFalse(self._blocking(self.company))  # 나중에 통과한 비교가 최신

        self._run(self.company, "CH-X", conn, blocked=True, created_at=now)
        self.assertTrue(self._blocking(self.company))  # 그 뒤 다시 BLOCKED면 차단

    def test_another_companys_blocked_run_never_leaks(self):

        other = self._company_b()
        other_conn = self._connection(other)
        self._connection(self.company)  # 이 회사도 같은 매입처 연결은 있지만 기록은 없음
        self._run(other, "CH-X", other_conn, blocked=True)
        self.assertFalse(self._blocking(self.company))
        self.assertTrue(self._blocking(other))

    def test_same_code_on_a_different_supplier_mall_is_not_mixed(self):

        onchannel = self._connection(self.company, "ONCHANNEL")
        naver = self._connection(self.company, "NAVER_SHOPPING")
        self._run(self.company, "CH-X", naver, blocked=True)
        self.assertFalse(self._blocking(self.company, "ONCHANNEL:CH-X"))
        self.assertTrue(self._blocking(self.company, "NAVER_SHOPPING:CH-X"))
        self.assertIsNotNone(onchannel)

    def test_run_without_a_connection_is_not_attributed_to_any_supplier(self):

        self._connection(self.company)
        self._run(self.company, "CH-X", None, blocked=True)
        self.assertFalse(self._blocking(self.company))

    def test_two_connections_of_the_same_mall_block_if_any_is_unresolved(self):
        """어느 연결에서 온 상품인지 불명확하면 안전한 쪽(하나라도 미해소
        BLOCKED면 차단)."""

        from datetime import datetime, timedelta
        first = self._connection(self.company)
        second = self._connection(self.company)
        now = datetime.utcnow()
        self._run(self.company, "CH-X", first, blocked=False, created_at=now)
        self._run(self.company, "CH-X", second, blocked=True, created_at=now - timedelta(hours=1))
        self.assertTrue(self._blocking(self.company))

    def test_unclear_mapping_or_no_runs_is_not_applicable(self):

        self._connection(self.company)
        for unclear in (None, "", "no-colon", "ONCHANNEL:"):
            self.assertFalse(self._blocking(self.company, unclear), unclear)
        self.assertFalse(self._blocking(self.company, "ONCHANNEL:NEVER-COMPARED"))

    # ------------------------------
    # 필드 비교 판정(MATCHED/MISMATCHED/UNCONFIRMED)
    # ------------------------------

    def test_all_three_sources_agree_is_matched(self):

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P1",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=self._all_fields_matched(),
            homez_current_values=self._all_fields_matched(),
        )

        self.assertEqual(run.overall_status, ComparisonRunStatus.PASSED)
        self.assertTrue(
            all(i.match_status == AttributeMatchStatus.MATCHED for i in run.items),
        )

    def test_differing_values_are_mismatched_and_block(self):

        supplier = self._all_fields_matched()
        candidate = self._all_fields_matched()
        candidate[ProductAttributeField.NAME] = ("다른 상품명", "CANDIDATE", None)

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P2",
            supplier_values=supplier,
            sales_channel_values=self._all_fields_matched(),
            homez_current_values=candidate,
        )

        self.assertEqual(run.overall_status, ComparisonRunStatus.BLOCKED)
        name_item = next(
            i for i in run.items if i.field_name == ProductAttributeField.NAME
        )
        self.assertEqual(name_item.match_status, AttributeMatchStatus.MISMATCHED)

    def test_only_one_source_available_is_unconfirmed_not_matched(self):
        """소스가 하나뿐이면(나머지 둘은 아직 확인 안 됨) "같다"고
        판단할 근거가 없다 — MATCHED가 아니라 UNCONFIRMED다."""

        supplier = self._all_fields_matched()

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P3",
            supplier_values=supplier,
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )

        self.assertEqual(run.overall_status, ComparisonRunStatus.BLOCKED)
        self.assertTrue(
            all(
                i.match_status == AttributeMatchStatus.UNCONFIRMED
                for i in run.items
            ),
        )

    def test_no_sources_at_all_is_unconfirmed(self):

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P4",
            supplier_values=_blank(*ProductAttributeField.ALL),
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )

        self.assertEqual(run.overall_status, ComparisonRunStatus.BLOCKED)
        self.assertTrue(
            all(
                i.match_status == AttributeMatchStatus.UNCONFIRMED
                for i in run.items
            ),
        )

    def test_whitespace_and_case_only_differences_still_match(self):
        """정규화(공백 접기·대소문자 무시) 후 같으면 MATCHED — 원본
        값 자체는 그대로 저장된다(비교용 정규화일 뿐)."""

        supplier = {
            f: ("  Test   Product  ", "SRC", None)
            for f in ProductAttributeField.ALL
        }
        candidate = {
            f: ("test product", "SRC", None)
            for f in ProductAttributeField.ALL
        }

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P5",
            supplier_values=supplier,
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=candidate,
        )

        self.assertEqual(run.overall_status, ComparisonRunStatus.PASSED)
        name_item = next(
            i for i in run.items if i.field_name == ProductAttributeField.NAME
        )
        # 원본 값(공백 포함)은 그대로 저장된다.
        self.assertEqual(name_item.supplier_value, "  Test   Product  ")

    def test_missing_product_identifier_rejected(self):

        with self.assertRaises(BadRequestException):
            self.service.run_comparison(
                company_id=self.company.id, product_identifier="   ",
                supplier_values={}, sales_channel_values={},
                homez_current_values={},
            )

    def test_unspecified_fields_default_to_unconfirmed(self):
        """호출부가 일부 필드만 넘겨도 나머지 필드는 누락 자체가
        UNCONFIRMED 항목으로 정직하게 기록된다."""

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P6",
            supplier_values={
                ProductAttributeField.NAME: ("상품", "SRC", None),
            },
            sales_channel_values={},
            homez_current_values={},
        )

        self.assertEqual(len(run.items), len(ProductAttributeField.ALL))
        self.assertTrue(
            all(
                i.match_status == AttributeMatchStatus.UNCONFIRMED
                for i in run.items
            ),
        )

    # ------------------------------
    # 자동 등록/자동 발주 차단 게이트
    # ------------------------------

    def test_no_prior_comparison_does_not_block(self):

        self.service.assert_attributes_confirmed_or_block(
            self.company.id, "NEVER-COMPARED",
        )  # 예외 없이 통과해야 한다.
        self.assertFalse(
            self.service.has_blocking_attribute_mismatch(
                self.company.id, "NEVER-COMPARED",
            ),
        )

    def test_blocked_run_blocks_gate(self):

        from app.core.exceptions import ConflictException

        self.service.run_comparison(
            company_id=self.company.id, product_identifier="P7",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )

        self.assertTrue(
            self.service.has_blocking_attribute_mismatch(self.company.id, "P7"),
        )
        with self.assertRaises(ConflictException):
            self.service.assert_attributes_confirmed_or_block(
                self.company.id, "P7",
            )

    def test_passed_run_does_not_block_gate(self):

        self.service.run_comparison(
            company_id=self.company.id, product_identifier="P8",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=self._all_fields_matched(),
            homez_current_values=self._all_fields_matched(),
        )

        self.service.assert_attributes_confirmed_or_block(self.company.id, "P8")

    def test_blocking_is_isolated_by_product_identifier(self):

        self.service.run_comparison(
            company_id=self.company.id, product_identifier="P9-BLOCKED",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )

        self.assertFalse(
            self.service.has_blocking_attribute_mismatch(
                self.company.id, "P9-OTHER",
            ),
        )

    def test_reresolving_run_lifts_the_block(self):

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P10",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )

        selections = {
            item.id: item.supplier_value for item in run.items
        }
        self.service.resolve_run(
            run.id, self.company.id, is_admin=True,
            resolved_by=self.admin.id,
            resolution_note="매입처 값을 기준으로 확정",
            selected_values=selections,
        )

        self.assertFalse(
            self.service.has_blocking_attribute_mismatch(self.company.id, "P10"),
        )

    def test_new_comparison_with_changed_content_after_resolution_blocks_again(self):
        """과거 비교가 해소됐어도, 같은 상품을 다시 비교했을 때 **비교 내용이
        바뀌어** 또 불일치가 나오면 새로 차단한다(과거 해소가 영구 면제를 주지
        않는다). 2026-10-05: 내용이 같은 재조회는 해소를 유지한다 — 아래
        test_same_content_requery_keeps_the_resolution 참고."""

        first = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P11",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )
        self.service.resolve_run(
            first.id, self.company.id, is_admin=True,
            resolved_by=self.admin.id, resolution_note="확인함",
            selected_values={i.id: "값" for i in first.items},
        )
        self.assertFalse(
            self.service.has_blocking_attribute_mismatch(self.company.id, "P11"),
        )

        self.service.run_comparison(
            company_id=self.company.id, product_identifier="P11",
            supplier_values=self._all_fields_matched(name="바뀐 상품명"),
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )
        self.assertTrue(
            self.service.has_blocking_attribute_mismatch(self.company.id, "P11"),
        )

    # ------------------------------
    # 해소(resolve) 가드
    # ------------------------------

    def test_resolve_requires_admin(self):

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P12",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )

        with self.assertRaises(ForbiddenException):
            self.service.resolve_run(
                run.id, self.company.id, is_admin=False,
                resolved_by=self.admin.id, resolution_note="확인함",
                selected_values={i.id: "값" for i in run.items},
            )

    def test_resolve_requires_non_blank_note(self):

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P13",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )

        with self.assertRaises(BadRequestException):
            self.service.resolve_run(
                run.id, self.company.id, is_admin=True,
                resolved_by=self.admin.id, resolution_note="   ",
                selected_values={i.id: "값" for i in run.items},
            )

    def test_resolve_requires_selection_for_every_unmatched_item(self):
        """서버가 세 값 중 하나를 임의로 기본 선택하지 않는다 — 불일치/
        확인불가 항목 전부에 선택값이 없으면 해소를 거부한다."""

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P14",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )

        with self.assertRaises(BadRequestException):
            self.service.resolve_run(
                run.id, self.company.id, is_admin=True,
                resolved_by=self.admin.id, resolution_note="확인함",
                selected_values={},
            )

    def test_resolve_passed_run_rejected(self):

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P15",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=self._all_fields_matched(),
            homez_current_values=self._all_fields_matched(),
        )

        with self.assertRaises(BadRequestException):
            self.service.resolve_run(
                run.id, self.company.id, is_admin=True,
                resolved_by=self.admin.id, resolution_note="확인함",
                selected_values={},
            )

    def test_resolve_already_resolved_run_rejected(self):

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P16",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )
        self.service.resolve_run(
            run.id, self.company.id, is_admin=True,
            resolved_by=self.admin.id, resolution_note="1차 처리",
            selected_values={i.id: "값" for i in run.items},
        )

        with self.assertRaises(BadRequestException):
            self.service.resolve_run(
                run.id, self.company.id, is_admin=True,
                resolved_by=self.admin.id, resolution_note="2차 처리",
                selected_values={i.id: "값2" for i in run.items},
            )

    def test_get_run_other_company_not_found(self):

        run = self.service.run_comparison(
            company_id=self.company.id, product_identifier="P17",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=self._all_fields_matched(),
            homez_current_values=self._all_fields_matched(),
        )

        with self.assertRaises(NotFoundException):
            self.service.get_run(run.id, self.company.id + 999)

    def test_list_runs_filters_by_status(self):

        self.service.run_comparison(
            company_id=self.company.id, product_identifier="P18-PASS",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=self._all_fields_matched(),
            homez_current_values=self._all_fields_matched(),
        )
        self.service.run_comparison(
            company_id=self.company.id, product_identifier="P18-BLOCK",
            supplier_values=self._all_fields_matched(),
            sales_channel_values=_blank(*ProductAttributeField.ALL),
            homez_current_values=_blank(*ProductAttributeField.ALL),
        )

        blocked = self.service.list_runs(
            self.company.id, status=ComparisonRunStatus.BLOCKED,
        )
        self.assertEqual(
            [r.product_identifier for r in blocked], ["P18-BLOCK"],
        )


if __name__ == "__main__":
    unittest.main()
