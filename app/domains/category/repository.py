from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base_repository import (
    BaseRepository,
)

from app.domains.category.model import (
    Category,
)


class CategoryRepository(
    BaseRepository[Category]
):

    def __init__(
        self,
        db: Session,
    ) -> None:

        super().__init__(
            db,
            Category,
        )


    def get_by_id(
        self,
        category_id: int,
    ) -> Category | None:

        return self.db.scalar(
            select(Category).where(
                Category.id == category_id
            )
        )


    def get_active_categories(
        self,
    ) -> list[Category]:

        return list(
            self.db.scalars(
                select(Category).where(
                    Category.is_active.is_(True)
                )
            )
        )


    def get_by_slug(
        self,
        slug: str,
    ) -> Category | None:

        return self.db.scalar(
            select(Category).where(
                Category.slug == slug
            )
        )
    def get_root_categories(
        self,
    ) -> list[Category]:

        return list(
            self.db.scalars(
                select(Category).where(
                    Category.parent_id.is_(None)
                )
                .where(
                    Category.is_active.is_(True)
                )
                .order_by(
                    Category.sort_order.asc()
                )
            )
        )


    def get_children(
        self,
        parent_id: int,
    ) -> list[Category]:

        return list(
            self.db.scalars(
                select(Category).where(
                    Category.parent_id == parent_id
                )
                .where(
                    Category.is_active.is_(True)
                )
                .order_by(
                    Category.sort_order.asc()
                )
            )
        )


    def get_by_level(
        self,
        level: int,
    ) -> list[Category]:

        return list(
            self.db.scalars(
                select(Category).where(
                    Category.level == level
                )
            )
        )
    def search(
        self,
        keyword: str,
    ) -> list[Category]:

        return list(
            self.db.scalars(
                select(Category).where(
                    Category.name.contains(
                        keyword
                    )
                )
            )
        )


    def get_by_name(
        self,
        name: str,
    ) -> Category | None:

        return self.db.scalar(
            select(Category).where(
                Category.name == name
            )
        )


    def get_visible_categories(
        self,
    ) -> list[Category]:

        return list(
            self.db.scalars(
                select(Category)
                .where(
                    Category.is_visible.is_(True)
                )
                .where(
                    Category.is_active.is_(True)
                )
                .order_by(
                    Category.sort_order.asc()
                )
            )
        )


    def get_popular_categories(
        self,
        limit: int = 20,
    ) -> list[Category]:

        return list(
            self.db.scalars(
                select(Category)
                .order_by(
                    Category.product_count.desc()
                )
                .limit(limit)
            )
        )
    def increment_product_count(
        self,
        category: Category,
    ) -> Category:

        category.product_count += 1

        self.db.commit()
        self.db.refresh(
            category,
        )

        return category


    def decrement_product_count(
        self,
        category: Category,
    ) -> Category:

        if category.product_count > 0:
            category.product_count -= 1

        self.db.commit()
        self.db.refresh(
            category,
        )

        return category


    def deactivate(
        self,
        category: Category,
    ) -> Category:

        category.is_active = False

        self.db.commit()
        self.db.refresh(
            category,
        )

        return category


    def delete(
        self,
        category: Category,
    ) -> None:

        self.db.delete(
            category,
        )

        self.db.commit()


__all__ = [
    "CategoryRepository",
]   