from __future__ import annotations

from app.domains.category.model import (
    Category,
)


class CategoryPolicy:
    """
    Category Domain Policy

    카테고리 생성, 노출,
    수정 및 관리 정책
    """


    @staticmethod
    def can_view(
        category: Category,
    ) -> bool:

        return (
            category.is_active
            and category.is_visible
            and category.deleted_at is None
        )


    @staticmethod
    def can_update(
        category: Category,
    ) -> bool:

        return (
            category.deleted_at is None
        )


    @staticmethod
    def can_delete(
        category: Category,
    ) -> bool:

        return (
            category.product_count == 0
            and category.deleted_at is None
        )
    @staticmethod
    def can_add_product(
        category: Category,
    ) -> bool:

        return (
            category.is_active
            and category.is_visible
            and category.deleted_at is None
        )


    @staticmethod
    def can_create_child(
        category: Category,
    ) -> bool:

        # 최대 카테고리 깊이 제한
        return (
            category.level < 5
        )


    @staticmethod
    def can_display(
        category: Category,
    ) -> bool:

        return (
            category.is_active
            and category.is_visible
        )


    @staticmethod
    def validate_level(
        level: int,
    ) -> bool:

        return (
            1 <= level <= 5
        )
    @staticmethod
    def validate_name(
        name: str,
    ) -> bool:

        return (
            len(name.strip()) >= 2
        )


    @staticmethod
    def validate_sort_order(
        sort_order: int,
    ) -> bool:

        return (
            sort_order >= 0
        )


    @staticmethod
    def validate_status(
        is_active: bool,
        is_visible: bool,
    ) -> bool:

        return (
            isinstance(is_active, bool)
            and isinstance(is_visible, bool)
        )


    @staticmethod
    def can_move(
        category: Category,
        new_parent_id: int | None,
    ) -> bool:

        # 자기 자신을 부모로 지정하는 것 방지
        return (
            category.id != new_parent_id
        )


__all__ = [
    "CategoryPolicy",
]        