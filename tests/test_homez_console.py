"""
=========================================================
Homez OS

File : tests/test_homez_console.py

HOMEZ V3 운영자 Console (app/web/**) 검증

- 정적 화면(console.html/css/js) 서빙
- 모든 /console/api/* 엔드포인트가 admin_guard를 요구하는지(인증 우회
  없음) 정적 검증
- V2.4/V3 스키마 적용/미적용 두 상태 모두에서 API가 정직하게 동작하는지
  (Mock 데이터로 완료된 것처럼 표시하지 않음)
- Emergency Stop / Automation Mode 실제 상태 전이
- 후보 승인 후 개요 집계에 반영되는지

표준 라이브러리 unittest만 사용. 신규 패키지 없음. homez.db는 사용하지
않고, 테스트 전용 임시 SQLite 파일 DB만 사용한다.
=========================================================
"""

import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.web.router as web_router
from app.core.guard import admin_guard

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(REPO_ROOT, "app", "web")

MIGRATION_PATHS = [
    os.path.join(
        REPO_ROOT, "migrations",
        "20260727_00_create_funding_settlement_schema.sql",
    ),
    os.path.join(
        REPO_ROOT, "migrations",
        "20260728_00_create_v24_v3_schema.sql",
    ),
]


# --------------------------------------------------
# 정적 화면 서빙
# --------------------------------------------------

class ConsoleStaticAssetsTestCase(unittest.TestCase):

    def test_console_html_served_with_login_form(self):

        resp = web_router.console_page()
        content = resp.body.decode("utf-8")

        self.assertIn("HOMEZ 운영자 Console", content)
        self.assertIn('id="login-form"', content)
        self.assertIn('id="shell"', content)
        self.assertIn("/console/static/console.css", content)
        self.assertIn("/console/static/console.js", content)

    def test_console_css_served(self):

        resp = web_router.console_css()
        self.assertTrue(os.path.exists(resp.path))
        self.assertTrue(resp.path.endswith("console.css"))

    def test_console_js_served(self):

        resp = web_router.console_js()
        self.assertTrue(os.path.exists(resp.path))
        self.assertTrue(resp.path.endswith("console.js"))

    def test_console_asset_serves_logo(self):

        resp = web_router.console_asset("homez-logo.png")
        self.assertTrue(os.path.exists(resp.path))

    def test_console_asset_blocks_path_traversal(self):

        with self.assertRaises(HTTPException) as ctx:
            web_router.console_asset("../../../../windows/win.ini")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_console_asset_missing_file_is_404(self):

        with self.assertRaises(HTTPException) as ctx:
            web_router.console_asset("does-not-exist.png")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_no_external_cdn_or_font_dependency(self):

        for filename in ("console.html", "console.css", "console.js"):
            with open(os.path.join(WEB_DIR, filename), encoding="utf-8") as f:
                content = f.read().lower()

            for forbidden in (
                "cdn.jsdelivr", "unpkg.com", "cdnjs", "googleapis.com",
                "fonts.google", "cloudflare.com", "jsdelivr.net",
            ):
                self.assertNotIn(
                    forbidden, content,
                    f"{filename}에 외부 CDN 의존이 있습니다: {forbidden}",
                )

    def test_duplicate_click_prevention_implemented(self):

        with open(os.path.join(WEB_DIR, "console.js"), encoding="utf-8") as f:
            js = f.read()

        self.assertIn(".disabled = true", js)


# --------------------------------------------------
# 인증 우회 없음 (admin_guard 커버리지 정적 검증)
# --------------------------------------------------

class AdminGuardCoverageTestCase(unittest.TestCase):

    def test_all_console_api_routes_require_admin_guard(self):

        api_routes = [
            r for r in web_router.router.routes
            if getattr(r, "path", "").startswith("/console/api/")
        ]
        self.assertGreaterEqual(len(api_routes), 6)

        for route in api_routes:
            calls = [d.call for d in route.dependant.dependencies]
            self.assertIn(
                admin_guard, calls,
                f"{route.path} 이(가) admin_guard 없이 노출되어 있습니다"
                "(인증 우회 가능성).",
            )

    def test_console_shell_and_static_are_public(self):
        """
        로그인 폼 자체가 /console 안에 있으므로 화면·정적 자산은
        인증 없이 로드되어야 한다(그래야 로그인 폼을 보여줄 수 있다).
        """

        public_paths = {
            "/console",
            "/console/static/console.css",
            "/console/static/console.js",
        }

        for route in web_router.router.routes:
            if getattr(route, "path", None) in public_paths:
                calls = [d.call for d in route.dependant.dependencies]
                self.assertNotIn(admin_guard, calls)


# --------------------------------------------------
# V2.4/V3 스키마 적용 상태에서의 API 동작
# --------------------------------------------------

class ConsoleApiWithSchemaTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        conn = sqlite3.connect(path)
        try:
            for migration_path in MIGRATION_PATHS:
                with open(migration_path, encoding="utf-8") as f:
                    conn.executescript(f.read())

            # 2026-08-14 Gate R13 테넌트 격리 감사 —
            # product_candidate_decisions에 company_id가 추가됐다(회사별
            # 결정 이력). 위 두 레거시 Migration은 이 컬럼이 생기기
            # 전에 이 테이블을 만들었으므로, 신규 Migration 파일의
            # 실제 ALTER 문과 동일한 형태로 국소 추가한다(이 최소
            # 스키마 세트를 유지하는 다른 통과 테스트에 영향을 주지
            # 않도록 새 컬럼 추가만 하고 UNIQUE 재생성은 하지 않음 —
            # 이 테스트 파일은 idempotency_key 복합 UNIQUE 시나리오를
            # 검증하지 않는다).
            conn.execute(
                "ALTER TABLE product_candidate_decisions "
                "ADD COLUMN company_id INTEGER NOT NULL DEFAULT 0",
            )

            # 2026-08-15 V7 Gate 2 — product_candidates에 visibility/
            # owner_company_id가 추가됐다(비공개 후보 지원, 요구사항
            # 2). 이 레거시 Migration은 그 전에 이 테이블을 만들었으므로
            # 위와 동일한 이유로 국소 ADD COLUMN을 반영한다(신규
            # Migration 파일의 실제 ALTER 문과 동일한 형태).
            conn.execute(
                "ALTER TABLE product_candidates "
                "ADD COLUMN visibility VARCHAR(20) NOT NULL "
                "DEFAULT 'GLOBAL'",
            )
            conn.execute(
                "ALTER TABLE product_candidates "
                "ADD COLUMN owner_company_id INTEGER",
            )
            conn.commit()
        finally:
            conn.close()

        self.engine = create_engine(f"sqlite:///{path}")

        # 2026-08-14 Gate R13 테넌트 격리 감사 — get_overview()가 이제
        # product_candidate_selections를 조회한다(회사별 승인 상태
        # 투영, ProductCandidate.status 단일 전역 필드의 무임승차
        # 결함을 닫은 신규 테이블). 이 테이블은 FK 없이 독립적이라
        # (이 저장소 전역 컨벤션) 위 두 레거시 Migration 재생 없이도
        # SQLAlchemy 모델 정의만으로 안전하게 단독 생성할 수 있다.
        from app.domains.product_candidate.model import (
            ProductCandidateSelection,
        )
        ProductCandidateSelection.__table__.create(self.engine)

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.fake_admin = SimpleNamespace(id=1, is_admin=True, username="admin")

    def tearDown(self):

        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_overview_reports_schema_ready_with_empty_data(self):

        db = self.SessionLocal()
        try:
            result = web_router.get_overview(_=self.fake_admin, db=db)
            self.assertTrue(result["v3_schema_ready"])
            self.assertIsNotNone(result["candidates"])
            self.assertEqual(result["candidates"]["discovered_today"], 0)
            self.assertEqual(result["candidates"]["pending_review"], 0)
        finally:
            db.close()

    def test_overview_reflects_candidate_lifecycle(self):

        db = self.SessionLocal()
        try:
            from app.domains.product_candidate.schema import (
                ProductCandidateDiscover,
            )
            from app.domains.product_candidate.service import (
                ProductCandidateService,
            )

            svc = ProductCandidateService(db)
            candidate, _ = svc.discover(
                ProductCandidateDiscover(
                    source_type="TEST", source_reference="T-1",
                    market="COUPANG", product_name="테스트 상품",
                ),
                correlation_id="c1",
            )
            svc.apply_trend_analysis(
                candidate.id, company_id=1, trend_score=0.5, confidence=0.5,
                evidence_text="e", correlation_id="c2",
            )

            before = web_router.get_overview(_=self.fake_admin, db=db)
            self.assertEqual(before["candidates"]["pending_review"], 1)
            self.assertEqual(before["candidates"]["approved"], 0)

            svc.recommend(candidate.id, company_id=1, correlation_id="c3")
            svc.approve(
                candidate.id, company_id=1, operator_id=1, is_admin=True,
                memo=None, correlation_id="c4",
            )

            after = web_router.get_overview(_=self.fake_admin, db=db)
            self.assertEqual(after["candidates"]["approved"], 1)
            self.assertEqual(after["candidates"]["pending_review"], 0)
        finally:
            db.close()

    def test_safety_status_default_state(self):

        db = self.SessionLocal()
        try:
            result = web_router.get_safety_status(_=self.fake_admin, db=db)
            self.assertTrue(result["schema_ready"])
            self.assertEqual(result["mode"], "RECOMMEND_ONLY")
            self.assertIsNone(result["emergency_stop"])
        finally:
            db.close()

    def test_emergency_stop_activate_and_deactivate_real_state_change(self):

        db = self.SessionLocal()
        try:
            activated = web_router.activate_emergency_stop(
                web_router.EmergencyStopActivateRequest(reason="테스트 사유"),
                current_user=self.fake_admin, db=db,
            )
            self.assertTrue(activated["is_active"])

            mid = web_router.get_safety_status(_=self.fake_admin, db=db)
            self.assertTrue(mid["emergency_stop"]["is_active"])

            deactivated = web_router.deactivate_emergency_stop(
                current_user=self.fake_admin, db=db,
            )
            self.assertFalse(deactivated["is_active"])

            final = web_router.get_safety_status(_=self.fake_admin, db=db)
            self.assertFalse(final["emergency_stop"]["is_active"])
        finally:
            db.close()

    def test_emergency_stop_requires_reason(self):

        db = self.SessionLocal()
        try:
            with self.assertRaises(Exception):
                web_router.activate_emergency_stop(
                    web_router.EmergencyStopActivateRequest(reason=""),
                    current_user=self.fake_admin, db=db,
                )
        finally:
            db.close()

    def test_set_automation_mode(self):

        db = self.SessionLocal()
        try:
            result = web_router.set_automation_mode(
                web_router.AutomationModeRequest(mode="DISABLED", reason=None),
                current_user=self.fake_admin, db=db,
            )
            self.assertEqual(result["mode"], "DISABLED")

            status = web_router.get_safety_status(_=self.fake_admin, db=db)
            self.assertEqual(status["mode"], "DISABLED")
        finally:
            db.close()

    def test_set_automation_mode_rejects_unknown_mode(self):

        db = self.SessionLocal()
        try:
            with self.assertRaises(HTTPException) as ctx:
                web_router.set_automation_mode(
                    web_router.AutomationModeRequest(
                        mode="NOT_A_REAL_MODE", reason=None,
                    ),
                    current_user=self.fake_admin, db=db,
                )
            self.assertEqual(ctx.exception.status_code, 400)
        finally:
            db.close()

    def test_system_status_all_tables_present(self):

        db = self.SessionLocal()
        try:
            result = web_router.get_system_status(_=self.fake_admin, db=db)
            self.assertEqual(result["db_integrity"], "ok")
            self.assertTrue(result["api_connected"])
            self.assertTrue(result["v23_schema_applied"])
            self.assertTrue(result["v24_v3_schema_applied"])
            self.assertTrue(all(result["v23_tables"].values()))
            self.assertTrue(all(result["v24_v3_tables"].values()))
        finally:
            db.close()

    def test_system_status_includes_recorded_test_evidence_text(self):

        db = self.SessionLocal()
        try:
            result = web_router.get_system_status(_=self.fake_admin, db=db)
            # 실시간으로 테스트를 실행하지 않는다 — 저장소 문서의 텍스트를
            # 그대로 반환하거나(문서가 있으면) None이어야 한다(조작 금지).
            self.assertTrue(
                result["last_recorded_test_evidence"] is None
                or "Test Evidence" in result["last_recorded_test_evidence"],
            )
        finally:
            db.close()


# --------------------------------------------------
# V2.4/V3 스키마 미적용 상태 — 정직한 "적용 필요" 안내
# --------------------------------------------------

class ConsoleApiWithoutSchemaTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path

        self.engine = create_engine(f"sqlite:///{path}")
        # 의도적으로 아무 테이블도 만들지 않는다 — Migration 미적용 상태 재현.

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.fake_admin = SimpleNamespace(id=1, is_admin=True, username="admin")

    def tearDown(self):

        self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_overview_reports_schema_not_ready(self):

        db = self.SessionLocal()
        try:
            result = web_router.get_overview(_=self.fake_admin, db=db)
            self.assertFalse(result["v3_schema_ready"])
            self.assertIsNone(result["candidates"])
            self.assertIsNone(result["safety"])
        finally:
            db.close()

    def test_safety_status_reports_schema_not_ready(self):

        db = self.SessionLocal()
        try:
            result = web_router.get_safety_status(_=self.fake_admin, db=db)
            self.assertFalse(result["schema_ready"])
            self.assertIn("V3", result["message"])
        finally:
            db.close()

    def test_emergency_stop_activate_blocked_without_schema(self):

        db = self.SessionLocal()
        try:
            with self.assertRaises(HTTPException) as ctx:
                web_router.activate_emergency_stop(
                    web_router.EmergencyStopActivateRequest(reason="test"),
                    current_user=self.fake_admin, db=db,
                )
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            db.close()

    def test_set_mode_blocked_without_schema(self):

        db = self.SessionLocal()
        try:
            with self.assertRaises(HTTPException) as ctx:
                web_router.set_automation_mode(
                    web_router.AutomationModeRequest(
                        mode="DISABLED", reason=None,
                    ),
                    current_user=self.fake_admin, db=db,
                )
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            db.close()

    def test_system_status_reports_all_missing(self):

        db = self.SessionLocal()
        try:
            result = web_router.get_system_status(_=self.fake_admin, db=db)
            self.assertFalse(result["v23_schema_applied"])
            self.assertFalse(result["v24_v3_schema_applied"])
            self.assertFalse(any(result["v23_tables"].values()))
            self.assertFalse(any(result["v24_v3_tables"].values()))
        finally:
            db.close()


# --------------------------------------------------
# app.main 회귀 (라우터 등록이 기존 앱을 깨지 않는지)
# --------------------------------------------------

class AppMainRegressionTestCase(unittest.TestCase):
    """
    app.main은 반드시 별도 하위 프로세스에서 import 확인한다(in-process로
    import하지 않는다). app.main은 app/domains/company 등 앱 전체 라우터를
    끌어들이는데, Company.users 관계에 FK/primaryjoin이 없는 기존
    (Whitelist 밖) 결함 때문에 SQLAlchemy가 registry 전체의
    configure_mappers()를 한 번 실패하면 같은 프로세스 안의 이후 모든 ORM
    쿼리(automation_safety/product_candidate 등 무관한 모델 포함)가 함께
    깨진다 — 이번 검증 중 실제로 재현했다. 하위 프로세스로 격리하면 이
    테스트 파일의 다른 DB 테스트에 영향을 주지 않는다.
    """

    def test_app_main_imports_with_console_router_in_subprocess(self):

        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable, "-c",
                "import app.main; "
                "paths = [r.path for r in app.main.app.routes]; "
                "assert '/console' in paths; "
                "assert '/console/api/overview' in paths; "
                "print('OK', len(app.main.app.routes))",
            ],
            cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=60,
        )

        self.assertEqual(
            result.returncode, 0,
            f"app.main import 실패:\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
