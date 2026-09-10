"""
=========================================================
Homez OS

File : tests/test_backup_encryption.py

2026-09-09 Phase 5 — app/domains/backup/encryption.py 단독 검증.
BackupService/RestoreService에는 아직 연결되지 않았으므로(사유:
app/domains/backup/encryption.py 모듈 docstring 및
docs/HOMEZ_PROJECT_STATE.md Phase 5 절 참고) 이 파일은 암호화
원시 함수 자체(왕복, 변조 감지, 오탈키 거부, 키 저장소 연동)만
검증한다.
=========================================================
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.windows_credential_store import InMemoryCredentialStore
from app.domains.backup.encryption import BackupEncryptionError
from app.domains.backup.encryption import MAGIC
from app.domains.backup.encryption import decrypt_file
from app.domains.backup.encryption import encrypt_file
from app.domains.backup.encryption import generate_key
from app.domains.backup.encryption import get_or_create_backup_encryption_key
from app.domains.backup.encryption import is_encrypted_backup


class TestBackupEncryptionRoundTrip(unittest.TestCase):

    def setUp(self):

        self.tmp = TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):

        self.tmp.cleanup()

    def test_round_trip_restores_original_bytes(self):

        plaintext = b"HOMEZ backup encryption round-trip test payload" * 100
        source = self.tmp_path / "plain.db"
        encrypted = self.tmp_path / "plain.db.enc"
        decrypted = self.tmp_path / "plain.db.dec"
        source.write_bytes(plaintext)

        key = generate_key()
        encrypt_file(source, encrypted, key)
        decrypt_file(encrypted, decrypted, key)

        self.assertEqual(decrypted.read_bytes(), plaintext)

    def test_round_trip_with_empty_file(self):

        source = self.tmp_path / "empty.db"
        encrypted = self.tmp_path / "empty.db.enc"
        decrypted = self.tmp_path / "empty.db.dec"
        source.write_bytes(b"")

        key = generate_key()
        encrypt_file(source, encrypted, key)
        decrypt_file(encrypted, decrypted, key)

        self.assertEqual(decrypted.read_bytes(), b"")

    def test_encrypt_in_place_same_path(self):

        plaintext = b"in-place encryption test" * 50
        path = self.tmp_path / "inplace.db"
        path.write_bytes(plaintext)

        key = generate_key()
        encrypt_file(path, path, key)

        self.assertTrue(is_encrypted_backup(path))
        self.assertNotEqual(path.read_bytes(), plaintext)

        decrypted = self.tmp_path / "inplace.dec"
        decrypt_file(path, decrypted, key)
        self.assertEqual(decrypted.read_bytes(), plaintext)

    def test_ciphertext_differs_from_plaintext(self):

        plaintext = b"\x00" * 500
        source = self.tmp_path / "zeros.db"
        encrypted = self.tmp_path / "zeros.db.enc"
        source.write_bytes(plaintext)

        encrypt_file(source, encrypted, generate_key())

        self.assertNotIn(plaintext, encrypted.read_bytes())

    def test_two_encryptions_of_same_plaintext_differ(self):
        """매번 새 nonce를 쓰므로 같은 평문·같은 키라도 암호문이 달라야 한다."""

        plaintext = b"same plaintext" * 20
        source = self.tmp_path / "same.db"
        enc1 = self.tmp_path / "same1.enc"
        enc2 = self.tmp_path / "same2.enc"
        source.write_bytes(plaintext)

        key = generate_key()
        encrypt_file(source, enc1, key)
        encrypt_file(source, enc2, key)

        self.assertNotEqual(enc1.read_bytes(), enc2.read_bytes())


class TestBackupEncryptionTamperDetection(unittest.TestCase):

    def setUp(self):

        self.tmp = TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.key = generate_key()
        self.plaintext = b"tamper detection payload" * 30
        self.source = self.tmp_path / "src.db"
        self.encrypted = self.tmp_path / "src.db.enc"
        self.source.write_bytes(self.plaintext)
        encrypt_file(self.source, self.encrypted, self.key)

    def tearDown(self):

        self.tmp.cleanup()

    def test_flipped_ciphertext_byte_is_rejected(self):

        data = bytearray(self.encrypted.read_bytes())
        data[-1] ^= 0xFF
        self.encrypted.write_bytes(bytes(data))

        with self.assertRaises(BackupEncryptionError):
            decrypt_file(self.encrypted, self.tmp_path / "out.db", self.key)

    def test_flipped_tag_byte_is_rejected(self):

        data = bytearray(self.encrypted.read_bytes())
        tag_offset = len(MAGIC) + 16
        data[tag_offset] ^= 0xFF
        self.encrypted.write_bytes(bytes(data))

        with self.assertRaises(BackupEncryptionError):
            decrypt_file(self.encrypted, self.tmp_path / "out.db", self.key)

    def test_wrong_key_is_rejected(self):

        wrong_key = generate_key()

        with self.assertRaises(BackupEncryptionError):
            decrypt_file(
                self.encrypted, self.tmp_path / "out.db", wrong_key,
            )

    def test_decrypt_never_writes_output_on_tamper(self):

        data = bytearray(self.encrypted.read_bytes())
        data[-1] ^= 0xFF
        self.encrypted.write_bytes(bytes(data))

        out_path = self.tmp_path / "should_not_exist.db"

        with self.assertRaises(BackupEncryptionError):
            decrypt_file(self.encrypted, out_path, self.key)

        self.assertFalse(out_path.exists())

    def test_decrypt_non_encrypted_file_is_rejected(self):

        plain_sqlite_like = self.tmp_path / "not_encrypted.db"
        plain_sqlite_like.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)

        with self.assertRaises(BackupEncryptionError):
            decrypt_file(
                plain_sqlite_like, self.tmp_path / "out.db", self.key,
            )


class TestIsEncryptedBackup(unittest.TestCase):

    def setUp(self):

        self.tmp = TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):

        self.tmp.cleanup()

    def test_true_for_encrypted_file(self):

        source = self.tmp_path / "a.db"
        encrypted = self.tmp_path / "a.db.enc"
        source.write_bytes(b"payload")
        encrypt_file(source, encrypted, generate_key())

        self.assertTrue(is_encrypted_backup(encrypted))

    def test_false_for_plain_sqlite_file(self):

        plain = self.tmp_path / "plain.db"
        plain.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)

        self.assertFalse(is_encrypted_backup(plain))

    def test_false_for_missing_file(self):

        missing = self.tmp_path / "does_not_exist.db"

        self.assertFalse(is_encrypted_backup(missing))


class TestBackupEncryptionKeyManagement(unittest.TestCase):

    def setUp(self):

        self.store = InMemoryCredentialStore()

    def test_first_call_creates_and_persists_key(self):

        self.assertFalse(
            self.store.exists("homez_backup_encryption_key"),
        )

        key = get_or_create_backup_encryption_key(self.store)

        self.assertEqual(len(key), 32)
        self.assertTrue(
            self.store.exists("homez_backup_encryption_key"),
        )

    def test_second_call_returns_same_key(self):

        key1 = get_or_create_backup_encryption_key(self.store)
        key2 = get_or_create_backup_encryption_key(self.store)

        self.assertEqual(key1, key2)

    def test_key_usable_for_encrypt_decrypt(self):

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "s.db"
            encrypted = tmp_path / "s.db.enc"
            decrypted = tmp_path / "s.db.dec"
            source.write_bytes(b"stored-key round trip")

            key = get_or_create_backup_encryption_key(self.store)
            encrypt_file(source, encrypted, key)
            decrypt_file(encrypted, decrypted, key)

            self.assertEqual(
                decrypted.read_bytes(), b"stored-key round trip",
            )


if __name__ == "__main__":
    unittest.main()
