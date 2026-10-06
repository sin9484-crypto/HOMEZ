"""
=========================================================
Homez OS

File : tests/test_product_attribute_resolution_contract.py

2026-10-05 — 상품 속성 비교의 해소·재조회 계약 격리 테스트(임시 SQLite 파일
DB만 사용, 실제 homez.db·외부 API 없음).

  1) 해소는 차단 필드 6개만 요구한다(비차단 미확인은 남겨 둔다).
  2) 같은 비교 내용으로 다시 조회하면 이전 해소가 유지되고, 내용이 바뀌면
     새 검토 대상이다. 다른 회사·연결·상품의 해소는 쓰이지 않는다.
  3) source_reference 형식 문제로 기존 BLOCKED가 점검에서 빠지지 않는다.

DB는 클래스당 한 번만 만들고(부트스트랩이 느림) 테스트마다 상품 식별자를
새로 만들어 서로 섞이지 않게 한다.
=========================================================
"""

import itertools
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.database.bootstrap import bootstrap_environment
from app.domains.company.model import Company
from app.domains.product_attribute_match.constants import AttributeMatchStatus
from app.domains.product_attribute_match.constants import ProductAttributeField
from app.domains.product_attribute_match.constants import SupplierSourceCheck
from app.domains.product_attribute_match.service import ProductAttributeMatchService
from app.domains.product_attribute_match.service import (
    supplier_values_from_channel_lookup,
)
from app.domains.purchase_task.channel_adapter import ChannelProductOption
from app.domains.purchase_task.channel_adapter import ProductLookupResult
from app.domains.purchase_task.constants import CapabilitySupport
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"

F = ProductAttributeField
BLOCKING = ProductAttributeField.REQUIRED_FOR_BLOCKING
NON_BLOCKING = tuple(f for f in ProductAttributeField.ALL if f not in BLOCKING)


def _v(value, source="TEST"):
    return (value, source, None)


class ResolutionContractTestCase(unittest.TestCase):

    _counter = itertools.count(1)

    @classmethod
    def setUpClass(cls):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(path)
        cls.db_path = Path(path)
        cls.backups_dir = Path(tempfile.mkdtemp())
        bootstrap_environment(
            db_path=cls.db_path, migrations_dir=MIGRATIONS_DIR,
            backups_dir=cls.backups_dir,
        )
        cls.engine = create_engine(f"sqlite:///{cls.db_path}")
        cls.db = sessionmaker(bind=cls.engine)()

        def _company(name, number):
            row = Company(
                name=name, business_number=number, ceo="테스트",
                phone="02-000-0000", email=f"{number}@example.com", address="테스트",
            )
            cls.db.add(row)
            cls.db.commit()
            return row

        cls.company = _company("해소 계약 회사", "777-77-77771")
        cls.other_company = _company("다른 회사", "777-77-77772")
        cls.service = ProductAttributeMatchService(cls.db)

    @classmethod
    def tearDownClass(cls):

        cls.db.close()
        cls.engine.dispose()
        if cls.db_path.exists():
            cls.db_path.unlink()

    # ---- helpers ----

    def _code(self):
        return f"CH-{next(self._counter)}"

    def _connection(self, company=None, mall="ONCHANNEL"):
        row = PurchaseChannelConnection(
            company_id=(company or self.company).id, mall_code=mall,
            connection_method="CREDENTIAL", account_label="계약 테스트",
            status="CONNECTED",
        )
        self.db.add(row)
        self.db.commit()
        return row

    def _lookup_values(self, **overrides):
        """실제 조회처럼 상품명만 있고 나머지는 모르는 공급처 값. overrides로
        필드별 값을 바꾼다."""

        values = {F.NAME: _v("레이펄스 워터리스 샴푸/ 바디워시", "매입처 실제 조회")}
        for field, value in overrides.items():
            values[field] = _v(value, "매입처 실제 조회")
        return values

    def _run(self, code, conn, *, supplier=None, sales=None, homez=None, company=None):
        return self.service.run_comparison(
            company_id=(company or self.company).id, product_identifier=code,
            connection_id=conn.id if conn is not None else None,
            supplier_values=supplier if supplier is not None else self._lookup_values(),
            sales_channel_values=sales or {}, homez_current_values=homez or {},
        )

    def _resolve(self, run, company=None, note="사람이 확인함", value="확인값", **per_item):
        """차단 필드의 미일치·미확인 항목마다 선택값을 준다(per_item은 필드명→값)."""

        selections = {}
        for item in run.items:
            if item.match_status != AttributeMatchStatus.MATCHED and item.is_blocking_field:
                selections[item.id] = per_item.get(item.field_name, value)
        return self.service.resolve_run(
            run.id, (company or self.company).id, is_admin=True, resolved_by=1,
            resolution_note=note, selected_values=selections,
        )

    def _blocking(self, code, company=None):
        return self.service.has_blocking_attribute_mismatch(
            (company or self.company).id, code,
        )

    def _supplier_check(self, ref, company=None):
        return self.service.check_supplier_source((company or self.company).id, ref)

    # ------------------------------------------------------------
    # 1) 해소 계약: 차단 필드만 요구한다
    # ------------------------------------------------------------

    def test_blocking_fields_are_the_six_defined_in_the_contract(self):

        self.assertEqual(
            set(BLOCKING),
            {F.NAME, F.OPTIONS, F.QUANTITY, F.SIZE, F.MANUFACTURER, F.ORIGIN_COUNTRY},
        )
        self.assertEqual(set(NON_BLOCKING), {F.MODEL_NAME, F.CERTIFICATION_IDENTIFIERS})

    def test_non_blocking_unconfirmed_items_do_not_prevent_resolution(self):

        code, conn = self._code(), self._connection()
        run = self._run(code, conn)
        non_blocking_items = [i for i in run.items if not i.is_blocking_field]
        self.assertEqual(
            {i.field_name for i in non_blocking_items}, set(NON_BLOCKING),
        )

        resolved = self._resolve(run)  # 차단 필드 6개에만 선택값을 준다

        self.assertIsNotNone(resolved.resolved_at)
        self.assertFalse(self._blocking(code))
        for item in resolved.items:
            if not item.is_blocking_field:
                # 미확인으로 남고, 값을 요구하거나 만들어 넣지 않는다.
                self.assertEqual(item.match_status, AttributeMatchStatus.UNCONFIRMED)
                self.assertIsNone(item.selected_value)
                self.assertIsNone(item.supplier_value)
        self.assertEqual(
            set(self.service.describe_run(resolved)["non_blocking_unconfirmed_fields"]),
            set(NON_BLOCKING),
        )

    def test_blocking_item_without_a_selection_keeps_blocking(self):
        """차단 필드의 근거 부족(구성수량 없음)은 해소되지 않고 계속 차단한다."""

        code, conn = self._code(), self._connection()
        run = self._run(code, conn)
        selections = {
            i.id: "확인값" for i in run.items
            if i.is_blocking_field and i.field_name != F.QUANTITY
        }
        with self.assertRaises(BadRequestException):
            self.service.resolve_run(
                run.id, self.company.id, is_admin=True, resolved_by=1,
                resolution_note="수량 빼고 해소 시도", selected_values=selections,
            )
        self.db.refresh(run)
        self.assertIsNone(run.resolved_at)
        self.assertTrue(self._blocking(code))

    def test_blank_selection_for_a_blocking_item_is_rejected(self):

        code, conn = self._code(), self._connection()
        run = self._run(code, conn)
        selections = {i.id: "확인값" for i in run.items if i.is_blocking_field}
        quantity = next(i for i in run.items if i.field_name == F.QUANTITY)
        for blank in ("", "   "):
            selections[quantity.id] = blank
            with self.assertRaises(BadRequestException):
                self.service.resolve_run(
                    run.id, self.company.id, is_admin=True, resolved_by=1,
                    resolution_note="빈 값", selected_values=selections,
                )
        self.assertTrue(self._blocking(code))

    def test_unconfirmed_placeholders_cannot_resolve_a_blocking_item(self):
        """"미확인"·"미제공" 같은 값은 확인한 값이 아니라 해소를 통과시키는 우회다."""

        code, conn = self._code(), self._connection()
        run = self._run(code, conn)
        quantity = next(i for i in run.items if i.field_name == F.QUANTITY)
        for placeholder in ("미확인", "미제공", "N/A", " - ", "Unknown", "알 수 없음"):
            selections = {i.id: "확인값" for i in run.items if i.is_blocking_field}
            selections[quantity.id] = placeholder
            with self.assertRaises(BadRequestException, msg=placeholder):
                self.service.resolve_run(
                    run.id, self.company.id, is_admin=True, resolved_by=1,
                    resolution_note="자리표시자", selected_values=selections,
                )
        self.assertTrue(self._blocking(code))

    def test_certification_not_applicable_unavailable_and_empty_are_three_different_things(self):
        """"인증 대상 아님"(내용이 있는 값)·"미제공"(확인 못 함)·빈 문자열(없음)은
        같은 값으로 처리하지 않는다."""

        # 비교에서: 내용이 있는 값은 값으로 남고, 빈 문자열은 값이 없음이다.
        code, conn = self._code(), self._connection()
        run = self._run(code, conn, supplier={
            F.CERTIFICATION_IDENTIFIERS: _v("인증대상 아님"),
        }, homez={F.CERTIFICATION_IDENTIFIERS: _v("미제공")})
        cert = next(i for i in run.items if i.field_name == F.CERTIFICATION_IDENTIFIERS)
        self.assertEqual(cert.supplier_value, "인증대상 아님")
        self.assertEqual(cert.homez_current_value, "미제공")
        self.assertEqual(cert.match_status, AttributeMatchStatus.MISMATCHED)

        empty = self._run(self._code(), conn, supplier={
            F.CERTIFICATION_IDENTIFIERS: _v(""),
        })
        empty_cert = next(i for i in empty.items if i.field_name == F.CERTIFICATION_IDENTIFIERS)
        self.assertEqual(empty_cert.match_status, AttributeMatchStatus.UNCONFIRMED)

        # 해소에서: 실제 내용을 말하는 값은 선택값이 될 수 있고(없음·인증대상 아님),
        # 자리표시자와 빈 값은 될 수 없다.
        code2, conn2 = self._code(), self._connection()
        run2 = self._run(code2, conn2)
        resolved = self._resolve(run2, **{
            F.MANUFACTURER: "인증 대상 아님",   # 내용이 있는 값 — 허용(사람이 정한 값)
            F.OPTIONS: "없음",
        })
        self.assertIsNotNone(resolved.resolved_at)

    def test_non_blocking_value_given_by_a_human_is_recorded_without_changing_the_judgement(self):

        code, conn = self._code(), self._connection()
        run = self._run(code, conn)
        selections = {i.id: "확인값" for i in run.items if i.is_blocking_field}
        cert = next(i for i in run.items if i.field_name == F.CERTIFICATION_IDENTIFIERS)
        selections[cert.id] = "KC 인증대상 아님(공급처 기재)"
        resolved = self.service.resolve_run(
            run.id, self.company.id, is_admin=True, resolved_by=1,
            resolution_note="비차단 항목도 기록", selected_values=selections,
        )
        cert = next(i for i in resolved.items if i.field_name == F.CERTIFICATION_IDENTIFIERS)
        self.assertEqual(cert.selected_value, "KC 인증대상 아님(공급처 기재)")
        self.assertEqual(cert.match_status, AttributeMatchStatus.UNCONFIRMED)

    def test_placeholder_is_rejected_for_a_non_blocking_item_too(self):

        code, conn = self._code(), self._connection()
        run = self._run(code, conn)
        selections = {i.id: "확인값" for i in run.items if i.is_blocking_field}
        model_name = next(i for i in run.items if i.field_name == F.MODEL_NAME)
        selections[model_name.id] = "미확인"
        with self.assertRaises(BadRequestException):
            self.service.resolve_run(
                run.id, self.company.id, is_admin=True, resolved_by=1,
                resolution_note="가짜 모델명", selected_values=selections,
            )

    def test_missing_model_name_is_never_generated(self):

        code, conn = self._code(), self._connection()
        run = self._run(code, conn, supplier=self._lookup_values(), homez={
            F.NAME: _v("HOMEZ 상품명"),
        })
        model_name = next(i for i in run.items if i.field_name == F.MODEL_NAME)
        self.assertIsNone(model_name.supplier_value)
        self.assertIsNone(model_name.homez_current_value)
        self.assertIsNone(model_name.selected_value)

    def test_quantity_is_not_derived_from_unit_count_or_option_quantity(self):
        """구성수량은 주문 수량·unitCount·옵션 수량과 다른 값이다 — 비교는 공급처가
        구성수량으로 확인해 준 값(package_quantity)만 받고, 없으면 비어 있다."""

        result = ProductLookupResult(
            support=CapabilitySupport.SUPPORTED, external_product_id="CH-Q",
            title="수량 테스트", detail="d",
            options=(
                ChannelProductOption(
                    option_id="1", label="본품 200ml x 1", price=None, in_stock=True,
                ),
                ChannelProductOption(
                    option_id="2", label="500ml 리필 2개", price=None, in_stock=True,
                ),
            ),
        )
        values = supplier_values_from_channel_lookup(result, source_label="t")
        self.assertEqual(values[F.QUANTITY], (None, None, None))

        # HOMEZ 쪽에 다른 필드(옵션·사이즈)가 있어도 수량 칸을 채우지 않는다.
        code, conn = self._code(), self._connection()
        run = self._run(code, conn, supplier=values, homez={
            F.OPTIONS: _v("용량: 200ml"), F.SIZE: _v("200ml"),
        })
        quantity = next(i for i in run.items if i.field_name == F.QUANTITY)
        self.assertIsNone(quantity.supplier_value)
        self.assertIsNone(quantity.homez_current_value)
        self.assertEqual(quantity.match_status, AttributeMatchStatus.UNCONFIRMED)

    # ------------------------------------------------------------
    # 2) 재조회: 같은 비교 내용이면 해소 유지, 바뀌면 재검토
    # ------------------------------------------------------------

    def test_same_content_requery_keeps_the_resolution_and_never_overwrites_history(self):

        code, conn = self._code(), self._connection()
        first = self._resolve(self._run(code, conn))
        first_snapshot = (
            first.id, first.resolved_by, first.resolved_at, first.resolution_note,
            sorted((i.id, i.selected_value) for i in first.items),
        )
        self.assertFalse(self._blocking(code))

        # 같은 내용을 두 번 더 조회(출처 라벨·확인 시각은 달라도 내용은 같다)
        second = self._run(code, conn, supplier=self._lookup_values())
        third = self._run(code, conn, supplier=self._lookup_values())

        self.assertFalse(self._blocking(code), "같은 비교 내용이면 해소가 유지돼야 한다")
        # 새 run은 해소된 것으로 바뀌지 않는다(미해소로 남고, 이전 해소로 덮일 뿐).
        self.assertIsNone(second.resolved_at)
        self.assertEqual(second.overall_status, "BLOCKED")
        self.assertEqual(self.service.describe_run(second)["covered_by_run_id"], first.id)
        self.assertEqual(self.service.describe_run(third)["covered_by_run_id"], first.id)
        self.assertFalse(self.service.describe_run(third)["blocking_active"])
        # 과거 run·해소자·근거·시각·선택값은 그대로다.
        self.db.refresh(first)
        self.assertEqual(
            first_snapshot,
            (
                first.id, first.resolved_by, first.resolved_at, first.resolution_note,
                sorted((i.id, i.selected_value) for i in first.items),
            ),
        )

    def test_the_supplier_name_matching_alone_does_not_reuse_the_resolution(self):
        """공급처 값 하나(상품명)가 같다는 이유만으로 해소를 재사용하지 않는다 —
        공급처가 제조사를 새로 알려 주면 검토 대상이다."""

        code, conn = self._code(), self._connection()
        self._resolve(self._run(code, conn))
        self.assertFalse(self._blocking(code))

        self._run(code, conn, supplier=self._lookup_values(**{F.MANUFACTURER: "유레카코스"}))
        self.assertTrue(self._blocking(code))

    def test_changed_supplier_value_requires_new_review(self):

        code, conn = self._code(), self._connection()
        self._resolve(self._run(code, conn))
        self._run(code, conn, supplier={
            F.NAME: _v("공급처가 바꾼 상품명", "매입처 실제 조회"),
        })
        self.assertTrue(self._blocking(code))

    def test_changed_homez_value_requires_new_review(self):

        code, conn = self._code(), self._connection()
        self._resolve(self._run(code, conn, homez={F.NAME: _v("HOMEZ 이름 A")}))
        self.assertFalse(self._blocking(code))

        self._run(code, conn, homez={F.NAME: _v("HOMEZ 이름 A")})
        self.assertFalse(self._blocking(code), "HOMEZ 값이 그대로면 유지")

        self._run(code, conn, homez={F.NAME: _v("HOMEZ 이름 B")})
        self.assertTrue(self._blocking(code), "HOMEZ 값이 바뀌면 재검토")

    def test_changed_option_or_sales_channel_value_requires_new_review(self):

        code, conn = self._code(), self._connection()
        self._resolve(self._run(code, conn, supplier=self._lookup_values(**{
            F.OPTIONS: "3945580 바디워시 200ml (본품)",
        })))
        self.assertFalse(self._blocking(code))

        self._run(code, conn, supplier=self._lookup_values(**{
            F.OPTIONS: "3945581 바디워시 500ml (리필)",
        }))
        self.assertTrue(self._blocking(code), "옵션이 바뀌면 재검토")

        # 판매채널 값이 새로 생긴 경우도 재검토
        code2, conn2 = self._code(), self._connection()
        self._resolve(self._run(code2, conn2))
        self.assertFalse(self._blocking(code2))
        self._run(code2, conn2, sales={F.SIZE: _v("200ml", "쿠팡")})
        self.assertTrue(self._blocking(code2))

    def test_going_back_to_old_content_after_a_change_is_not_covered(self):
        """내용이 중간에 바뀌었다면 이후 같은 내용으로 돌아와도 이어 쓰지 않는다
        (해소는 바뀌지 않은 구간에만 유효)."""

        code, conn = self._code(), self._connection()
        self._resolve(self._run(code, conn))
        self._run(code, conn, supplier={F.NAME: _v("바뀐 이름", "x")})
        self.assertTrue(self._blocking(code))
        self._run(code, conn)  # 처음 내용으로 복귀
        self.assertTrue(self._blocking(code))

    def test_non_blocking_field_change_does_not_reopen_the_review(self):
        """모델명 같은 비차단 필드는 해소 판단에 쓰이지 않으므로 그 변화만으로
        재검토하지 않는다."""

        code, conn = self._code(), self._connection()
        self._resolve(self._run(code, conn))
        self._run(code, conn, supplier=self._lookup_values(**{F.MODEL_NAME: "RP-200"}))
        self.assertFalse(self._blocking(code))

    def test_resolution_is_not_reused_across_company_connection_or_product(self):

        code, conn = self._code(), self._connection()
        self._resolve(self._run(code, conn))
        self.assertFalse(self._blocking(code))

        # 다른 회사: 같은 코드·같은 내용이어도 이 회사의 해소를 쓰지 못한다.
        other_conn = self._connection(self.other_company)
        self._run(code, other_conn, company=self.other_company)
        self.assertTrue(self._blocking(code, company=self.other_company))

        # 같은 회사의 다른 매입처 연결
        another_conn = self._connection()
        run_other_conn = self._run(code, another_conn)
        self.assertIsNone(self.service.describe_run(run_other_conn)["covered_by_run_id"])
        self.assertTrue(
            self.service.check_supplier_source(self.company.id, f"ONCHANNEL:{code}")
            == SupplierSourceCheck.BLOCKED,
            "해소되지 않은 다른 연결의 기록이 있으면 차단",
        )

        # 다른 상품 코드
        other_code = self._code()
        self._run(other_code, conn)
        self.assertTrue(self._blocking(other_code))

    def test_a_rule_that_grew_does_not_inherit_an_incomplete_resolution(self):
        """차단 필드 목록이 늘어 새 필드(모델명)가 선택값이 필요한 항목이 됐다면,
        그 필드를 확인한 적 없는 과거 해소를 이어 쓰지 않는다."""

        code, conn = self._code(), self._connection()
        self._resolve(self._run(code, conn))
        self._run(code, conn)
        self.assertFalse(self._blocking(code))

        with mock.patch.object(
            ProductAttributeField, "REQUIRED_FOR_BLOCKING", BLOCKING + (F.MODEL_NAME,),
        ):
            self.assertTrue(self._blocking(code))

    def test_resolved_run_that_never_recorded_selections_is_not_inherited(self):
        """선택값 없이 resolved_at만 채워진 기록(직접 수정 등)은 새 run으로 이어
        쓰지 않는다 — fail-closed."""

        from datetime import datetime

        code, conn = self._code(), self._connection()
        run = self._run(code, conn)
        run.resolved_at = datetime.utcnow()
        run.resolved_by = 1
        self.db.commit()
        self.assertFalse(self._blocking(code))  # 이 run 자체는 해소된 것으로 본다
        self._run(code, conn)
        self.assertTrue(self._blocking(code))

    # ------------------------------------------------------------
    # 3) 식별자 경계
    # ------------------------------------------------------------

    def test_parse_allows_only_whitespace_and_mall_code_case_normalization(self):

        parse = ProductAttributeMatchService.parse_supplier_source_reference
        self.assertEqual(parse("ONCHANNEL:CH1147184"), ("ONCHANNEL", "CH1147184"))
        self.assertEqual(parse("onchannel:CH1147184"), ("ONCHANNEL", "CH1147184"))
        self.assertEqual(parse(" OnChannel : CH1147184 "), ("ONCHANNEL", "CH1147184"))
        # 상품 코드의 대소문자는 그대로 둔다.
        self.assertEqual(parse("ONCHANNEL:ch1147184"), ("ONCHANNEL", "ch1147184"))
        # 알려진 매입처가 아니면 정규화하지 않는다.
        self.assertEqual(parse("foo:Bar"), ("foo", "Bar"))
        for unclear in (None, "", "ONCHANNEL", ":CH1", "ONCHANNEL:", "  :  "):
            self.assertIsNone(parse(unclear), unclear)

    def test_mall_code_case_or_spacing_does_not_hide_an_existing_blocked_run(self):

        code, conn = self._code(), self._connection()
        self._run(code, conn)
        for ref in (
            f"ONCHANNEL:{code}", f"onchannel:{code}", f" OnChannel : {code} ",
        ):
            self.assertEqual(self._supplier_check(ref), SupplierSourceCheck.BLOCKED, ref)

    def test_product_code_case_is_not_folded_but_the_ambiguity_is_reported(self):

        code, conn = self._code(), self._connection()
        run = self._run(code, conn)  # 예: CH-12 의 미해소 BLOCKED
        lowered = f"ONCHANNEL:{code.lower()}"
        self.assertNotEqual(code, code.lower())

        # 정확히 같은 코드가 아니므로 같은 상품으로 합치지 않는다 — 그렇다고 통과도 아님.
        self.assertEqual(self._supplier_check(lowered), SupplierSourceCheck.IDENTIFIER_AMBIGUOUS)
        self.assertEqual(
            self._supplier_check(f"ONCHANNEL:{code}"), SupplierSourceCheck.BLOCKED,
        )

        # 해소되면 모호함도 사라진다.
        self._resolve(run)
        self.assertEqual(self._supplier_check(lowered), SupplierSourceCheck.CLEAR)

    def test_supplier_reference_that_cannot_be_parsed_is_not_treated_as_no_record(self):

        for unreadable in (
            "ONCHANNEL", "ONCHANNEL:", "onchannel", "ONCHANNEL CH1147184",
            "ONCHANNEL/CH1147184", "ONCHANNEL-CH1147184", "naver_shopping",
        ):
            self.assertEqual(
                self._supplier_check(unreadable),
                SupplierSourceCheck.IDENTIFIER_UNREADABLE, unreadable,
            )

    def test_reference_that_is_not_a_supplier_target_is_not_checked(self):
        """원래 공급처 대상이 아닌 후보 — 점검 대상이 아니다(막지도 않는다)."""

        for other in (
            None, "", "   ", "no-supplier-identity", "CH1147184", "https://example.com/x",
            "Other brand sample", "trend:keyword",
        ):
            self.assertEqual(
                self._supplier_check(other), SupplierSourceCheck.NOT_APPLICABLE, other,
            )

    def test_identifier_check_does_not_widen_across_company_connection_or_mall(self):

        code = self._code()
        other_conn = self._connection(self.other_company)
        self._run(code, other_conn, company=self.other_company)  # 다른 회사의 BLOCKED
        self._connection()  # 이 회사의 연결은 있지만 기록은 없음
        self.assertEqual(self._supplier_check(f"ONCHANNEL:{code}"), SupplierSourceCheck.CLEAR)
        self.assertEqual(
            self._supplier_check(f"ONCHANNEL:{code}", company=self.other_company),
            SupplierSourceCheck.BLOCKED,
        )

        # 연결에 묶이지 않은 기록
        code2 = self._code()
        self._run(code2, None)
        self.assertEqual(self._supplier_check(f"ONCHANNEL:{code2}"), SupplierSourceCheck.CLEAR)

        # 다른 매입처의 같은 코드
        code3 = self._code()
        naver = self._connection(mall="NAVER_SHOPPING")
        self._run(code3, naver)
        self.assertEqual(self._supplier_check(f"ONCHANNEL:{code3}"), SupplierSourceCheck.CLEAR)
        self.assertEqual(
            self._supplier_check(f"naver_shopping:{code3}"), SupplierSourceCheck.BLOCKED,
        )

    def test_case_variant_of_another_company_or_mall_is_not_reported(self):

        code = self._code()
        other_conn = self._connection(self.other_company)
        self._run(code.lower(), other_conn, company=self.other_company)
        self._connection()
        self.assertEqual(self._supplier_check(f"ONCHANNEL:{code}"), SupplierSourceCheck.CLEAR)

    # ------------------------------------------------------------
    # API 응답: 차단 여부·이전 해소 적용·비차단 미확인이 해소 여부와 별도로 표시된다
    # ------------------------------------------------------------

    def test_api_response_separates_blocking_coverage_and_non_blocking_unconfirmed(self):

        from app.domains.product_attribute_match.router import _present

        code, conn = self._code(), self._connection()
        first = self._resolve(self._run(code, conn))
        second = self._run(code, conn)

        shown_first = _present(self.service, first)
        shown_second = _present(self.service, second)

        self.assertIsNotNone(shown_first.resolved_at)
        self.assertEqual(
            {i.field_name: i.is_blocking_field for i in shown_first.items},
            {f: f in BLOCKING for f in ProductAttributeField.ALL},
        )
        self.assertEqual(
            set(shown_first.non_blocking_unconfirmed_fields), set(NON_BLOCKING),
        )
        # 두 번째 run은 미해소로 남지만 이전 해소로 덮이고, 지금은 차단 중이 아니다.
        self.assertIsNone(shown_second.resolved_at)
        self.assertEqual(shown_second.covered_by_run_id, first.id)
        self.assertFalse(shown_second.blocking_active)
        self.assertEqual(
            set(shown_second.non_blocking_unconfirmed_fields), set(NON_BLOCKING),
        )

        changed = _present(self.service, self._run(code, conn, supplier={
            F.NAME: _v("바뀐 이름", "x"),
        }))
        self.assertIsNone(changed.covered_by_run_id)
        self.assertTrue(changed.blocking_active)

    # ------------------------------------------------------------
    # 해소와 실제 등록 내용의 연결(2026-10-05)
    # ------------------------------------------------------------

    REGISTRATION = {
        F.NAME: "레이펄스 워터리스 바디워시 200ml (본품)",
        F.OPTIONS: "기본형 [PC3-CH1147184-3945580]",
        F.QUANTITY: None,
        F.SIZE: "개당 용량=200",
        F.MANUFACTURER: "제조업자=유레카코스",
        F.ORIGIN_COUNTRY: "제조국=대한민국",
    }

    def _registration_run(self, code, conn, values=None, **supplier):
        """공급처 조회 값 + 등록 내용을 한 번에 담은 run(record_registration_comparison과 같은 모양)."""

        registration = dict(self.REGISTRATION if values is None else values)
        return self._run(
            code, conn, supplier=self._lookup_values(**supplier),
            homez={f: _v(v, "위저드 최종 등록 내용") for f, v in registration.items() if v is not None},
        )

    def _binding(self, code, current=None):
        return self.service.check_registration_binding(
            self.company.id, f"ONCHANNEL:{code}",
            dict(self.REGISTRATION if current is None else current),
        )

    def test_resolving_a_lookup_only_run_does_not_cover_the_registration_content(self):

        code, conn = self._code(), self._connection()
        self._resolve(self._run(code, conn))  # 공급처 조회만 한 run을 해소
        self.assertFalse(self._blocking(code))
        outcome, fields = self._binding(code)
        self.assertEqual(outcome, "UNBOUND")
        # 현재 등록 내용에 값이 있는 차단 필드는 모두 검토되지 않은 것이다(수량은 값이 없음).
        self.assertEqual(
            set(fields), {F.NAME, F.OPTIONS, F.SIZE, F.MANUFACTURER, F.ORIGIN_COUNTRY},
        )

    def test_resolved_registration_run_binds_to_the_same_content_only(self):

        code, conn = self._code(), self._connection()
        self._resolve(self._registration_run(code, conn))
        self.assertEqual(self._binding(code), ("BOUND", []))

        # 공백·대소문자만 다른 값은 같은 값(비교와 같은 정규화)
        spaced = dict(self.REGISTRATION)
        spaced[F.NAME] = "  레이펄스   워터리스 바디워시 200ML (본품) "
        self.assertEqual(self._binding(code, spaced), ("BOUND", []))

        for field in (F.NAME, F.OPTIONS, F.SIZE, F.MANUFACTURER, F.ORIGIN_COUNTRY):
            changed = dict(self.REGISTRATION)
            changed[field] = "해소 뒤에 바뀐 값"
            self.assertEqual(self._binding(code, changed), ("UNBOUND", [field]), field)

        # 구성수량: 해소 때 없던 값이 새로 생기면 검토 필요
        with_quantity = dict(self.REGISTRATION)
        with_quantity[F.QUANTITY] = "수량=1"
        self.assertEqual(self._binding(code, with_quantity), ("UNBOUND", [F.QUANTITY]))

    def test_same_content_lookup_after_a_registration_resolution_keeps_it_bound(self):

        code, conn = self._code(), self._connection()
        first = self._resolve(self._registration_run(code, conn))
        again = self._run(code, conn)  # 공급처 조회만 한 새 run(같은 공급처 값)
        self.assertEqual(self.service.describe_run(again)["covered_by_run_id"], first.id)
        self.assertEqual(self._binding(code), ("BOUND", []))
        # 등록 내용을 같은 값으로 다시 비교해도 유지
        same = self._registration_run(code, conn)
        self.assertEqual(self.service.describe_run(same)["covered_by_run_id"], first.id)

    def test_changed_registration_content_or_supplier_value_needs_a_new_resolution(self):

        code, conn = self._code(), self._connection()
        self._resolve(self._registration_run(code, conn))
        changed = dict(self.REGISTRATION)
        changed[F.NAME] = "다른 상품명"
        new_run = self._registration_run(code, conn, values=changed)
        self.assertTrue(self._blocking(code), "등록 내용이 바뀐 새 비교는 새 해소가 필요하다")
        self._resolve(new_run)
        self.assertEqual(self._binding(code, changed), ("BOUND", []))
        self.assertEqual(self._binding(code)[0], "UNBOUND")  # 이전 등록 내용은 더 이상 근거가 아님

        # 공급처 값이 바뀌어도 새 검토
        self._registration_run(code, conn, values=changed, **{F.MANUFACTURER: "유레카코스"})
        self.assertTrue(self._blocking(code))

    def test_binding_check_never_writes(self):

        from app.domains.product_attribute_match.model import ProductAttributeComparisonRun

        code, conn = self._code(), self._connection()
        resolved = self._resolve(self._run(code, conn))
        self._run(code, conn)
        before = self.db.query(ProductAttributeComparisonRun).count()
        snapshot = (resolved.resolved_by, resolved.resolved_at, resolved.resolution_note)
        for _ in range(3):
            self._binding(code)
            self._supplier_check(f"ONCHANNEL:{code}")
        self.db.refresh(resolved)
        self.assertEqual(self.db.query(ProductAttributeComparisonRun).count(), before)
        self.assertEqual(snapshot, (resolved.resolved_by, resolved.resolved_at, resolved.resolution_note))

    def test_binding_is_not_applicable_without_a_resolved_basis_and_not_shared_across_scope(self):

        code, conn = self._code(), self._connection()
        self.assertEqual(self._binding(code)[0], "NOT_APPLICABLE")  # 기록 없음
        self._run(code, conn)  # 미해소 BLOCKED뿐 — 근거가 되는 해소 없음
        self.assertEqual(self._binding(code)[0], "NOT_APPLICABLE")

        # 다른 회사의 해소·다른 매입처의 해소는 근거가 아니다.
        other_conn = self._connection(self.other_company)
        self._resolve(self._run(code, other_conn, company=self.other_company), company=self.other_company)
        self.assertEqual(self._binding(code)[0], "NOT_APPLICABLE")
        naver = self._connection(mall="NAVER_SHOPPING")
        self._resolve(self._registration_run(code, naver))
        self.assertEqual(self._binding(code)[0], "NOT_APPLICABLE")

    def test_record_registration_comparison_copies_stored_supplier_values_without_inventing(self):

        code, conn = self._code(), self._connection()
        lookup = self._run(code, conn, supplier=self._lookup_values(**{F.MANUFACTURER: "유레카코스"}))
        recorded = self.service.record_registration_comparison(
            self.company.id, f"onchannel:{code}",
            {**self.REGISTRATION, F.QUANTITY: None}, triggered_by=7,
        )
        self.assertNotEqual(recorded.id, lookup.id)
        self.assertEqual(recorded.connection_id, conn.id)
        self.assertEqual(recorded.triggered_by, 7)
        values = {i.field_name: i for i in recorded.items}
        self.assertEqual(values[F.MANUFACTURER].supplier_value, "유레카코스")
        self.assertEqual(values[F.NAME].homez_current_value, self.REGISTRATION[F.NAME])
        self.assertIsNone(values[F.QUANTITY].homez_current_value)  # 근거 없는 값을 만들지 않는다
        self.assertIsNone(values[F.QUANTITY].supplier_value)
        # 과거 조회 run은 그대로다.
        self.db.refresh(lookup)
        self.assertIsNone(lookup.resolved_at)
        self.assertEqual(
            {i.field_name: i.homez_current_value for i in lookup.items if i.homez_current_value}, {},
        )


if __name__ == "__main__":
    unittest.main()
