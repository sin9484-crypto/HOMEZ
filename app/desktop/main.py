"""
=========================================================
Homez OS

File : app/desktop/main.py

HOMEZ Desktop Shell — 진입점

Flow:
  단일 인스턴스 확인 → FastAPI를 백그라운드 스레드로 시작 →
  HOMEZ 전용 /health 검증 → PyWebView 독립 창에서 /console 표시 →
  창 종료 → 서버 정상 종료 → 리소스 해제.

이 모듈이 시작한 서버(app/desktop/server.py의 DesktopServerHandle)만
종료한다 — 다른 프로세스를 대상으로 하는 어떤 종료 로직도 없다.

개발자 도구는 기본적으로 비활성화되어 있다. 환경 변수
`HOMEZ_DESKTOP_DEBUG=1`을 설정한 경우에만(개발 모드) 활성화된다.
=========================================================
"""

import ctypes
import os
import sys

# Windows 작업표시줄·Alt+Tab에서 HOMEZ 창을 pythonw.exe의 기본
# 프로세스 그룹이 아니라 HOMEZ 고유 그룹으로 묶기 위한 AppUserModelID.
# 버전이 바뀌어도 값을 바꾸지 않는다(사용자 핀 고정·작업표시줄 그룹
# 연속성 유지) — Gate F-10, 2026-08-07.
APP_USER_MODEL_ID = "HOMEZ.CommerceOS.Desktop"

import webview

from app.core.desktop_console_session_store import DesktopConsoleSessionStore
from app.core.windows_credential_store import CredentialStoreUnavailableError
from app.core.windows_credential_store import WindowsCredentialStore
from app.database.bootstrap import bootstrap_environment
from app.database.migration_runner import MigrationRunnerError
from app.database.seed import seed_environment
from app.domains.channel_policy.service import (
    seed_channel_policy_catalog_at_boot,
)
from app.domains.marketplace_listing.store_connection_projection import (
    sync_connected_store_connections_at_boot,
)
from app.database.session import get_engine_db_path
from app.desktop.crash_log import log_lifecycle_event
from app.desktop.paths import get_assets_dir
from app.desktop.paths import get_homez_db_path
from app.desktop.restore_helper import RESTORE_HELPER_CLI_FLAG
from app.desktop.restore_helper import register_desktop_context
from app.desktop.server import DesktopServerHandle
from app.desktop.server import HomezHealthCheckFailed
from app.desktop.server import PortIsOtherServiceError
from app.desktop.server import get_desktop_logger
from app.desktop.server import start_server
from app.desktop.single_instance import SingleInstanceGuard

MB_ICONERROR = 0x10
MB_OK = 0x0
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000

# 오류 코드 — 2026-07-30, "왜 실패했는지 알 수 없다"는 문제에 대응해
# 추가. 각 코드는 실패 지점을 정확히 하나로 특정한다(로그와 대조 가능).
ERROR_CODE_PORT_CONFLICT = "E1001"
ERROR_CODE_HEALTH_TIMEOUT = "E1002"
ERROR_CODE_UNHANDLED_EXCEPTION = "E1003"
ERROR_CODE_BOOTSTRAP_FAILED = "E1004"
ERROR_CODE_DB_PATH_MISMATCH = "E1005"
ERROR_CODE_SEED_FAILED = "E1006"
ERROR_CODE_CHANNEL_POLICY_SEED_FAILED = "E1007"


def _build_start_failure_message(error_code: str) -> str:
    """
    화면에는 항상 오류 코드와 로그 파일 경로만 표시한다 — 원본 예외
    메시지나 traceback은 절대 포함하지 않는다(요구사항).
    """

    from app.desktop.crash_log import get_crash_log_path

    try:
        log_path = str(get_crash_log_path())
    except Exception:  # noqa: BLE001
        log_path = "(로그 경로 확인 실패)"

    return (
        f"HOMEZ를 시작하지 못했습니다. (오류 코드: {error_code})\n\n"
        f"로그 파일:\n{log_path}"
    )


def _show_error_message_box(error_code: str, title: str = "HOMEZ") -> None:
    """
    pythonw.exe로 실행하면 콘솔이 없어 print()가 어디에도 보이지
    않는다 — 그 상태에서도 오류가 조용히 사라지지 않도록 네이티브
    Windows 메시지 박스로 표시한다. 이 호출 자체가 실패해도(비-Windows
    환경 등) 앱 종료를 막지 않는다. traceback 전체나 원본 예외
    메시지가 아니라 항상 오류 코드 + 로그 경로만 표시한다.
    """

    message = _build_start_failure_message(error_code)

    try:
        ctypes.windll.user32.MessageBoxW(
            0, message, title,
            MB_ICONERROR | MB_OK | MB_SETFOREGROUND | MB_TOPMOST,
        )
    except Exception:  # noqa: BLE001
        pass

WINDOW_TITLE = "HOMEZ | EVERY HOMEZ"
DEFAULT_WIDTH = 1440
DEFAULT_HEIGHT = 900
MIN_WIDTH = 1024
MIN_HEIGHT = 700
BACKGROUND_COLOR = "#F5F7FA"

SW_RESTORE = 9


class _DesktopJsApi:
    """
    pywebview의 js_api 브리지 — 이 창 안의 JS만 `window.pywebview.api.
    get_desktop_token()`을 호출해 Desktop 세션 토큰을 얻을 수 있다.
    일반 브라우저에는 `window.pywebview` 자체가 없으므로 이 값을 알아낼
    방법이 없다(2026-07-30, app/core/desktop_auth.py — Desktop token을
    실제 방어 계층에 연결하는 설계의 핵심 경계).

    토큰을 URL 쿼리/fragment나 localStorage에 두지 않기 위한 것이므로,
    이 메서드 자체도 로그에 값을 남기지 않는다.

    2026-08-14 Gate F-2 — 같은 원칙으로 콘솔 로그인 세션(access_token)
    도 이 브리지로만 오간다. `session_store`가 None이면(Windows
    Credential Manager를 이 환경에서 쓸 수 없음) 영속화 없이 매번
    새로 로그인해야 하는 상태로 안전하게 저하(degrade)한다 — 이번
    실행 자체를 막지 않는다.
    """

    def __init__(
        self,
        session_token: str,
        setup_nonce: str,
        session_store: "DesktopConsoleSessionStore | None" = None,
    ):
        self._session_token = session_token
        self._setup_nonce = setup_nonce
        self._console_session_store = session_store

    def get_desktop_token(self) -> str:

        return self._session_token

    def get_setup_nonce(self) -> str:
        """
        최초 관리자 설정 화면 전용. 일반 브라우저는 이 브리지 자체를
        호출할 수 없다(app/core/desktop_setup.py 참고).
        """

        return self._setup_nonce

    # --------------------------------------------------
    # 콘솔 로그인 세션(access_token) — Windows Credential Manager
    # --------------------------------------------------

    def load_console_session(self) -> dict | None:
        """
        재시작 후 복원할 세션이 있으면 `{"user_id": int, "access_token":
        str}`을, 없거나 만료됐으면 None을 반환한다. 토큰 값은 반환값
        자체에만 담기고 로그에는 남지 않는다.
        """

        if self._console_session_store is None:
            return None

        return self._console_session_store.load()

    def save_console_session(
        self, user_id: int, access_token: str, expires_at_epoch: int | None,
        refresh_token: str | None = None,
        refresh_expires_at_epoch: int | None = None,
    ) -> bool:
        """
        로그인/Refresh 성공 직후 콘솔 JS가 호출한다. 저장소를 쓸 수
        없는 환경이면 False만 반환한다(로그인 자체는 이미 성공했으므로
        이번 실행에서는 계속 진행하되, 다음 재시작에는 세션이 복원되지
        않는다는 뜻).

        2026-08-30 V7 후속 안정화 Phase 2 — Refresh Token도 함께
        저장한다("localStorage/sessionStorage에 Refresh Token을 두지
        않는다" 요구사항 — Windows Credential Manager로만 저장한다).
        """

        if self._console_session_store is None:
            return False

        if not isinstance(user_id, int) or not access_token:
            return False

        self._console_session_store.save(
            user_id, access_token, expires_at_epoch,
            refresh_token=refresh_token,
            refresh_expires_at_epoch=refresh_expires_at_epoch,
        )

        return True

    def clear_console_session(self) -> bool:
        """로그아웃 시 콘솔 JS가 호출한다 — 저장된 세션을 실제로 지운다."""

        if self._console_session_store is None:
            return False

        self._console_session_store.clear()

        return True


def _set_app_user_model_id(logger) -> None:
    """
    Windows 작업표시줄·Alt+Tab이 이 프로세스가 만드는 창을 HOMEZ
    고유 그룹(APP_USER_MODEL_ID)으로 인식하게 한다 — 설정하지 않으면
    Windows가 python.exe/pythonw.exe 자체를 그룹 키로 사용해 다른
    Python 앱과 같은 작업표시줄 항목으로 묶이거나 기본 Python 아이콘이
    표시될 수 있다. 창이 생성되기 전(가능한 한 프로세스 시작 초기)에
    호출해야 효과가 있다. 실패해도 HOMEZ 실행 자체를 막지 않으며,
    Secret이나 로컬 경로 없이 오류 유형만 로그에 남긴다. Windows
    이외 환경에서는 안전하게 아무 것도 하지 않는다.
    """

    if sys.platform != "win32":
        return

    try:
        set_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_id.argtypes = [ctypes.c_wchar_p]
        set_id.restype = ctypes.c_long
        hresult = set_id(APP_USER_MODEL_ID)
        if hresult == 0:
            logger.info("Windows AppUserModelID 설정 완료: %s", APP_USER_MODEL_ID)
        else:
            logger.warning(
                "Windows AppUserModelID 설정이 HRESULT=%s를 반환했습니다(무시하고 계속).",
                hresult,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Windows AppUserModelID 설정 실패(무시): %s", type(exc).__name__,
        )


def _try_focus_existing_window(logger) -> None:
    """
    이미 실행 중인 HOMEZ Desktop 인스턴스의 창을 앞으로 가져오기를
    시도한다(best-effort). 창을 찾지 못하거나 API 호출이 실패해도
    예외를 던지지 않는다 — 실패하면 그냥 안내 메시지만 남긴다.
    """

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)

        if hwnd:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            logger.info("기존 HOMEZ 창을 앞으로 가져왔습니다.")
        else:
            logger.info(
                "기존 HOMEZ 창을 찾지 못했습니다(제목 불일치 또는 아직 "
                "로드되지 않음).",
            )

    except Exception as exc:  # noqa: BLE001
        logger.warning("기존 창 포커스 시도 실패(무시): %s", exc)


def run() -> int:
    """
    HOMEZ Desktop Shell 진입점. 반환값은 프로세스 종료 코드다.

    2026-07-30: 콘솔 없이(pythonw.exe) 실행하면 이전까지의 print()
    출력이 어디에도 보이지 않아, 시작 실패가 사용자에게 완전히
    조용히 사라지는 문제가 있었다 — 각 실패 분기에 네이티브 메시지
    박스를 추가하고, `%LOCALAPPDATA%\\HOMEZ\\logs\\homez-desktop.log`
    에도 같은 이벤트를 보조로 남긴다(기존 storage\\logs\\
    desktop-launcher.log 로거는 그대로 유지 — 대체하지 않음).
    """

    logger = get_desktop_logger()
    log_lifecycle_event("HOMEZ Desktop 프로세스 시작")
    _set_app_user_model_id(logger)
    guard = SingleInstanceGuard()

    if not guard.acquire():
        logger.info("이미 실행 중인 HOMEZ Desktop 인스턴스가 있어 종료합니다.")
        log_lifecycle_event("이미 실행 중인 인스턴스 감지 — 새 서버를 만들지 않음")
        print("HOMEZ가 이미 실행 중입니다.")
        _try_focus_existing_window(logger)
        return 0

    handle: DesktopServerHandle | None = None

    try:
        try:
            log_lifecycle_event("DB 부트스트랩 시작")
            # 2026-08-05 CTO 재검증 지시 Gate E — 승인 없이 호출한다.
            # `pending`(실제 DDL) Migration이 있으면 자동 적용되지
            # 않고 `migration_approval_required=True`로만 표시된다
            # (backfill은 스키마 변경이 없어 계속 자동 처리됨).
            # Desktop UI(console.js init())가 `GET /desktop-setup/
            # migration-status`로 이 상태를 조회해 승인 화면을 보여준다
            # — 서버 자체는 이 상태에서도 정상 기동한다(fail-closed
            # 대상은 여전히 checksum 불일치 등 진짜 오류뿐).
            # 2026-08-11 Gate V-1 — 여기가 실제 운영 DB를 대상으로
            # bootstrap_environment()를 호출해도 되는 유일한 공식
            # 진입점이다(db_path를 명시하지 않으므로
            # confirm_production_path=True가 반드시 필요하다).
            bootstrap_result = bootstrap_environment(
                confirm_production_path=True,
            )
            log_lifecycle_event(
                "DB 부트스트랩 완료 "
                f"(신규 설치={bootstrap_result.is_new_install}, "
                f"backfill={len(bootstrap_result.backfilled)}건, "
                f"apply={len(bootstrap_result.applied)}건, "
                f"승인 대기 Migration="
                f"{bootstrap_result.migration_approval_required})",
            )

        except MigrationRunnerError as exc:
            # 여기서 실패하면 DB 상태가 Model과 불일치할 수 있으므로,
            # 서버를 아예 기동하지 않는다(fail-closed) — 원본 예외
            # 메시지는 로그에만 남기고 화면에는 오류 코드만 표시한다.
            logger.error("DB 부트스트랩 실패: %s", exc)
            log_lifecycle_event(
                f"[{ERROR_CODE_BOOTSTRAP_FAILED}] 시작 실패: "
                f"{type(exc).__name__}",
            )
            print(f"오류: {exc}")
            _show_error_message_box(ERROR_CODE_BOOTSTRAP_FAILED)
            return 1

        # V7 Live Gate 4 재작업(2026-08-16) — 결함 1: bootstrap_
        # environment()가 방금 Migration을 적용한 DB 경로와, 실제
        # 요청을 처리할 SQLAlchemy 세션 엔진(app/database/session.py의
        # 전역 engine)이 물리는 DB 경로가 반드시 정확히 같은 파일이어야
        # 한다. 둘 다 결국 app.desktop.paths.get_homez_db_path()를
        # 거치므로 정상 조건에서는 항상 일치하지만, 두 경로 계산이
        # 다시 갈라지는 회귀(예: DATABASE_URL 환경변수만 바꾸고
        # bootstrap 경로 계약은 갱신하지 않는 경우)를 조용히 넘기지
        # 않기 위해 명시적으로 fail-fast 확인한다 — 하나라도 어긋나면
        # 서버를 아예 기동하지 않는다. 경로 문자열 자체는 로그 파일
        # (사용자 화면에는 노출되지 않음)에만 남긴다.
        try:
            bootstrap_db_path = get_homez_db_path(confirm=True).resolve()
            engine_db_path = get_engine_db_path()

            if engine_db_path is None or engine_db_path != bootstrap_db_path:
                logger.error(
                    "DB 경로 불일치 감지: bootstrap=%s, session_engine=%s",
                    bootstrap_db_path, engine_db_path,
                )
                log_lifecycle_event(
                    f"[{ERROR_CODE_DB_PATH_MISMATCH}] 시작 실패: "
                    "DB 경로 불일치",
                )
                print("오류: 내부 DB 경로 설정이 일치하지 않습니다.")
                _show_error_message_box(ERROR_CODE_DB_PATH_MISMATCH)
                return 1

        except Exception as exc:  # noqa: BLE001 — 경로 계산 실패도 fail-closed
            logger.error("DB 경로 확인 중 예외: %s", exc)
            log_lifecycle_event(
                f"[{ERROR_CODE_DB_PATH_MISMATCH}] 시작 실패: "
                f"{type(exc).__name__}",
            )
            print(f"오류: {exc}")
            _show_error_message_box(ERROR_CODE_DB_PATH_MISMATCH)
            return 1

        # 결함 2: 공식 bootstrap 흐름에 기준 데이터(역할·권한) 시딩을
        # 이어붙인다 — Migration이 방금 만든/갱신한 것과 동일한
        # db_path(위에서 이미 session engine과 동일함을 확인함)를
        # 대상으로, 단일 Transaction으로 idempotent하게 실행한다.
        try:
            log_lifecycle_event("기준 데이터 시딩 시작")
            seed_result = seed_environment(bootstrap_db_path)
            log_lifecycle_event(
                "기준 데이터 시딩 완료 "
                f"(역할 추가={seed_result['roles_added']}건, "
                f"권한 추가={seed_result['permissions_added']}건)",
            )

        except Exception as exc:  # noqa: BLE001 — 시딩 실패는 fail-closed
            logger.error("기준 데이터 시딩 실패: %s", exc)
            log_lifecycle_event(
                f"[{ERROR_CODE_SEED_FAILED}] 시작 실패: "
                f"{type(exc).__name__}",
            )
            print(f"오류: {exc}")
            _show_error_message_box(ERROR_CODE_SEED_FAILED)
            return 1

        # Audit(2026-08-21, CTO 후속 지시) — 채널 정책 규칙 카탈로그도
        # 역할·권한과 동일하게 부팅마다 자동·멱등 시딩한다. 이전에는
        # 관리자가 `POST /channel-policy/rules/seed-catalog`를 수동
        # 호출해야만 채워졌는데, 신규 설치·업그레이드에서 이 호출이
        # 누락되면 모든 채널의 활성 규칙이 0건이 되어(카탈로그가 이
        # 채널에 대해 시딩된 적이 없다는 사실 자체를 서비스 레벨에서
        # fail-closed 처리하도록 이미 고쳤지만) 어떤 상품도 정책 검사를
        # 통과할 수 없는 상태가 영구히 지속된다.
        #
        # 2026-08-23 실사용 중 발견한 결함 수정 — channel_policy_rules를
        # 만드는 Migration이 아직 승인 대기 상태(다른 pending Migration과
        # 함께 있는 경우)면 이 테이블 자체가 없어 seed_channel_policy_
        # catalog_at_boot()가 OperationalError로 죽고, 그 결과 앱이 아예
        # 기동하지 못해 사용자가 Migration 승인 화면조차 볼 수 없는
        # 순환 잠금이 발생한다(E1007 재현 확인). role/permission 시딩과
        # 달리 이 테이블은 이미 존재한다고 가정할 수 없으므로, 시딩
        # 자체를 대상 테이블이 실제로 존재할 때만 시도한다 — 존재하지
        # 않으면 조용히 건너뛰고(다음 재시작 때, 즉 Migration 승인 후
        # 다시 시도된다) 정상적으로 서버를 계속 기동한다. app/core/
        # audit_db.py::_has_created_at_column()과 동일한 "매번 직접
        # 확인" 방어 패턴.
        import sqlite3

        # 기본값 True(=평소처럼 시딩을 시도한다) — "테이블이 확실히
        # 없다"고 직접 확인된 경우에만 False로 바꿔 건너뛴다. DB 파일을
        # 열 수 없는 등 확인 자체가 실패한 경우는 "없다고 단정"하지
        # 않는다 — 그 경우 기존과 동일하게 실제 시딩을 시도해 진짜
        # 원인(파일 접근 문제 등)이 있으면 그대로 fail-closed 처리되게
        # 둔다(추측으로 건너뛰지 않는다는 원칙).
        channel_policy_table_ready = True
        try:
            _cp_conn = sqlite3.connect(f"file:{bootstrap_db_path}?mode=ro", uri=True)
            try:
                _cp_conn.execute("PRAGMA query_only = ON")
                channel_policy_table_ready = _cp_conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='channel_policy_rules'",
                ).fetchone() is not None
            finally:
                _cp_conn.close()
        except Exception as exc:  # noqa: BLE001 — 확인 자체 실패는 "없다"로 단정하지 않는다
            logger.warning(
                "채널 정책 테이블 존재 확인 실패(시딩은 그대로 시도함): %s", exc,
            )

        if not channel_policy_table_ready:
            log_lifecycle_event(
                "채널 정책 카탈로그 시딩 건너뜀 — channel_policy_rules "
                "테이블이 아직 없음(해당 Migration 승인 대기 중, 승인 "
                "후 다음 재시작에서 다시 시도됨)",
            )
        else:
            try:
                log_lifecycle_event("채널 정책 카탈로그 시딩 시작")
                policy_seed_result = seed_channel_policy_catalog_at_boot(
                    bootstrap_db_path,
                )
                log_lifecycle_event(
                    "채널 정책 카탈로그 시딩 완료 "
                    f"(규칙={policy_seed_result['rules_seeded']}건)",
                )

            except Exception as exc:  # noqa: BLE001 — 시딩 실패는 fail-closed
                logger.error("채널 정책 카탈로그 시딩 실패: %s", exc)
                log_lifecycle_event(
                    f"[{ERROR_CODE_CHANNEL_POLICY_SEED_FAILED}] 시작 실패: "
                    f"{type(exc).__name__}",
                )
                print(f"오류: {exc}")
                _show_error_message_box(ERROR_CODE_CHANNEL_POLICY_SEED_FAILED)
                return 1

        # A verified StoreConnection and a MarketplaceAccount intentionally
        # have separate ownership: the former owns Credential Manager state,
        # while the latter is the non-secret identity used by product
        # registration.  Keep that projection idempotently synchronized at
        # official desktop boot so an already-connected channel is selectable
        # in the registration workflow.  No credential reference or secret is
        # copied across the boundary.
        try:
            projection_result = sync_connected_store_connections_at_boot(
                bootstrap_db_path,
            )
            log_lifecycle_event(
                "판매채널 상품등록 계정 동기화 완료 "
                f"(연결={projection_result['connections_seen']}건, "
                f"계정 추가={projection_result['accounts_added']}건, "
                f"판매방식 추가={projection_result['capabilities_added']}건)",
            )
        except Exception as exc:  # noqa: BLE001 — listing stays fail-closed
            logger.error("판매채널 상품등록 계정 동기화 실패: %s", exc)
            log_lifecycle_event(
                "판매채널 상품등록 계정 동기화 실패: "
                f"{type(exc).__name__}",
            )

        try:
            handle = start_server()
            log_lifecycle_event(f"서버 준비 완료 (port={handle.port})")

        except PortIsOtherServiceError as exc:
            logger.error("포트가 다른 프로그램에 의해 사용 중: %s", exc)
            log_lifecycle_event(f"[{ERROR_CODE_PORT_CONFLICT}] 시작 실패: PortIsOtherServiceError")
            print(f"오류: {exc}")
            _show_error_message_box(ERROR_CODE_PORT_CONFLICT)
            return 1

        except HomezHealthCheckFailed as exc:
            logger.error("서버 준비 실패: %s", exc)
            log_lifecycle_event(f"[{ERROR_CODE_HEALTH_TIMEOUT}] 시작 실패: HomezHealthCheckFailed")
            print(f"오류: {exc}")
            _show_error_message_box(ERROR_CODE_HEALTH_TIMEOUT)
            return 1

        icon_path = get_assets_dir() / "homez-app.ico"

        # 2026-08-14 Gate F-2 — Windows Credential Manager를 쓸 수 없는
        # 환경(비-Windows 등)이면 fail-closed가 아니라 "이번 실행은
        # 로그인 세션 영속화만 비활성화"로 저하한다 — Desktop 앱 자체가
        # 뜨지 못하게 막을 이유는 없다(로그인은 매 실행 다시 하면 된다).
        try:
            console_session_store = DesktopConsoleSessionStore(
                WindowsCredentialStore(),
            )
        except CredentialStoreUnavailableError:
            logger.warning(
                "Windows Credential Manager 사용 불가 — 콘솔 로그인 세션 "
                "영속화를 비활성화합니다(이번 실행은 재시작 시 다시 "
                "로그인해야 합니다).",
            )
            console_session_store = None

        # target=_blank/새 창 요청은 기본값이 이미 True라 이 줄이 없어도
        # 시스템 기본 브라우저로 열리지만, 의도를 명시적으로 고정한다.
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

        window = webview.create_window(
            title=WINDOW_TITLE,
            url=f"{handle.base_url}/console",
            width=DEFAULT_WIDTH,
            height=DEFAULT_HEIGHT,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            resizable=True,
            background_color=BACKGROUND_COLOR,
            js_api=_DesktopJsApi(
                handle.session_token, handle.setup_nonce, console_session_store,
            ),
        )

        # V7 Live Gate 4 복원 결함 수정(2026-08-17) — Gate R-1 Option B.
        # 복원 실행 API(app/domains/restore/router.py)가 요청 스레드에서
        # 이 창을 닫아 정상 종료 경로(바로 아래 finally 블록)를 그대로
        # 타게 만들 수 있도록 등록한다. 등록하지 않으면
        # restore_helper.request_restore_shutdown()이
        # RestoreHelperError를 던져 fail-closed로 막는다.
        register_desktop_context(window)

        def _on_closing():

            logger.info("창 종료 이벤트 수신 — 서버 정리를 준비합니다.")
            log_lifecycle_event("창 종료 이벤트 수신")

        window.events.closing += _on_closing

        debug_mode = os.environ.get("HOMEZ_DESKTOP_DEBUG") == "1"
        logger.info(
            "PyWebView 창을 시작합니다 (debug=%s)", debug_mode,
        )
        log_lifecycle_event("pywebview 창 시작")

        webview.start(
            icon=str(icon_path) if icon_path.exists() else None,
            debug=debug_mode,
        )

        log_lifecycle_event("pywebview 창 종료됨 — 정상 흐름 복귀")

        return 0

    except Exception as exc:
        logger.exception("Desktop Shell 실행 중 예외 발생")
        # 원본 예외 메시지·traceback은 사용자 화면에 표시하지 않는다
        # (요구사항) — 예외 "종류"만 보조 로그에 남기고, 사용자에게는
        # 오류 코드 + 로그 경로만 보여준다.
        log_lifecycle_event(
            f"[{ERROR_CODE_UNHANDLED_EXCEPTION}] 처리되지 않은 예외 발생: {type(exc).__name__}",
        )
        _show_error_message_box(ERROR_CODE_UNHANDLED_EXCEPTION)
        return 1

    finally:

        if handle is not None:
            handle.shutdown()
        log_lifecycle_event("HOMEZ Desktop 프로세스 종료")

        guard.release()


if __name__ == "__main__":
    # V7 Live Gate 4 복원 결함 수정(2026-08-17) — Gate R-1 Option B.
    # 패키징된 단일 exe가 이 플래그로 자기 자신을 Restore Helper
    # 모드로 재실행한다(별도 실행 파일을 새로 빌드하지 않기 위함).
    # 이 분기는 일반 GUI 부팅 흐름(run()) 전체를 건너뛰므로 반드시
    # 다른 모든 import/부팅 로직보다 먼저 검사한다.
    if RESTORE_HELPER_CLI_FLAG in sys.argv:
        from app.desktop.restore_helper import main_cli as _restore_helper_main_cli

        sys.exit(_restore_helper_main_cli(sys.argv[1:]))

    sys.exit(run())


__all__ = [
    "run",
    "WINDOW_TITLE",
    "ERROR_CODE_PORT_CONFLICT",
    "ERROR_CODE_HEALTH_TIMEOUT",
    "ERROR_CODE_UNHANDLED_EXCEPTION",
    "ERROR_CODE_BOOTSTRAP_FAILED",
    "ERROR_CODE_DB_PATH_MISMATCH",
    "ERROR_CODE_SEED_FAILED",
]
