"""
=========================================================
Homez OS

File : app/core/policy.py
Version : 5.0.0

Authorization Policy
=========================================================
"""

from abc import ABC
from abc import abstractmethod

from app.domains.user.model import User


class BasePolicy(ABC):

    @abstractmethod
    def can_view(
        self,
        user: User,
        obj=None,
    ) -> bool:
        ...

    @abstractmethod
    def can_create(
        self,
        user: User,
        obj=None,
    ) -> bool:
        ...

    @abstractmethod
    def can_update(
        self,
        user: User,
        obj=None,
    ) -> bool:
        ...

    @abstractmethod
    def can_delete(
        self,
        user: User,
        obj=None,
    ) -> bool:
        ...


class AllowAllPolicy(BasePolicy):

    def can_view(self, user: User, obj=None) -> bool:
        return True

    def can_create(self, user: User, obj=None) -> bool:
        return True

    def can_update(self, user: User, obj=None) -> bool:
        return True

    def can_delete(self, user: User, obj=None) -> bool:
        return True
class AdminPolicy(BasePolicy):

    def can_view(
        self,
        user: User,
        obj=None,
    ) -> bool:
        return getattr(user, "is_admin", False)

    def can_create(
        self,
        user: User,
        obj=None,
    ) -> bool:
        return getattr(user, "is_admin", False)

    def can_update(
        self,
        user: User,
        obj=None,
    ) -> bool:
        return getattr(user, "is_admin", False)

    def can_delete(
        self,
        user: User,
        obj=None,
    ) -> bool:
        return getattr(user, "is_admin", False)


class OwnerPolicy(BasePolicy):

    def can_view(
        self,
        user: User,
        obj=None,
    ) -> bool:

        if obj is None:
            return False

        return getattr(obj, "user_id", None) == user.id

    def can_create(
        self,
        user: User,
        obj=None,
    ) -> bool:
        return True

    def can_update(
        self,
        user: User,
        obj=None,
    ) -> bool:

        if obj is None:
            return False

        return getattr(obj, "user_id", None) == user.id

    def can_delete(
        self,
        user: User,
        obj=None,
    ) -> bool:

        if obj is None:
            return False

        return getattr(obj, "user_id", None) == user.id
class ReadOnlyPolicy(BasePolicy):

    def can_view(
        self,
        user: User,
        obj=None,
    ) -> bool:
        return True

    def can_create(
        self,
        user: User,
        obj=None,
    ) -> bool:
        return False

    def can_update(
        self,
        user: User,
        obj=None,
    ) -> bool:
        return False

    def can_delete(
        self,
        user: User,
        obj=None,
    ) -> bool:
        return False


__all__ = [
    "BasePolicy",
    "AllowAllPolicy",
    "AdminPolicy",
    "OwnerPolicy",
    "ReadOnlyPolicy",
]   