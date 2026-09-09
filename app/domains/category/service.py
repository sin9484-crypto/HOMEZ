from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.base_service import (
    BaseService,
)

from app.domains.category.model import (
    Category,
)

from app.domains.category.repository import (
    CategoryRepository,
)


class CategoryService(
    BaseService[Category]
):

    def __init__(
        self,
        db: Session,
    ) -> None:

        self.repository = CategoryRepository(
            db,
        )


    def get(
        self,
        category_id: int,
    ) -> Category | None:

        return self.repository.get_by_id(
            category_id,
        )


    def list_active(
        self,
    ) -> list[Category]:

        return self.repository.get_active_categories()


    def get_by_slug(
        self,
        slug: str,
    ) -> Category | None:

        return self.repository.get_by_slug(
            slug,
        )


    def get_root_categories(
        self,
    ) -> list[Category]:

        return self.repository.get_root_categories()
    def get_children(
        self,
        parent_id: int,
    ) -> list[Category]:

        return self.repository.get_children(
            parent_id,
        )


    def get_by_level(
        self,
        level: int,
    ) -> list[Category]:

        return self.repository.get_by_level(
            level,
        )


    def search(
        self,
        keyword: str,
    ) -> list[Category]:

        return self.repository.search(
            keyword,
        )


    def get_by_name(
        self,
        name: str,
    ) -> Category | None:

        return self.repository.get_by_name(
            name,
        )


    def get_visible_categories(
        self,
    ) -> list[Category]:

        return self.repository.get_visible_categories()
    def get_popular_categories(
        self,
        limit: int = 20,
    ) -> list[Category]:

        return self.repository.get_popular_categories(
            limit,
        )


    def create(
        self,
        category: Category,
    ) -> Category:

        return self.repository.create(
            category,
        )


    def update(
        self,
        category: Category,
    ) -> Category:

        return self.repository.update(
            category,
        )


    def increment_product_count(
        self,
        category: Category,
    ) -> Category:

        return self.repository.increment_product_count(
            category,
        )


    def decrement_product_count(
        self,
        category: Category,
    ) -> Category:

        return self.repository.decrement_product_count(
            category,
        )
    def deactivate(
        self,
        category: Category,
    ) -> Category:

        return self.repository.deactivate(
            category,
        )


    def delete(
        self,
        category: Category,
    ) -> None:

        self.repository.delete(
            category,
        )


    def build_tree(
        self,
        categories: list[Category],
    ) -> list[dict]:

        tree = []

        roots = [
            category
            for category in categories
            if category.parent_id is None
        ]

        for root in roots:

            node = {
                "id": root.id,
                "name": root.name,
                "children": [],
            }

            children = [
                category
                for category in categories
                if category.parent_id == root.id
            ]

            node["children"] = [
                {
                    "id": child.id,
                    "name": child.name,
                    "children": [],
                }
                for child in children
            ]

            tree.append(
                node
            )

        return tree


__all__ = [
    "CategoryService",
]       