"""
=========================================================
Homez OS

File : app/domains/media_asset/fingerprint.py

app/domains/marketplace_listing/fingerprint.py와 동일한 SHA-256 +
canonical JSON 패턴 — 도메인 간 모듈을 직접 import하지 않는 이
코드베이스 컨벤션에 따라 패턴만 복제한다.
=========================================================
"""

import hashlib
import json


def canonical_json(data: dict) -> str:

    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def sha256_hex(text: str) -> str:

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "canonical_json",
    "sha256_hex",
]
