"""Project verified StoreConnections into marketplace listing accounts.

The connection domain owns credentials and verification state.  The listing
domain only receives the non-secret channel/account identity needed by the
product registration workflow.
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.domains.marketplace_listing.service import MarketplaceListingService
from app.domains.store_connection.constants import ConnectionStatus
from app.domains.store_connection.model import StoreConnection


def sync_connected_store_connections(db: Session) -> dict[str, int]:
    """Idempotently create listing channels/accounts for verified connections."""

    service = MarketplaceListingService(db)
    capabilities_added = len(service.seed_capability_registry())
    accounts_added = 0

    connections = (
        db.query(StoreConnection)
        .filter(StoreConnection.connection_status == ConnectionStatus.CONNECTED)
        .order_by(StoreConnection.id.asc())
        .all()
    )

    for connection in connections:
        channel = service.repository.get_channel_by_code(
            connection.marketplace_code,
        )
        if channel is None:
            # An unknown/unverified channel must not be invented from user data.
            continue

        existing = service.repository.get_account_by_company_channel_code(
            connection.company_id,
            channel.id,
            connection.seller_identifier,
        )
        if existing is not None:
            continue

        service.create_account(
            channel.id,
            connection.seller_identifier,
            connection.display_name,
            connection.company_id,
        )
        accounts_added += 1

    return {
        "capabilities_added": capabilities_added,
        "accounts_added": accounts_added,
        "connections_seen": len(connections),
    }


def sync_connected_store_connections_at_boot(db_path: Path) -> dict[str, int]:
    """Run the projection against the exact database selected by desktop boot."""

    engine = create_engine(f"sqlite:///{db_path}")
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        return sync_connected_store_connections(db)
    finally:
        db.close()
        engine.dispose()


__all__ = [
    "sync_connected_store_connections",
    "sync_connected_store_connections_at_boot",
]
