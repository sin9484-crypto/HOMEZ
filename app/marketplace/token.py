"""
=========================================================
Homez OS

File : app/marketplace/token.py
Version : 1.0.0

Marketplace Token Manager
=========================================================
"""

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from typing import Optional
from typing import Dict


@dataclass
class MarketplaceToken:

    access_token: Optional[str] = None

    refresh_token: Optional[str] = None

    token_type: str = "Bearer"

    expires_at: Optional[datetime] = None

    scope: Optional[str] = None

    # --------------------------------------------------
    # Status
    # --------------------------------------------------

    @property
    def is_authenticated(self) -> bool:

        return self.access_token is not None

    @property
    def is_expired(self) -> bool:

        if self.expires_at is None:
            return False

        return datetime.utcnow() >= self.expires_at

    @property
    def expires_in(self) -> Optional[int]:

        if self.expires_at is None:
            return None

        seconds = (
            self.expires_at - datetime.utcnow()
        ).total_seconds()

        return max(0, int(seconds))

    # --------------------------------------------------
    # Token
    # --------------------------------------------------

    def set_token(

        self,

        access_token: str,

        refresh_token: Optional[str] = None,

        expires_in: Optional[int] = None,

        token_type: str = "Bearer",

        scope: Optional[str] = None,

    ):

        self.access_token = access_token

        self.refresh_token = refresh_token

        self.token_type = token_type

        self.scope = scope

        if expires_in:

            self.expires_at = (
                datetime.utcnow()
                + timedelta(seconds=expires_in)
            )

        else:

            self.expires_at = None

    # --------------------------------------------------
    # Clear
    # --------------------------------------------------

    def clear(self):

        self.access_token = None

        self.refresh_token = None

        self.expires_at = None

        self.scope = None

    # --------------------------------------------------
    # Serialize
    # --------------------------------------------------

    def to_dict(self) -> Dict:

        return {

            "access_token": self.access_token,

            "refresh_token": self.refresh_token,

            "token_type": self.token_type,

            "expires_at": (
                self.expires_at.isoformat()
                if self.expires_at
                else None
            ),

            "scope": self.scope,

        }

    # --------------------------------------------------
    # Deserialize
    # --------------------------------------------------

    @classmethod
    def from_dict(

        cls,

        data: Dict,

    ):

        token = cls()

        token.access_token = data.get(
            "access_token"
        )

        token.refresh_token = data.get(
            "refresh_token"
        )

        token.token_type = data.get(
            "token_type",
            "Bearer",
        )

        token.scope = data.get(
            "scope"
        )

        expires = data.get(
            "expires_at"
        )

        if expires:

            token.expires_at = (
                datetime.fromisoformat(expires)
            )

        return token