"""
=========================================================
Homez OS

File : tests/test_supplier_offer_provider.py

2026-10-23 — 상품 서칭 AI S3: 공급처 읽기 전용 어댑터 경계(`supplier/offer_provider.py`)를 고정한다. 외부 호출·DB 0.

  - 제공자는 읽기만 한다(공개 메서드가 get_offer뿐 — 주문·결제·취소·계정 메서드가 없다).
  - 지금은 FAKE·MANUAL만 있고, 실제 공급처(오너클랜·도매매)는 "아직 만들지 않았다"고 정직하게 거절한다. 모르는 코드는 거절한다.
  - 어댑터가 낸 값은 평가 규칙과 그대로 이어진다(모르는 값은 None → 보류).
=========================================================
"""

import re
import unittest
from decimal import Decimal
from pathlib import Path

from app.core.exceptions import BadRequestException
from app.domains.supplier import offer_provider as op
from app.domains.supplier import offer_rules as rules
from app.domains.supplier.offer_provider import FakeSupplierOfferProvider
from app.domains.supplier.offer_provider import ManualSupplierOfferProvider
from app.domains.supplier.offer_provider import SupplierOfferProvider
from app.domains.supplier.offer_provider import get_offer_provider
from app.domains.supplier.offer_rules import SupplierOffer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/domains/supplier/offer_provider.py").read_text(encoding="utf-8")


def sample():
    return {"A1": SupplierOffer("FAKE", "A1", "상품 A", Decimal("7000"), Decimal("3000"), Decimal("3000"), 1, True, True, False, True),
            "B2": SupplierOffer("FAKE", "B2", "상품 B")}


class ProviderTests(unittest.TestCase):

    def test_known_numbers_return_the_stored_offer_and_unknown_ones_return_none(self):
        provider = get_offer_provider("FAKE", sample())
        self.assertEqual(provider.get_offer("A1").unit_cost, Decimal("7000"))
        self.assertIsNone(provider.get_offer("ZZ"))
        self.assertEqual(provider.get_offer("A1"), sample()["A1"])
        self.assertEqual(get_offer_provider("MANUAL", sample()).get_offer(1 if False else "B2").title, "상품 B")
        self.assertIsNone(get_offer_provider("FAKE").get_offer("A1"))

    def test_numbers_are_matched_as_text(self):
        provider = get_offer_provider("MANUAL", {"123": SupplierOffer("MANUAL", "123", "숫자 번호")})
        self.assertIsNotNone(provider.get_offer(123))   # 숫자로 물어도 같은 번호

    def test_the_stored_mapping_is_copied(self):
        data = sample()
        provider = get_offer_provider("FAKE", data)
        data.clear()
        self.assertIsNotNone(provider.get_offer("A1"))

    def test_codes_and_classes(self):
        self.assertEqual((FakeSupplierOfferProvider.code, ManualSupplierOfferProvider.code), ("FAKE", "MANUAL"))
        self.assertIsInstance(get_offer_provider("FAKE"), SupplierOfferProvider)
        with self.assertRaises(TypeError):
            SupplierOfferProvider()    # 추상 클래스

    def test_real_suppliers_are_honestly_not_built_yet(self):
        self.assertEqual(op.PENDING_REAL_PROVIDERS, ("OWNERCLAN", "DOMEME"))
        for code in op.PENDING_REAL_PROVIDERS:
            with self.assertRaises(BadRequestException) as ctx:
                get_offer_provider(code)
            self.assertIn("아직 만들지 않았습니다", str(ctx.exception.detail))
        for code in ("ONCH", "", "fake", "Manual", "UNKNOWN"):
            with self.assertRaises(BadRequestException, msg=code):
                get_offer_provider(code)

    def test_providers_only_read(self):
        for cls in (SupplierOfferProvider, FakeSupplierOfferProvider, ManualSupplierOfferProvider):
            public = sorted(n for n in dir(cls) if not n.startswith("_") and callable(getattr(cls, n)))
            self.assertEqual(public, ["get_offer"], cls.__name__)
        for word in ("order", "pay", "cancel", "login", "password", "token", "refund"):
            self.assertNotRegex(SOURCE.split('"""', 2)[2].lower().replace("get_offer_provider", ""), r"def\s+\w*" + word)

    def test_no_network_database_or_file_access(self):
        imports = "\n".join(re.findall(r"^(?:from|import)\s+\S+", SOURCE, flags=re.MULTILINE))
        for forbidden in ("requests", "httpx", "urllib", "socket", "sqlalchemy", "subprocess", "pathlib", "os"):
            self.assertNotRegex(imports, r"\b" + forbidden + r"\b", forbidden)

    def test_the_provider_output_feeds_the_rules_and_unknowns_hold(self):
        provider = get_offer_provider("FAKE", sample())
        known = rules.evaluate_offer(provider.get_offer("A1"))
        self.assertEqual(known["decision"], "READY")
        self.assertEqual(known["floor_price"], 14451)
        unknown = rules.evaluate_offer(provider.get_offer("B2"))
        self.assertEqual(unknown["decision"], "HOLD")     # 값을 모르는 상품은 후보 가능이 되지 않는다
        self.assertIsNone(unknown["floor_price"])


if __name__ == "__main__":
    unittest.main()
