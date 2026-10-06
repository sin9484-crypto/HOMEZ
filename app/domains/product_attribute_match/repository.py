"""
=========================================================
Homez OS

File : app/domains/product_attribute_match/repository.py

2026-09-15 전면 감사 후속(Phase 9G, 10-4) — 저장소 계층.
=========================================================
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from app.domains.product_attribute_match.model import ProductAttributeComparisonItem
from app.domains.product_attribute_match.model import ProductAttributeComparisonRun


class ProductAttributeMatchRepository:

    def __init__(self, db: Session):

        self.db = db

    def add_run(
        self, run: ProductAttributeComparisonRun,
    ) -> ProductAttributeComparisonRun:

        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get_run(
        self, run_id: int, company_id: int,
    ) -> ProductAttributeComparisonRun | None:

        return (
            self.db.query(ProductAttributeComparisonRun)
            .options(joinedload(ProductAttributeComparisonRun.items))
            .filter(
                ProductAttributeComparisonRun.id == run_id,
                ProductAttributeComparisonRun.company_id == company_id,
            )
            .first()
        )

    def get_latest_run_for_product(
        self, company_id: int, product_identifier: str,
    ) -> ProductAttributeComparisonRun | None:

        return (
            self.db.query(ProductAttributeComparisonRun)
            .filter(
                ProductAttributeComparisonRun.company_id == company_id,
                ProductAttributeComparisonRun.product_identifier
                == product_identifier,
            )
            .order_by(
                ProductAttributeComparisonRun.created_at.desc(),
                ProductAttributeComparisonRun.id.desc(),
            )
            .first()
        )

    def list_earlier_runs_in_scope(
        self, run: ProductAttributeComparisonRun,
    ) -> list[ProductAttributeComparisonRun]:
        """이 run보다 먼저 만들어진 같은 범위(회사·상품 식별자·매입처 연결)의
        기록을 최신 순으로 돌려준다. 연결이 없는 기록(None)은 연결이 없는 기록과만
        같은 범위다."""

        query = self.db.query(ProductAttributeComparisonRun).filter(
            ProductAttributeComparisonRun.company_id == run.company_id,
            ProductAttributeComparisonRun.product_identifier
            == run.product_identifier,
        )
        if run.connection_id is None:
            query = query.filter(
                ProductAttributeComparisonRun.connection_id.is_(None),
            )
        else:
            query = query.filter(
                ProductAttributeComparisonRun.connection_id == run.connection_id,
            )
        query = query.filter(
            (ProductAttributeComparisonRun.created_at < run.created_at)
            | (
                (ProductAttributeComparisonRun.created_at == run.created_at)
                & (ProductAttributeComparisonRun.id < run.id)
            ),
        )
        return query.order_by(
            ProductAttributeComparisonRun.created_at.desc(),
            ProductAttributeComparisonRun.id.desc(),
        ).all()

    def get_latest_runs_for_case_variants(
        self, company_id: int, product_identifier: str,
        connection_ids: list[int],
    ) -> list[ProductAttributeComparisonRun]:
        """대소문자만 다른 상품 코드(정확히 같은 코드는 제외)의 기록을 (연결,
        코드)별 최신 1건씩 돌려준다. 상품 코드는 대소문자를 구분하므로 이
        기록들을 같은 상품으로 취급하지 않는다 — 점검이 모호함을 알리는 용도다."""

        if not connection_ids:
            return []
        rows = (
            self.db.query(ProductAttributeComparisonRun)
            .filter(
                ProductAttributeComparisonRun.company_id == company_id,
                ProductAttributeComparisonRun.connection_id.in_(connection_ids),
                func.lower(ProductAttributeComparisonRun.product_identifier)
                == product_identifier.lower(),
                ProductAttributeComparisonRun.product_identifier
                != product_identifier,
            )
            .order_by(
                ProductAttributeComparisonRun.created_at.desc(),
                ProductAttributeComparisonRun.id.desc(),
            )
            .all()
        )
        latest: dict[tuple[int, str], ProductAttributeComparisonRun] = {}
        for row in rows:
            latest.setdefault((row.connection_id, row.product_identifier), row)
        return list(latest.values())

    def get_latest_runs_per_connection(
        self, company_id: int, product_identifier: str,
        connection_ids: list[int],
    ) -> list[ProductAttributeComparisonRun]:
        """같은 회사·같은 상품 코드의 비교 기록을 매입처 연결별로 최신 1건씩
        돌려준다(최신성은 get_latest_run_for_product와 같은 created_at 기준,
        같은 시각이면 id가 큰 쪽). connection_id가 없는 기록과 다른 연결의
        기록은 포함하지 않는다 — 다른 공급처의 같은 코드가 섞이지 않게 한다."""

        if not connection_ids:
            return []
        rows = (
            self.db.query(ProductAttributeComparisonRun)
            .filter(
                ProductAttributeComparisonRun.company_id == company_id,
                ProductAttributeComparisonRun.product_identifier
                == product_identifier,
                ProductAttributeComparisonRun.connection_id.in_(connection_ids),
            )
            .order_by(
                ProductAttributeComparisonRun.created_at.desc(),
                ProductAttributeComparisonRun.id.desc(),
            )
            .all()
        )
        latest: dict[int, ProductAttributeComparisonRun] = {}
        for row in rows:
            latest.setdefault(row.connection_id, row)
        return list(latest.values())

    def list_runs(
        self, company_id: int, *, status: str | None = None,
    ) -> list[ProductAttributeComparisonRun]:

        query = self.db.query(ProductAttributeComparisonRun).filter(
            ProductAttributeComparisonRun.company_id == company_id,
        )
        if status is not None:
            query = query.filter(
                ProductAttributeComparisonRun.overall_status == status,
            )
        return query.order_by(
            ProductAttributeComparisonRun.created_at.desc(),
        ).all()

    def get_item(
        self, item_id: int, run_id: int,
    ) -> ProductAttributeComparisonItem | None:

        return (
            self.db.query(ProductAttributeComparisonItem)
            .filter(
                ProductAttributeComparisonItem.id == item_id,
                ProductAttributeComparisonItem.run_id == run_id,
            )
            .first()
        )


__all__ = ["ProductAttributeMatchRepository"]
