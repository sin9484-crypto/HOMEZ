"""
HOMEZ AI Commerce OS
Build 06

Product Policy

상품 비즈니스 규칙
"""


class ProductPolicy:

    MIN_MARGIN_RATE = 10.0

    @staticmethod
    def is_margin_valid(margin_rate: float) -> bool:
        """최소 마진율 검사"""
        return margin_rate >= ProductPolicy.MIN_MARGIN_RATE

    @staticmethod
    def can_register(is_supplier_active: bool) -> bool:
        """공급처 활성 여부"""
        return is_supplier_active

    @staticmethod
    def can_upload(ai_score: float) -> bool:
        """AI 추천 점수"""
        return ai_score >= 70