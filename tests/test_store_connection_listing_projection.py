"""StoreConnection -> marketplace listing account projection contract."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.domains.marketplace_listing.model import MarketplaceAccount
from app.domains.marketplace_listing.model import MarketplaceChannel
from app.domains.marketplace_listing.model import MarketplaceFulfillmentCapability
from app.domains.marketplace_listing.store_connection_projection import (
    sync_connected_store_connections,
)
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection


class StoreConnectionListingProjectionTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "projection.db"
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(
            self.engine,
            tables=[
                StoreConnection.__table__,
                MarketplaceChannel.__table__,
                MarketplaceAccount.__table__,
                MarketplaceFulfillmentCapability.__table__,
            ],
        )
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_connected_connection_creates_non_secret_account_idempotently(self):
        connection = StoreConnection(
            company_id=7,
            marketplace_code="COUPANG",
            display_name="Coupang primary",
            seller_identifier="seller-7",
            credential_reference="HOMEZ/secret-reference-not-to-copy",
            connection_status=ConnectionStatus.CONNECTED,
            credential_version=1,
            created_by=11,
            creation_idempotency_key="projection-test",
            creation_request_fingerprint="a" * 64,
        )
        self.db.add(connection)
        self.db.commit()

        first = sync_connected_store_connections(self.db)
        second = sync_connected_store_connections(self.db)

        channel = self.db.query(MarketplaceChannel).filter_by(code="COUPANG").one()
        account = self.db.query(MarketplaceAccount).one()
        self.assertEqual(first["accounts_added"], 1)
        self.assertEqual(second["accounts_added"], 0)
        self.assertEqual(account.company_id, 7)
        self.assertEqual(account.channel_id, channel.id)
        self.assertEqual(account.account_code, "seller-7")
        self.assertEqual(account.account_name, "Coupang primary")
        self.assertFalse(hasattr(account, "credential_reference"))

    def test_unverified_connection_does_not_create_account(self):
        self.db.add(StoreConnection(
            company_id=8,
            marketplace_code="COUPANG",
            display_name="Pending",
            seller_identifier="seller-8",
            connection_status=ConnectionStatus.ERROR,
            credential_version=0,
            created_by=12,
            creation_idempotency_key="projection-error-test",
            creation_request_fingerprint="b" * 64,
        ))
        self.db.commit()

        result = sync_connected_store_connections(self.db)

        self.assertEqual(result["connections_seen"], 0)
        self.assertEqual(self.db.query(MarketplaceAccount).count(), 0)


if __name__ == "__main__":
    unittest.main()
