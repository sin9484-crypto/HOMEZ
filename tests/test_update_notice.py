"""
=========================================================
Homez OS

File : tests/test_update_notice.py

Gate Y-4(2026-08-12) — 업데이트 공지 시스템 검증. 전부 임시 SQLite
파일만 사용한다. 이 테스트 어디에서도 실제 네트워크 호출을 하지
않는다(서비스 자체가 네트워크 호출 코드를 갖지 않는다 — 코드
감사로도 확인 가능).
=========================================================
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import BadRequestException
from app.core.exceptions import NotFoundException
from app.core.version import VERSION as CURRENT_VERSION
from app.database.base import Base
from app.domains.company.model import Company  # noqa: F401 (User FK 해석용)
from app.domains.role.model import Role  # noqa: F401 (User FK 해석용)
from app.domains.update import router as update_router_module
from app.domains.update.service import UpdateNoticeService
from app.domains.update.service import parse_semver
from app.domains.user.model import User  # noqa: F401 (User FK 해석용)


class SemverParsingTestCase(unittest.TestCase):

    def test_valid_semver(self):

        self.assertEqual(parse_semver("2.1.0"), (2, 1, 0))
        self.assertEqual(parse_semver("10.20.30"), (10, 20, 30))

    def test_invalid_semver_rejected(self):

        for bad in ["2.1", "v2.1.0", "2.1.0-beta", "abc", ""]:
            with self.assertRaises(BadRequestException):
                parse_semver(bad)


class UpdateNoticeServiceTestCase(unittest.TestCase):

    def setUp(self):

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="homez_update_test_"))
        self.app_db_path = self.tmp_dir / "app.db"
        self.engine = create_engine(f"sqlite:///{self.app_db_path}")
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.db = self.SessionLocal()
        self.service = UpdateNoticeService(self.db)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_create_notice_succeeds(self):

        notice = self.service.create_notice(
            version="99.0.0",
            title="새 버전",
            message="업데이트하세요.",
            severity="info",
            published_by_user_id=1,
        )

        self.assertIsNotNone(notice.id)
        self.assertTrue(notice.is_active)

    def test_create_notice_rejects_bad_version(self):

        with self.assertRaises(BadRequestException):
            self.service.create_notice(
                version="not-a-version",
                title="t",
                message="m",
                severity="info",
            )

    def test_create_notice_rejects_bad_severity(self):

        with self.assertRaises(BadRequestException):
            self.service.create_notice(
                version="99.0.0",
                title="t",
                message="m",
                severity="catastrophic",
            )

    def test_create_notice_rejects_empty_title(self):

        with self.assertRaises(BadRequestException):
            self.service.create_notice(
                version="99.0.0",
                title="",
                message="m",
                severity="info",
            )

    def test_no_update_when_no_notices(self):

        status = self.service.get_update_status()

        self.assertFalse(status["update_available"])
        self.assertIsNone(status["latest_notice"])
        self.assertEqual(status["current_version"], CURRENT_VERSION)

    def test_no_update_when_notice_is_older_or_equal(self):

        self.service.create_notice(
            version=CURRENT_VERSION,
            title="현재와 동일",
            message="m",
            severity="info",
        )
        self.service.create_notice(
            version="0.0.1",
            title="더 낮은 버전",
            message="m",
            severity="info",
        )

        status = self.service.get_update_status()

        self.assertFalse(status["update_available"])

    def test_update_available_when_newer_notice_exists(self):

        self.service.create_notice(
            version="999.0.0",
            title="미래 버전",
            message="m",
            severity="required",
        )

        status = self.service.get_update_status()

        self.assertTrue(status["update_available"])
        self.assertEqual(status["latest_notice"].version, "999.0.0")

    def test_latest_notice_picks_highest_version(self):

        self.service.create_notice(
            version="10.0.0", title="a", message="m", severity="info",
        )
        self.service.create_notice(
            version="20.0.0", title="b", message="m", severity="info",
        )
        self.service.create_notice(
            version="15.0.0", title="c", message="m", severity="info",
        )

        status = self.service.get_update_status()

        self.assertEqual(status["latest_notice"].version, "20.0.0")

    def test_deactivated_notice_is_ignored(self):

        notice = self.service.create_notice(
            version="999.0.0", title="a", message="m", severity="info",
        )
        self.service.deactivate_notice(notice.id)

        status = self.service.get_update_status()

        self.assertFalse(status["update_available"])

    def test_deactivate_nonexistent_notice_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.service.deactivate_notice(99999)

    def test_list_notices_includes_inactive(self):

        n1 = self.service.create_notice(
            version="1.0.0", title="a", message="m", severity="info",
        )
        self.service.deactivate_notice(n1.id)
        self.service.create_notice(
            version="2.0.0", title="b", message="m", severity="info",
        )

        notices = self.service.list_notices()
        self.assertEqual(len(notices), 2)

    def test_custom_current_version_comparison(self):
        """
        get_update_status()에 current_version을 명시적으로 넘기면
        app.core.version.VERSION이 아니라 그 값 기준으로 비교한다
        (미래에 실행 파일 버전을 동적으로 주입할 수 있게).
        """

        self.service.create_notice(
            version="5.0.0", title="a", message="m", severity="info",
        )

        status_old = self.service.get_update_status(
            current_version="1.0.0",
        )
        status_new = self.service.get_update_status(
            current_version="9.0.0",
        )

        self.assertTrue(status_old["update_available"])
        self.assertFalse(status_new["update_available"])


# ----------------------------------------------------
# Router 계약 — admin_guard 커버리지 + 네트워크 호출 부재
# ----------------------------------------------------


class UpdateRouterContractTestCase(unittest.TestCase):

    def test_status_route_requires_authentication_but_not_admin(self):

        from app.core.guard import admin_guard

        status_route = next(
            r for r in update_router_module.router.routes
            if r.path == "/updates/status"
        )
        calls = [d.call for d in status_route.dependant.dependencies]
        self.assertNotIn(admin_guard, calls)
        self.assertGreater(len(calls), 0)

    def test_notice_management_routes_require_admin_guard(self):

        from app.core.guard import admin_guard

        admin_paths = {"/updates/notices", "/updates/notices/{notice_id}/deactivate"}

        for route in update_router_module.router.routes:
            if route.path in admin_paths:
                calls = [d.call for d in route.dependant.dependencies]
                self.assertIn(admin_guard, calls)

    def test_service_module_has_no_network_client_imports(self):
        """
        Gate Y-4 명시적 경계: 이 세션에서는 실제 네트워크로 매니페스트를
        가져오는 코드를 구현하지 않는다. service.py 소스에 httpx/
        requests/urllib 같은 HTTP 클라이언트 import가 없는지 정적으로
        확인해, 이 경계가 조용히 깨지지 않았는지 지킨다.
        """

        import inspect

        from app.domains.update import service as update_service_module

        source = inspect.getsource(update_service_module)

        for forbidden in ("httpx", "requests", "urllib.request", "aiohttp"):
            self.assertNotIn(
                forbidden,
                source,
                f"{forbidden} 사용이 발견됐습니다 — Gate Y-4는 네트워크 "
                "호출 없는 수동 공지 등록만 구현하기로 했습니다.",
            )


if __name__ == "__main__":
    unittest.main()
