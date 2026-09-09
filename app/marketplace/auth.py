"""
=========================================================
Homez OS

File : app/marketplace/auth.py
Version : 1.0.0

Marketplace Authentication Base
=========================================================
"""

from abc import ABC
from abc import abstractmethod
from typing import Dict
from typing import Any


class MarketplaceAuth(ABC):

    @property
    @abstractmethod
    def auth_type(self) -> str:
        """
        Authentication Type
        Example:
            HMAC
            OAuth2
            API_KEY
            BEARER
        """
        raise NotImplementedError

    # --------------------------------------------------
    # Authorization Header
    # --------------------------------------------------

    @abstractmethod
    def build_headers(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> Dict[str, str]:
        """
        Return authorization headers.
        """
        raise NotImplementedError

    # --------------------------------------------------
    # Validate
    # --------------------------------------------------

    @abstractmethod
    def validate(self) -> bool:
        """
        Validate credentials.
        """
        raise NotImplementedError

    # --------------------------------------------------
    # Refresh
    # --------------------------------------------------

    @abstractmethod
    def refresh(self) -> Any:
        """
        Refresh authentication.
        """
        raise NotImplementedError

    # --------------------------------------------------
    # Revoke
    # --------------------------------------------------

    @abstractmethod
    def revoke(self) -> bool:
        """
        Revoke authentication.
        """
        raise NotImplementedError