"""
=========================================================
Homez OS

File : app/domains/diagnostics/db_identity.py

2026-08-30 후속 지시 — DB Identity 진단(읽기 전용, admin 전용).

목적: 이 세션 앞부분에서 "Claude가 측정한 운영 DB 경로/해시"와
"Codex가 측정한 경로/해시"가 서로 다르다고 보고된 사고를 다시 겪지
않도록, HOMEZ 서버 프로세스 자기 자신이 실제로 사용 중인 DB의 정체를
스스로 구조적으로 보고하게 한다. 외부 도구(Claude/Codex 등)가 각자
다른 실행 컨텍스트(예: Windows 패키지 앱 컨텍스트에 의한
%LOCALAPPDATA% 가상화)에서 파일시스템을 보는 문제를, 프로세스 자기
판단으로 대체한다 — canonical 판정 근거는 이 값(또는 사용자가 외부
PowerShell에서 직접 측정한 값)만 인정한다.

이 파일은:
- 오직 읽기만 한다 — DB 파일을 열어 SHA-256을 계산하고 stat()만
  본다. SQLAlchemy로 새 커넥션을 열거나 쿼리하지 않는다(엔진의 URL만
  읽는다 — app/database/session.py::get_engine_db_path()와 동일한
  원칙, 그 함수를 그대로 재사용한다).
- 외부 프로세스를 실행하지 않는다(subprocess 없음, 명령 문자열을
  조합하지 않는다) — Windows 전용 정보(NTFS volume serial/file
  index/hardlink count)는 ctypes로 Win32 API(CreateFileW/
  GetFileInformationByHandle)를 직접 호출해서만 얻는다.
- Windows가 아니면 예외를 던지지 않고 available=False로 정상
  응답한다("지원하지 않음"이 아니라 "이 정보만 비어 있음").
- Credential·사용자 개인정보를 출력하지 않는다. HOMEZ_DATA_ROOT
  값은 마스킹해서만 노출한다(개발자가 직접 설정하는 격리 테스트용
  경로이지만, 사용자 프로필 경로를 포함할 수 있어 그대로 노출하지
  않는다).
=========================================================
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.sensitive_data import mask_address

_HASH_CHUNK_SIZE = 1024 * 1024  # 1MB — 큰 DB 파일도 한 번에 메모리에 올리지 않는다.
# 2026-08-30 후속 지시 — 이 상한을 넘는 파일은 전체 해시 계산을
# 건너뛴다(요청이 오래 걸려 화면을 막는 것을 피하기 위함). 500MB는
# 이 시점의 실제 homez.db(수 MB대)보다 훨씬 크게 잡은 여유값이다 —
# 정상 크기에서는 절대 개입하지 않고, 비정상적으로 커진 경우에만
# fail-safe로 작동한다.
_MAX_HASH_BYTES = 500 * 1024 * 1024  # 500MB


@dataclass(frozen=True)
class WindowsFileIdentity:
    available: bool
    volume_serial: int | None = None
    file_index: int | None = None
    hardlink_count: int | None = None
    error: str | None = None


def _get_windows_file_identity(path: Path) -> WindowsFileIdentity:
    """
    NTFS volume serial + file index + hardlink count를 ctypes로 직접
    조회한다. subprocess/외부 명령을 전혀 실행하지 않는다 — Win32 API
    (CreateFileW → GetFileInformationByHandle → CloseHandle)만 쓴다.
    파일은 읽기 전용으로 열되, FILE_SHARE_* 플래그를 전부 켜서 실제로
    이 파일을 쓰고 있는 다른 프로세스(HOMEZ 본체 등)를 절대 막지
    않는다(잠그지 않는다).
    """

    if platform.system() != "Windows":
        return WindowsFileIdentity(available=False, error="NOT_WINDOWS")

    try:
        from ctypes import wintypes
    except Exception:
        return WindowsFileIdentity(
            available=False, error="CTYPES_WINTYPES_UNAVAILABLE",
        )

    class _FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", _FILETIME),
            ("ftLastAccessTime", _FILETIME),
            ("ftLastWriteTime", _FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE

        handle = create_file(
            str(path), GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None, OPEN_EXISTING, 0, None,
        )

        if handle is None or handle == INVALID_HANDLE_VALUE:
            return WindowsFileIdentity(
                available=False, error="CREATE_FILE_FAILED",
            )

        try:
            info = _BY_HANDLE_FILE_INFORMATION()
            get_info = kernel32.GetFileInformationByHandle
            get_info.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
            ]
            get_info.restype = wintypes.BOOL

            ok = get_info(handle, ctypes.byref(info))
            if not ok:
                return WindowsFileIdentity(
                    available=False, error="GET_FILE_INFO_FAILED",
                )

            file_index = (info.nFileIndexHigh << 32) | info.nFileIndexLow

            return WindowsFileIdentity(
                available=True,
                volume_serial=info.dwVolumeSerialNumber,
                file_index=file_index,
                hardlink_count=info.nNumberOfLinks,
            )
        finally:
            kernel32.CloseHandle(handle)
    except Exception as exc:  # noqa: BLE001 — 진단 기능 자체가 죽으면 안 된다.
        return WindowsFileIdentity(available=False, error=type(exc).__name__)


def _sha256_of_file(path: Path) -> str:

    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _path_suggests_package_redirection(path: Path) -> bool:
    """
    이 세션에서 실제로 확인된 사실 — Claude 자신의 실행 컨텍스트가
    Windows 패키지 앱으로 등록돼 있어, %LOCALAPPDATA% 하위 경로가
    최초 접근 시 `AppData\\Local\\Packages\\<PackageId>\\LocalCache\\...`
    로 하드링크되는 것을 확인했다. 경로 문자열 조합이 아니라 각
    경로 구성요소(parts)를 그대로 비교한다.
    """

    parts_lower = [p.lower() for p in path.parts]
    return "packages" in parts_lower and "localcache" in parts_lower


def collect_db_identity(
    *,
    engine_db_path: Path | None,
    bootstrap_db_path: Path | None,
) -> dict[str, Any]:
    """
    실제 SQLAlchemy engine이 물린 DB 파일의 정체를 읽기 전용으로
    측정한다. Credential이나 사용자 개인정보는 이 함수의 반환값
    어디에도 포함되지 않는다(파일 경로·크기·시각·해시·NTFS 식별자만).
    """

    sampled_started_at = datetime.now(timezone.utc).isoformat()

    raw_data_root = os.environ.get("HOMEZ_DATA_ROOT", "").strip()
    data_root_env = {
        "is_set": bool(raw_data_root),
        "masked_value": mask_address(raw_data_root) if raw_data_root else None,
    }

    if engine_db_path is None:
        return {
            "resolved_db_path": None,
            "exists": False,
            "sampled_at": sampled_started_at,
            "homez_data_root_env": data_root_env,
            "identity_stable": False,
            "redirection_suspected": False,
            "path_mismatch_warning": False,
            "windows_file_identity": {"available": False, "error": "NO_ENGINE_PATH"},
        }

    path = Path(engine_db_path)
    exists = path.exists()

    if not exists:
        return {
            "resolved_db_path": str(path),
            "exists": False,
            "sampled_at": sampled_started_at,
            "homez_data_root_env": data_root_env,
            "identity_stable": False,
            "redirection_suspected": _path_suggests_package_redirection(path),
            "path_mismatch_warning": (
                bootstrap_db_path is not None
                and path.resolve() != Path(bootstrap_db_path).resolve()
            ),
            "windows_file_identity": {"available": False, "error": "FILE_NOT_FOUND"},
        }

    # 2026-08-30 후속 지시(DB 운영 안전 감사) — 이 화면은 이제
    # 명시적 "진단 새로고침" 버튼에서만 호출되지만(자동 실행 아님),
    # 그래도 DB가 아주 커지면 이 한 번의 요청 자체가 오래 걸릴 수
    # 있다. 전체 파일 해시가 의미 있으려면 결국 전체를 읽어야 하므로
    # (부분 읽기로는 "그 순간의 파일 전체"를 증명할 수 없다), 크기
    # 상한을 넘는 파일은 해시 계산 자체를 건너뛰고 이유를 알린다 —
    # 아예 응답하지 않거나 요청을 오래 막는 대신, 훨씬 빠른 다른
    # 필드(경로·크기·mtime·NTFS 식별자)만이라도 즉시 돌려준다.
    before_stat = path.stat()
    if before_stat.st_size > _MAX_HASH_BYTES:
        sha256_hex = None
        changed_during_measurement = False
        identity_stable = False
        hash_skipped_reason = "FILE_TOO_LARGE"
        after_stat = before_stat
    else:
        sha256_hex = _sha256_of_file(path)
        after_stat = path.stat()
        changed_during_measurement = (
            before_stat.st_size != after_stat.st_size
            or before_stat.st_mtime != after_stat.st_mtime
        )
        identity_stable = not changed_during_measurement
        hash_skipped_reason = None

    windows_identity = _get_windows_file_identity(path)

    resolved_path = path.resolve()
    path_mismatch_warning = (
        bootstrap_db_path is not None
        and resolved_path != Path(bootstrap_db_path).resolve()
    )

    return {
        "resolved_db_path": str(resolved_path),
        "exists": True,
        "file_size_bytes": after_stat.st_size,
        "mtime_utc": datetime.fromtimestamp(
            after_stat.st_mtime, tz=timezone.utc,
        ).isoformat(),
        # 측정 중 값이 바뀌면(identity_stable=False) 이 해시를 확정값
        # 으로 쓰지 않는다 — 호출부(Schema/UI)는 identity_stable이
        # False일 때 이 값을 "참고용, 미확정"으로만 표시해야 한다.
        "sha256": sha256_hex,
        "hash_skipped_reason": hash_skipped_reason,
        "sampled_at": sampled_started_at,
        "changed_during_measurement": changed_during_measurement,
        "identity_stable": identity_stable,
        "homez_data_root_env": data_root_env,
        "redirection_suspected": _path_suggests_package_redirection(path),
        "path_mismatch_warning": path_mismatch_warning,
        "windows_file_identity": {
            "available": windows_identity.available,
            "volume_serial": windows_identity.volume_serial,
            "file_index": windows_identity.file_index,
            "hardlink_count": windows_identity.hardlink_count,
            "error": windows_identity.error,
        },
    }


__all__ = [
    "WindowsFileIdentity",
    "collect_db_identity",
]
