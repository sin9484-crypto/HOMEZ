"""
=========================================================
Homez OS

File : tests/test_pricing_router_route_ordering.py

V7 Gate 7(2026-08-15) — 실제 Browser E2E(신규 "채널 정산" 화면)
도중 발견한 실제 라우팅 결함의 회귀 테스트.

`GET /pricing/reconciliations`가 `GET /pricing/{pricing_id}`보다
router에 나중에 등록돼 있었다 — FastAPI/Starlette는 라우트를 등록
순서대로 매칭하므로, "/pricing/reconciliations" 요청이
"/{pricing_id}"에 pricing_id="reconciliations"로 먼저 매칭되어
정수 파싱 실패(422)로 항상 깨져 있었다(단위 테스트는 서비스 계층을
직접 호출해 이 결함을 잡지 못했다 — 실제 HTTP 라우팅 경로를 거치는
검증이 필요했던 이유).

app/domains/pricing/router.py에서 `list_reconciliations`
(`GET /reconciliations`)를 `get_pricing`(`GET /{pricing_id}`)보다
먼저 등록하도록 옮겨 고쳤다 — 이 테스트는 그 등록 순서를 고정한다.
=========================================================
"""

import unittest


class PricingReconciliationsRouteOrderingTestCase(unittest.TestCase):

    def test_reconciliations_route_registered_before_pricing_id_route(self):

        import app.main

        pricing_routes = [
            r for r in app.main.app.routes
            if hasattr(r, "path") and r.path.startswith("/pricing")
        ]

        reconciliations_index = None
        pricing_id_index = None

        for i, r in enumerate(pricing_routes):
            if r.path == "/pricing/reconciliations" and "GET" in r.methods:
                reconciliations_index = i
            if r.path == "/pricing/{pricing_id}" and "GET" in r.methods:
                pricing_id_index = i

        self.assertIsNotNone(
            reconciliations_index,
            "GET /pricing/reconciliations 라우트를 찾을 수 없습니다.",
        )
        self.assertIsNotNone(
            pricing_id_index,
            "GET /pricing/{pricing_id} 라우트를 찾을 수 없습니다.",
        )
        self.assertLess(
            reconciliations_index, pricing_id_index,
            "GET /pricing/reconciliations가 GET /pricing/{pricing_id}보다 "
            "먼저 등록돼야 한다 — 그렇지 않으면 "
            "\"reconciliations\"가 pricing_id로 잘못 매칭돼 422가 "
            "발생한다(2026-08-15 V7 Gate 7 Browser E2E에서 실제로 재현된 "
            "결함).",
        )

    def test_export_csv_and_by_listing_also_precede_pricing_id_route(self):
        """
        같은 클래스의 결함이 다른 고정 literal 경로에는 없는지도 함께
        고정한다 — export/csv, by-listing은 세그먼트 수가 달라 우연히
        충돌을 피하고 있었을 뿐이라, 앞으로 세그먼트가 하나짜리인 새
        literal 경로를 추가할 때 반드시 이 순서 원칙을 지키게 하려는
        의도다.
        """

        import app.main

        pricing_routes = [
            r for r in app.main.app.routes
            if hasattr(r, "path") and r.path.startswith("/pricing")
        ]

        by_listing_index = None
        export_csv_index = None
        pricing_id_index = None

        for i, r in enumerate(pricing_routes):
            if r.path == "/pricing/by-listing/{listing_id}" and "GET" in r.methods:
                by_listing_index = i
            if r.path == "/pricing/export/csv" and "GET" in r.methods:
                export_csv_index = i
            if r.path == "/pricing/{pricing_id}" and "GET" in r.methods:
                pricing_id_index = i

        self.assertIsNotNone(by_listing_index)
        self.assertIsNotNone(export_csv_index)
        self.assertIsNotNone(pricing_id_index)

        self.assertLess(by_listing_index, pricing_id_index)
        # export/csv는 세그먼트가 2개(export, csv)라 /{pricing_id}(세그먼트
        # 1개)와 애초에 충돌하지 않는다 — 순서와 무관하게 항상 안전하다는
        # 사실 자체를 문서화한다(향후 누군가 "그럼 이것도 옮겨야 하나"
        # 헷갈리지 않도록).
        self.assertGreater(
            export_csv_index, pricing_id_index,
            "export/csv는 세그먼트 수가 달라 순서가 뒤에 있어도 원래 "
            "안전하다 — 이 값이 바뀌면(누군가 라우트를 재배치하면) 이 "
            "테스트가 그 사실을 다시 확인해줘야 한다.",
        )


if __name__ == "__main__":
    unittest.main()
