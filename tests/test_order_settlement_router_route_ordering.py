"""
=========================================================
Homez OS

File : tests/test_order_settlement_router_route_ordering.py

Gate AI-F3(2026-08-22) — 실제 Browser E2E("주문 예외 검토"/"정산 차이
검토" 신규 화면) 도중 발견한 실제 라우팅 결함의 회귀 테스트.

`GET /orders/exception-analysis`가 `GET /orders/{order_id}`보다,
`GET /settlements/difference-analysis`가 `GET /settlements/
{settlement_id}`보다 각각 router에 나중에 등록돼 있었다 —
FastAPI/Starlette는 라우트를 등록 순서대로 매칭하므로, 두 요청 모두
동적 경로에 문자열이 정수로 잘못 매칭되어 422(정수 파싱 실패)로 항상
깨져 있었다(단위 테스트는 라우터 함수를 직접 호출해 이 결함을 잡지
못했다 — 실제 HTTP 라우팅 경로를 거치는 검증이 필요했던 이유,
tests/test_pricing_router_route_ordering.py의 V7 Gate 7 결함과 동일
클래스).

order/router.py·settlement/router.py에서 각각 exception-analysis·
difference-analysis 엔드포인트를 동적 경로보다 먼저 등록하도록
옮겨 고쳤다 — 이 테스트는 그 등록 순서를 고정한다.
=========================================================
"""

import unittest


class OrderExceptionAnalysisRouteOrderingTestCase(unittest.TestCase):

    def test_exception_analysis_routes_registered_before_order_id_route(self):

        import app.main

        order_routes = [
            r for r in app.main.app.routes
            if hasattr(r, "path") and r.path.startswith("/orders")
        ]

        exception_analysis_index = None
        review_actions_index = None
        order_id_index = None

        for i, r in enumerate(order_routes):
            if r.path == "/orders/exception-analysis" and "GET" in r.methods:
                exception_analysis_index = i
            if (
                r.path == "/orders/exception-analysis/review-actions"
                and "POST" in r.methods
            ):
                review_actions_index = i
            if r.path == "/orders/{order_id}" and "GET" in r.methods:
                order_id_index = i

        self.assertIsNotNone(
            exception_analysis_index,
            "GET /orders/exception-analysis 라우트를 찾을 수 없습니다.",
        )
        self.assertIsNotNone(
            review_actions_index,
            "POST /orders/exception-analysis/review-actions 라우트를 "
            "찾을 수 없습니다.",
        )
        self.assertIsNotNone(
            order_id_index,
            "GET /orders/{order_id} 라우트를 찾을 수 없습니다.",
        )
        self.assertLess(
            exception_analysis_index, order_id_index,
            "GET /orders/exception-analysis가 GET /orders/{order_id}보다 "
            "먼저 등록돼야 한다 — 그렇지 않으면 \"exception-analysis\"가 "
            "order_id로 잘못 매칭돼 422가 발생한다(2026-08-22 Gate "
            "AI-F3 Browser E2E에서 실제로 재현된 결함).",
        )


class SettlementDifferenceAnalysisRouteOrderingTestCase(unittest.TestCase):

    def test_difference_analysis_routes_registered_before_settlement_id_route(self):

        import app.main

        settlement_routes = [
            r for r in app.main.app.routes
            if hasattr(r, "path") and r.path.startswith("/settlements")
        ]

        difference_analysis_index = None
        review_actions_index = None
        settlement_id_index = None

        for i, r in enumerate(settlement_routes):
            if r.path == "/settlements/difference-analysis" and "GET" in r.methods:
                difference_analysis_index = i
            if (
                r.path == "/settlements/difference-analysis/review-actions"
                and "POST" in r.methods
            ):
                review_actions_index = i
            if r.path == "/settlements/{settlement_id}" and "GET" in r.methods:
                settlement_id_index = i

        self.assertIsNotNone(
            difference_analysis_index,
            "GET /settlements/difference-analysis 라우트를 찾을 수 "
            "없습니다.",
        )
        self.assertIsNotNone(
            review_actions_index,
            "POST /settlements/difference-analysis/review-actions "
            "라우트를 찾을 수 없습니다.",
        )
        self.assertIsNotNone(
            settlement_id_index,
            "GET /settlements/{settlement_id} 라우트를 찾을 수 "
            "없습니다.",
        )
        self.assertLess(
            difference_analysis_index, settlement_id_index,
            "GET /settlements/difference-analysis가 GET /settlements/"
            "{settlement_id}보다 먼저 등록돼야 한다 — 그렇지 않으면 "
            "\"difference-analysis\"가 settlement_id로 잘못 매칭돼 "
            "422가 발생한다(2026-08-22 Gate AI-F3 Browser E2E에서 "
            "실제로 재현된 결함).",
        )


if __name__ == "__main__":
    unittest.main()
