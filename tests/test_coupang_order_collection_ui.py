import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CoupangOrderCollectionUiContractTest(unittest.TestCase):
    def test_order_screen_uses_real_collection_endpoints(self):
        script = (ROOT / "app/web/console.js").read_text(encoding="utf-8-sig")
        self.assertIn('apiFetch("/orders/collect/coupang"', script)
        self.assertIn('apiFetch("/orders/collection-status")', script)
        self.assertIn('apiFetch("/orders/unresolved-items")', script)
        self.assertNotIn("Fake Provider 기준 데이터", script)

    def test_korean_labels_are_natural_and_user_facing(self):
        labels = (ROOT / "app/web/i18n/ko-KR.js").read_text(encoding="utf-8-sig")
        for expected in (
            '"ord.collection_heading": "쿠팡 주문 가져오기"',
            '"ord.collection_run": "주문 가져오기"',
            '"ord.collection_unresolved": "상품 연결 대기 품목"',
            '"ord.collection_last_success": "마지막 성공 시각"',
        ):
            self.assertIn(expected, labels)


if __name__ == "__main__":
    unittest.main()
