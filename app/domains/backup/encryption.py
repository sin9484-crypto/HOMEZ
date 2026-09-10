"""
=========================================================
Homez OS

File : app/domains/backup/encryption.py

2026-09-09 Phase 5(HOMEZ_USER_OPERATION_SETTINGS.md 11번 — "실제 DB
백업 파일은 암호화하고 GitHub에 올리지 않는다") — 백업 파일 암호화
원시 모듈.

왜 표준 라이브러리만 쓰는가:
  이 저장소에는 `cryptography`/`pycryptodome` 같은 검증된 AEAD
  라이브러리가 설치되어 있지 않다. CLAUDE.md Whitelist는 "의존성
  추가"를 별도 승인 대상으로 명시한다 — 사용자 승인 없이 새 패키지를
  추가하지 않는다(app/core/windows_credential_store.py가 이미 같은
  이유로 pywin32 대신 표준 ctypes만 쓰는 선례를 남겼다). 그래서 이
  모듈은 `hashlib`/`hmac`/`secrets`만으로 encrypt-then-MAC 구성을
  직접 구현한다.

구성 (표준 stdlib 전용, 알려진 1차 원리 조합):
  1. 32바이트 마스터 키에서 HMAC-SHA256으로 enc_key/mac_key를
     분리한다(HKDF-Expand와 동일한 발상 — RFC 5869).
  2. 평문을 HMAC-SHA256(enc_key, nonce || counter) 블록을 이어붙인
     키스트림과 XOR한다 — HMAC을 PRF로 쓰는 카운터 모드 스트림
     암호(레거시 "HMAC-DRBG류" 구성과 동일 계열).
  3. MAGIC || nonce || ciphertext 전체에 대해 HMAC-SHA256 태그를
     계산해 파일 끝에 붙인다(encrypt-then-MAC) — 복호화 전에
     `hmac.compare_digest`로 태그를 먼저 검증하고, 일치하지 않으면
     단 1바이트도 복호화하지 않는다(변조/오탈키 감지, fail-closed).

한계와 후속 과제(정직하게 기록):
  이 구성은 AES-GCM 같은 표준·감사된 AEAD보다 실전 검증(cryptanalysis
  history)이 훨씬 적다. 새 의존성 추가가 승인되면 `cryptography`
  패키지의 AES-256-GCM으로 교체하는 것을 권장한다 — 이 모듈의 공개
  함수 시그니처(encrypt_file/decrypt_file)는 그런 교체가 있어도 호출부
  변경이 최소화되도록 설계했다.

이 모듈은 아직 BackupService.create_backup()/RestoreService에
연결되지 않았다 — 그 연결은 기존 backup/restore 테스트 스위트와
약 15곳의 운영 호출부(app/domains/backup/router.py,
app/domains/restore/router.py, app/desktop/restore_helper.py 등)에
영향을 주는 더 큰 변경이라 별도 단계로 분리했다. 자세한 이유는
docs/HOMEZ_PROJECT_STATE.md Phase 5 절 참고.
=========================================================
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from app.core.windows_credential_store import CredentialNotFoundError
from app.core.windows_credential_store import CredentialStore

MAGIC = b"HOMEZBAKENC1\n"

_NONCE_SIZE = 16
_TAG_SIZE = 32
_BLOCK_SIZE = 32
_KEY_SIZE = 32

BACKUP_ENCRYPTION_KEY_CREDENTIAL_TARGET = "homez_backup_encryption_key"


class BackupEncryptionError(Exception):
    """
    암호화/복호화 실패 — 원인 메시지에 키·평문·키스트림 원문을
    포함하지 않는다.
    """


def generate_key() -> bytes:

    return secrets.token_bytes(_KEY_SIZE)


def get_or_create_backup_encryption_key(
    credential_store: CredentialStore,
    *,
    target_name: str = BACKUP_ENCRYPTION_KEY_CREDENTIAL_TARGET,
) -> bytes:
    """
    Credential 저장소에 저장된 백업 암호화 키를 읽어오거나, 없으면
    새로 만들어 저장한 뒤 반환한다. 키 자체는 이 함수의 반환값
    외에는(로그·예외 메시지 등) 어디에도 노출하지 않는다.
    """

    try:
        payload = credential_store.read(target_name)
        return base64.b64decode(payload["key_b64"])

    except CredentialNotFoundError:
        key = generate_key()
        credential_store.save(
            target_name,
            {"key_b64": base64.b64encode(key).decode("ascii")},
        )
        return key


def _derive_subkeys(key: bytes) -> tuple[bytes, bytes]:

    if len(key) != _KEY_SIZE:
        raise BackupEncryptionError(
            f"백업 암호화 키 길이가 올바르지 않습니다(기대: "
            f"{_KEY_SIZE}바이트).",
        )

    enc_key = hmac.new(key, b"HOMEZ-BACKUP-ENC-v1", hashlib.sha256).digest()
    mac_key = hmac.new(key, b"HOMEZ-BACKUP-MAC-v1", hashlib.sha256).digest()

    return enc_key, mac_key


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:

    blocks = []
    counter = 0
    produced = 0

    while produced < length:
        block = hmac.new(
            enc_key,
            nonce + counter.to_bytes(8, "big"),
            hashlib.sha256,
        ).digest()
        blocks.append(block)
        produced += _BLOCK_SIZE
        counter += 1

    return b"".join(blocks)[:length]


def _xor_bytes(data: bytes, keystream: bytes) -> bytes:

    return bytes(a ^ b for a, b in zip(data, keystream))


def encrypt_file(source_path: Path, dest_path: Path, key: bytes) -> None:
    """
    source_path의 평문을 읽어 암호화한 뒤 dest_path에 원자적으로
    쓴다. source_path == dest_path여도 안전하다(임시 파일에 먼저
    쓰고 os.replace로 교체).
    """

    source_path = Path(source_path)
    dest_path = Path(dest_path)

    enc_key, mac_key = _derive_subkeys(key)

    plaintext = source_path.read_bytes()
    nonce = secrets.token_bytes(_NONCE_SIZE)
    keystream = _keystream(enc_key, nonce, len(plaintext))
    ciphertext = _xor_bytes(plaintext, keystream)

    tag = hmac.new(
        mac_key, MAGIC + nonce + ciphertext, hashlib.sha256,
    ).digest()

    tmp_path = dest_path.with_suffix(dest_path.suffix + ".enctmp")

    with open(tmp_path, "wb") as f:
        f.write(MAGIC)
        f.write(nonce)
        f.write(tag)
        f.write(ciphertext)

    os.replace(tmp_path, dest_path)


def is_encrypted_backup(path: Path) -> bool:

    path = Path(path)

    if not path.exists():
        return False

    with open(path, "rb") as f:
        header = f.read(len(MAGIC))

    return header == MAGIC


def decrypt_file(source_path: Path, dest_path: Path, key: bytes) -> None:
    """
    암호화된 source_path를 복호화해 dest_path에 원자적으로 쓴다.
    MAC 태그가 일치하지 않으면(변조 또는 오탈키) 어떤 바이트도
    쓰지 않고 BackupEncryptionError를 던진다(fail-closed).
    """

    source_path = Path(source_path)
    dest_path = Path(dest_path)

    raw = source_path.read_bytes()

    if not raw.startswith(MAGIC):
        raise BackupEncryptionError(
            f"암호화된 백업 파일이 아닙니다(매직 헤더 불일치): "
            f"{source_path}",
        )

    offset = len(MAGIC)
    nonce = raw[offset:offset + _NONCE_SIZE]
    offset += _NONCE_SIZE
    tag = raw[offset:offset + _TAG_SIZE]
    offset += _TAG_SIZE
    ciphertext = raw[offset:]

    enc_key, mac_key = _derive_subkeys(key)

    expected_tag = hmac.new(
        mac_key, MAGIC + nonce + ciphertext, hashlib.sha256,
    ).digest()

    if not hmac.compare_digest(tag, expected_tag):
        raise BackupEncryptionError(
            f"백업 파일 무결성 태그가 일치하지 않습니다(변조 또는 "
            f"잘못된 키): {source_path}",
        )

    keystream = _keystream(enc_key, nonce, len(ciphertext))
    plaintext = _xor_bytes(ciphertext, keystream)

    tmp_path = dest_path.with_suffix(dest_path.suffix + ".dectmp")

    with open(tmp_path, "wb") as f:
        f.write(plaintext)

    os.replace(tmp_path, dest_path)


__all__ = [
    "MAGIC",
    "BACKUP_ENCRYPTION_KEY_CREDENTIAL_TARGET",
    "BackupEncryptionError",
    "generate_key",
    "get_or_create_backup_encryption_key",
    "encrypt_file",
    "decrypt_file",
    "is_encrypted_backup",
]
