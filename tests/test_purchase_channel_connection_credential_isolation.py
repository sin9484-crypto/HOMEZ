"""
=========================================================
Homez OS

File : tests/test_purchase_channel_connection_credential_isolation.py

Gate PT-3 후속(2026-09-08) — 격리 검증 스크립트가 DATABASE_URL만
갈아끼우고 credential_store는 주입하지 않아 실제 Windows Credential
Manager의 homez_channel_connection_1에 합성 값을 저장한 사고의 재발
방지 테스트. 두 가지를 직접 증명한다:

1. `credential_reference`는 DB 행 id만으로 결정되는 문자열
   ("homez_channel_connection_{id}")이라, 서로 완전히 독립된 DB 두
   개가 각자 "id=1"인 연결을 만들면 이름이 완전히 같아진다 — 이
   이름 자체는 실행 환경을 구분하지 못한다.
2. 라우터의 의존성 팩토리(`get_purchase_channel_connection_service`)
   는 넘겨받은 db만으로 서비스를 만든다 — credential_store를 넘기지
   않으면 그 서비스는 (운영 코드와 동일하게) 자기 스스로 실제
   저장소를 선택할 자유가 있다는 뜻이므로, 격리 스크립트는 반드시
   `app.dependency_overrides[get_purchase_channel_connection_service]`
   로 credential_store까지 포함해 서비스 생성 자체를 대체해야 한다.

실제 Windows Credential Manager는 어디에서도 건드리지 않는다.
=========================================================
"""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.windows_credential_store import InMemoryCredentialStore
from app.database.base import Base
from app.domains.company.model import Company
from app.domains.purchase_task.channel_connection_service import (
    PurchaseChannelConnectionService,
)
from app.domains.purchase_task.model import PurchaseChannelConnection
from app.domains.purchase_task.model import PurchaseChannelConnectionEvent
from app.domains.purchase_task.router import get_purchase_channel_connection_service
from app.domains.role.model import Role  # noqa: F401 - Company relationship 등록용
from app.domains.user.model import User  # noqa: F401 - Company relationship 등록용


def _make_temp_db():

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Company.__table__, PurchaseChannelConnection.__table__,
            PurchaseChannelConnectionEvent.__table__,
        ],
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return path, engine, SessionLocal()


class CredentialReferenceCollisionAcrossEnvironmentsTestCase(unittest.TestCase):
    """DB 행 id만 같은 서로 다른 실행 환경이 동일 자격증명 슬롯
    이름을 공유할 수 있는지 — 실제로 그렇다는 것을 직접 증명한다."""

    def setUp(self):

        self._temp_paths = []
        self._engines = []
        self._dbs = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):

        for db in self._dbs:
            db.close()
        for engine in self._engines:
            engine.dispose()
        for path in self._temp_paths:
            if os.path.exists(path):
                os.remove(path)

    def _new_isolated_service(self):

        path, engine, db = _make_temp_db()
        self._temp_paths.append(path)
        self._engines.append(engine)
        self._dbs.append(db)
        company = Company(
            name="테스트 회사", business_number="000-00-00000",
            ceo="대표", phone="02-000-0000",
            email="t@example.com", address="서울",
        )
        db.add(company)
        db.commit()
        store = InMemoryCredentialStore()
        service = PurchaseChannelConnectionService(db, credential_store=store)
        return service, company, store

    def test_first_connection_in_two_independent_databases_shares_credential_reference_name(self):

        service_1, company_1, _ = self._new_isolated_service()
        service_2, company_2, _ = self._new_isolated_service()

        conn_1 = service_1.create_connection(
            company_1.id, mall_code="ONCHANNEL", account_label="환경 1",
        )
        conn_2 = service_2.create_connection(
            company_2.id, mall_code="ONCHANNEL", account_label="환경 2",
        )

        # 서로 완전히 독립된 DB 파일인데도, 둘 다 "그 DB의 첫 연결"이라
        # id=1이 되어 credential_reference 문자열이 완전히 같아진다.
        self.assertEqual(conn_1.id, 1)
        self.assertEqual(conn_2.id, 1)
        self.assertEqual(conn_1.credential_reference, conn_2.credential_reference)
        self.assertEqual(conn_1.credential_reference, "homez_channel_connection_1")

    def test_saving_credential_in_one_environment_does_not_appear_in_the_others_store(self):
        """이름은 같아도, 서로 다른 InMemoryCredentialStore 인스턴스를
        주입했다면(=credential_store 격리를 실제로 지켰다면) 값은
        섞이지 않는다 — 이름 충돌 자체가 문제가 아니라 "저장소를
        공유하는가"가 유일한 실제 격리 경계라는 것을 보여준다."""

        service_1, company_1, store_1 = self._new_isolated_service()
        service_2, company_2, store_2 = self._new_isolated_service()

        conn_1 = service_1.create_connection(
            company_1.id, mall_code="ONCHANNEL", account_label="환경 1",
        )
        service_2.create_connection(
            company_2.id, mall_code="ONCHANNEL", account_label="환경 2",
        )

        service_1.save_credential(conn_1.id, company_1.id, auth_key="env-1-secret")

        self.assertTrue(store_1.exists("homez_channel_connection_1"))
        self.assertFalse(store_2.exists("homez_channel_connection_1"))


class RouterDependencyFactoryTestCase(unittest.TestCase):
    """get_purchase_channel_connection_service()가 credential_store를
    스스로 결정하지 않는다는 것 — 넘겨받은 db로 서비스를 만들 뿐이고,
    credential_store 선택은 PurchaseChannelConnectionService.__init__
    (기본값: 실제 저장소)에 맡긴다는 계약을 문서화한다. 격리 스크립트가
    이 함수 자체를 app.dependency_overrides로 통째로 바꿔치기해야
    하는 이유가 이것이다."""

    def setUp(self):

        self.path, self.engine, self.db = _make_temp_db()
        self.addCleanup(self._cleanup)

    def _cleanup(self):

        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_factory_wires_the_given_db_session(self):

        service = get_purchase_channel_connection_service(db=self.db)

        self.assertIsInstance(service, PurchaseChannelConnectionService)
        self.assertIs(service.db, self.db)


if __name__ == "__main__":
    unittest.main()
