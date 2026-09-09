"""
=========================================================
Homez OS

File : tests/test_user_settings.py

2026-08-14 Gate F-2 — 사용자 설정(locale/guide_progress/ui_preferences/
notification_preferences) Service + Router 검증. 실제 homez.db는
전혀 사용하지 않는다(임시 SQLite 파일 DB만 사용).

Company만 실제 DB 행으로 만들고, "현재 사용자"는
tests/test_listing_wizard_router.py와 동일한 관례로
`types.SimpleNamespace(id=..., company_id=...)`로 대체한다 — 이 도메인은
user_id/company_id 정수값만 사용하고 User 테이블 자체를 참조하지
않으므로, 실제 users/roles 테이블(및 그 FK 체인)을 끌어올 필요가 없다.

시나리오:
- 허용목록 밖 key는 404
- 최초 생성(expected_version=0) → version=1
- 이미 존재하는데 expected_version=0로 다시 생성 시도 → 409
- 정상 갱신(expected_version 일치) → version 증가
- 오래된 expected_version(버전 충돌) → 409, 값 변경 없음
- 값 크기 초과 → 422
- 값 안에 금지된 필드명(token/password 등)이 있으면 → 422(중첩 dict도)
- 회사 격리 — 다른 회사의 사용자는 서로 값을 보거나 덮어쓸 수 없다
- company_id가 NULL인 사용자는 409(회사 설정 필요)
- 라우터 함수 직접 호출로 위 계약이 그대로 노출되는지 확인
- 실스레드 동시 UPDATE — 정확히 하나만 성공, 나머지는 409
=========================================================
"""

import os
import tempfile
import threading
import types
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ConflictException
from app.core.exceptions import NotFoundException
from app.core.exceptions import ValidationException
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.user_settings import router as usr
from app.domains.user_settings.model import UserSetting
from app.domains.user_settings.repository import UserSettingRepository
from app.domains.user_settings.schema import UserSettingUpdateRequest
from app.domains.user_settings.service import UserSettingService

_NEXT_USER_ID = [1000]


def _make_company(db, name, biz_no):

    company = Company(
        name=name, business_number=biz_no, ceo="테스트",
        phone="02-000-0000", email=f"{biz_no}@example.com", address="서울",
    )
    db.add(company)
    db.commit()
    return company


def _fake_user(company_id):

    _NEXT_USER_ID[0] += 1
    return types.SimpleNamespace(id=_NEXT_USER_ID[0], company_id=company_id)


class UserSettingServiceTestCase(unittest.TestCase):

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[Company.__table__, UserSetting.__table__],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = UserSettingService(UserSettingRepository(self.db))

        self.company = _make_company(self.db, "회사A", "111-11-11111")
        self.user = _fake_user(self.company.id)

        self.other_company = _make_company(self.db, "회사B", "222-22-22222")
        self.other_user = _fake_user(self.other_company.id)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        os.unlink(self.db_path)

    # --------------------------------------------------
    # 허용목록
    # --------------------------------------------------

    def test_unknown_key_get_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.service.get(self.user.id, self.company.id, "not_a_real_key")

    def test_unknown_key_put_raises_not_found(self):

        with self.assertRaises(NotFoundException):
            self.service.put(
                self.user.id, self.company.id, "not_a_real_key",
                "value", 0, 1,
            )

    # --------------------------------------------------
    # 최초 생성 / 재생성 충돌
    # --------------------------------------------------

    def test_get_missing_returns_version_zero_and_null_value(self):

        result = self.service.get(self.user.id, self.company.id, "locale")
        self.assertEqual(result["version"], 0)
        self.assertIsNone(result["value"])

    def test_create_with_expected_version_zero_succeeds(self):

        result = self.service.put(
            self.user.id, self.company.id, "locale", "ko-KR", 0, 1,
        )
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["value"], "ko-KR")
        self.assertEqual(result["schema_version"], 1)
        self.assertIsNotNone(result["updated_at"])

    def test_create_again_with_expected_version_zero_conflicts(self):

        self.service.put(self.user.id, self.company.id, "locale", "ko-KR", 0, 1)

        with self.assertRaises(ConflictException):
            self.service.put(
                self.user.id, self.company.id, "locale", "en-US", 0, 1,
            )

        # 충돌 시도가 기존 값을 훼손하지 않았는지 확인.
        current = self.service.get(self.user.id, self.company.id, "locale")
        self.assertEqual(current["value"], "ko-KR")
        self.assertEqual(current["version"], 1)

    # --------------------------------------------------
    # 낙관적 동시성
    # --------------------------------------------------

    def test_update_with_correct_version_succeeds_and_bumps_version(self):

        created = self.service.put(
            self.user.id, self.company.id, "locale", "ko-KR", 0, 1,
        )
        updated = self.service.put(
            self.user.id, self.company.id, "locale", "en-US",
            created["version"], 1,
        )
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["value"], "en-US")

    def test_update_with_stale_version_conflicts_and_leaves_value_unchanged(self):

        created = self.service.put(
            self.user.id, self.company.id, "locale", "ko-KR", 0, 1,
        )
        self.service.put(
            self.user.id, self.company.id, "locale", "en-US",
            created["version"], 1,
        )

        with self.assertRaises(ConflictException):
            # created["version"]은 이제 stale(현재는 2인데 1로 시도).
            self.service.put(
                self.user.id, self.company.id, "locale", "ja-JP",
                created["version"], 1,
            )

        current = self.service.get(self.user.id, self.company.id, "locale")
        self.assertEqual(current["value"], "en-US")
        self.assertEqual(current["version"], 2)

    # --------------------------------------------------
    # 값 크기 / 금지 필드명
    # --------------------------------------------------

    def test_value_too_large_rejected(self):

        huge = {"blob": "x" * 9000}
        with self.assertRaises(ValidationException):
            self.service.put(
                self.user.id, self.company.id, "guide_progress", huge, 0, 1,
            )

    def test_forbidden_top_level_field_rejected(self):

        with self.assertRaises(ValidationException):
            self.service.put(
                self.user.id, self.company.id, "ui_preferences",
                {"api_token": "should-never-be-here"}, 0, 1,
            )

    def test_forbidden_nested_field_rejected(self):

        with self.assertRaises(ValidationException):
            self.service.put(
                self.user.id, self.company.id, "notification_preferences",
                {"channel": {"email": {"password": "x"}}}, 0, 1,
            )

    def test_benign_value_with_similar_but_safe_words_is_blocked_by_design(self):

        # "tokenizer_theme"는 forbidden token "token"을 부분 문자열로
        # 포함한다 — 이 도메인은 정밀한 필드명 매칭보다 안전 우선
        # (과도 차단 허용) 정책을 택했다. 이 테스트는 실제 동작(차단됨)을
        # false positive 가능성까지 포함해 있는 그대로 문서화한다.
        with self.assertRaises(ValidationException):
            self.service.put(
                self.user.id, self.company.id, "ui_preferences",
                {"tokenizer_theme": "dark"}, 0, 1,
            )

    # --------------------------------------------------
    # 회사 격리
    # --------------------------------------------------

    def test_other_company_user_has_independent_setting_scope(self):

        self.service.put(self.user.id, self.company.id, "locale", "ko-KR", 0, 1)

        other_missing = self.service.get(
            self.other_user.id, self.other_company.id, "locale",
        )
        self.assertIsNone(other_missing["value"])

        self.service.put(
            self.other_user.id, self.other_company.id, "locale", "en-US", 0, 1,
        )

        mine = self.service.get(self.user.id, self.company.id, "locale")
        self.assertEqual(mine["value"], "ko-KR")


class UserSettingRouterTestCase(unittest.TestCase):
    """
    라우터 함수를 실제 Session + fake current_user로 직접 호출한다
    (이 저장소 관례 — httpx 미사용, tests/test_listing_wizard_router.py와
    동일 패턴).
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[Company.__table__, UserSetting.__table__],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )
        self.db = self.SessionLocal()
        self.service = UserSettingService(UserSettingRepository(self.db))

        self.company = _make_company(self.db, "회사A", "333-33-33333")
        self.user = _fake_user(self.company.id)
        self.orphan_user = _fake_user(None)

    def tearDown(self):

        self.db.close()
        self.engine.dispose()
        os.unlink(self.db_path)

    def test_get_and_put_round_trip_via_router_functions(self):

        # 라우터 함수를 직접(ASGI를 거치지 않고) 호출하므로 FastAPI의
        # response_model 자동 직렬화가 적용되지 않는다 — Service가
        # 반환하는 원본 dict를 그대로 받는다(이 저장소의 다른 라우터
        # 직접 호출 테스트와 동일한 관례).
        created = usr.put_user_setting(
            "locale",
            UserSettingUpdateRequest(value="ko-KR", expected_version=0, schema_version=1),
            current_user=self.user,
            service=self.service,
        )
        self.assertEqual(created["version"], 1)

        fetched = usr.get_user_setting(
            "locale", current_user=self.user, service=self.service,
        )
        self.assertEqual(fetched["value"], "ko-KR")
        self.assertEqual(fetched["version"], 1)

    def test_router_rejects_user_without_company(self):

        with self.assertRaises(ConflictException):
            usr.get_user_setting(
                "locale", current_user=self.orphan_user, service=self.service,
            )


class UserSettingConcurrencyTestCase(unittest.TestCase):
    """
    실스레드 + 별도 DB Session/connection으로 낙관적 동시성을 검증한다
    (Mock 아님) — app/domains/store_connection의 동시성 테스트 관례를
    그대로 따른다.
    """

    def setUp(self):

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.engine = create_engine(f"sqlite:///{path}")

        Base.metadata.create_all(
            bind=self.engine,
            tables=[Company.__table__, UserSetting.__table__],
        )

        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine,
        )

        seed_db = self.SessionLocal()
        company = _make_company(seed_db, "회사C", "444-44-44444")
        company_id = company.id
        seed_db.close()
        self.company = types.SimpleNamespace(id=company_id)
        self.user = _fake_user(company_id)

    def tearDown(self):

        self.engine.dispose()
        os.unlink(self.db_path)

    def test_concurrent_update_same_key_only_one_succeeds(self):

        setup_db = self.SessionLocal()
        setup_service = UserSettingService(UserSettingRepository(setup_db))
        created = setup_service.put(
            self.user.id, self.company.id, "guide_progress",
            {"tour": "screens-and-menus", "step": 1}, 0, 1,
        )
        setup_db.close()

        expected_version = created["version"]

        barrier = threading.Barrier(2)
        results = {}

        def worker(name, patch_value):

            db = self.SessionLocal()
            service = UserSettingService(UserSettingRepository(db))
            original = service.repository.update_value_conditional

            def wrapped(*args, **kwargs):
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass
                return original(*args, **kwargs)

            service.repository.update_value_conditional = wrapped

            try:
                service.put(
                    self.user.id, self.company.id, "guide_progress",
                    patch_value, expected_version, 1,
                )
                results[name] = "success"
            except ConflictException:
                results[name] = "conflict"
            finally:
                db.close()

        t1 = threading.Thread(target=worker, args=("t1", {"step": 2}))
        t2 = threading.Thread(target=worker, args=("t2", {"step": 3}))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        outcomes = list(results.values())
        self.assertEqual(outcomes.count("success"), 1)
        self.assertEqual(outcomes.count("conflict"), 1)

        final_db = self.SessionLocal()
        final_service = UserSettingService(UserSettingRepository(final_db))
        final = final_service.get(self.user.id, self.company.id, "guide_progress")
        final_db.close()

        # 정확히 하나의 UPDATE만 반영되어 version은 2여야 한다(경쟁에서
        # 진 쪽이 자신의 UPDATE를 실제로 적용하지 못했음을 증명).
        self.assertEqual(final["version"], 2)
        self.assertIn(final["value"]["step"], (2, 3))


if __name__ == "__main__":
    unittest.main()
