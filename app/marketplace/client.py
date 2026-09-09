"""
=========================================================
Homez OS

File : app/marketplace/client.py
Version : 2.0.0

Marketplace HTTP Client
=========================================================
"""

from typing import Any
from typing import Optional

import requests


class MarketplaceClient:

    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        base_url: str,
        timeout: int = DEFAULT_TIMEOUT,
    ):

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _url(
        self,
        path: str,
    ) -> str:

        if path.startswith("/"):
            return self.base_url + path

        return self.base_url + "/" + path

    def request(
        self,
        method: str,
        path: str,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        data: Any = None,
        files: Any = None,
        timeout: int | None = None,
    ):

        response = self.session.request(
            method=method.upper(),
            url=self._url(path),
            headers=headers,
            params=params,
            json=json,
            data=data,
            files=files,
            timeout=timeout or self.timeout,
        )

        response.raise_for_status()

        if not response.content:
            return None

        try:
            return response.json()

        except Exception:
            return response.text
"""
=========================================================
Homez OS

File : app/marketplace/client.py
Version : 2.0.0

Marketplace HTTP Client
=========================================================
"""

from typing import Any
from typing import Optional

import requests


class MarketplaceClient:

    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        base_url: str,
        timeout: int = DEFAULT_TIMEOUT,
    ):

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _url(
        self,
        path: str,
    ) -> str:

        if path.startswith("/"):
            return self.base_url + path

        return self.base_url + "/" + path

    def request(
        self,
        method: str,
        path: str,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        data: Any = None,
        files: Any = None,
        timeout: int | None = None,
    ):

        response = self.session.request(
            method=method.upper(),
            url=self._url(path),
            headers=headers,
            params=params,
            json=json,
            data=data,
            files=files,
            timeout=timeout or self.timeout,
        )

        response.raise_for_status()

        if not response.content:
            return None

        try:
            return response.json()

        except Exception:
            return response.text
    # --------------------------------------------------
    # Upload
    # --------------------------------------------------

    def upload(
        self,
        path: str,
        files,
        headers=None,
        data=None,
    ):

        return self.request(
            "POST",
            path,
            headers=headers,
            data=data,
            files=files,
        )

    # --------------------------------------------------
    # Download
    # --------------------------------------------------

    def download(
        self,
        path: str,
        headers=None,
        params=None,
    ):

        response = self.session.get(
            self._url(path),
            headers=headers,
            params=params,
            timeout=self.timeout,
            stream=True,
        )

        response.raise_for_status()

        return response
    # --------------------------------------------------
    # Close
    # --------------------------------------------------

    def close(
        self,
    ) -> None:

        self.session.close()


__all__ = [
    "MarketplaceClient",
]        