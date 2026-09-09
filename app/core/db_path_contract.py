"""
=========================================================
Homez OS

File : app/core/db_path_contract.py

2026-08-31 Preflight 8-A 코드 감사에서 발견 — `app/domains/backup/
router.py`(create_backup), `app/domains/restore/router.py`
(execute_restore), `app/domains/diagnostics/router.py`
(export_diagnostics) 세 곳이 실제로 다룰 DB 파일 경로를
`app.desktop.paths.get_homez_db_path(confirm=True)`로만 계산했다 —
같은 프로세스 안에서 모든 SQL 접근이 실제로 쓰는
`app.database.session`의 전역 engine(`settings.DATABASE_URL` 기준,
`get_engine_db_path()`로 조회 가능)과 일치하는지는 한 번도
확인하지 않았다.

`DATABASE_URL` 환경변수가 기본값과 다르게 설정된 채 이 프로세스가
떠 있으면(격리 테스트·스테이징 재사용·운영자 실수), 이 세 엔드포인트는
"엔진이 실제로 읽고 쓰는 파일"이 아니라 조용히 "기본 설치 위치의
파일"을 대상으로 동작한다 — 백업은 엉뚱한 파일을 저장하고, 복원은
엉뚱한 파일을 통째로 덮어쓰며, 진단 내보내기는 실제와 다른 파일의
정보를 담은 채 "정상"으로 보고한다. 셋 다 에러도 경고도 없이
성공한 것처럼 끝난다.

`app/desktop/main.py`가 2026-08-16(V7 Live Gate 4)에 부팅 시점에서
정확히 이 문제를 이미 fail-closed로 막았다(ERROR_CODE_DB_PATH_
MISMATCH) — 이 모듈은 그 원칙을 요청 처리 시점에도 재사용할 수
있게 공유 헬퍼로 뽑아낸 것이다. 파일을 읽거나 쓰지 않는다 — 경로
문자열만 비교한다.
=========================================================
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.exceptions import InternalServerException
from app.database.session import get_engine_db_path
from app.desktop.paths import get_homez_db_path

logger = logging.getLogger("homez.db_path_contract")

# app/desktop/main.py의 부팅 시점 검사와 같은 근본 원인(경로 불일치)
# 이므로 같은 오류코드를 재사용한다 — 검출 시점(부팅 vs 요청 처리)만
# 다를 뿐 사용자가 알아야 할 사실은 동일하다.
ERROR_CODE_DB_PATH_MISMATCH = "E1005"


def assert_bootstrap_path_matches_engine() -> Path:
    """
    `get_homez_db_path(confirm=True)`(공식 설치 경로 계산 규칙)와
    `get_engine_db_path()`(이 프로세스의 실제 SQLAlchemy engine이
    지금 쓰고 있는 경로)가 일치하는지 확인한다.

    일치하면 그 경로(Path, resolved)를 반환한다 — 호출자는 이
    반환값을 그대로 백업/복원/진단 대상 경로로 쓰면 된다.

    불일치하면 아무 파일도 건드리지 않고 `InternalServerException`
    (HTTP 500)을 던진다(fail-closed) — 두 경로 중 하나를 임의로
    고르거나, 자동으로 맞추거나, 경고만 남기고 계속 진행하지 않는다.
    로그에도 두 경로를 있는 그대로(마스킹 없이) 남긴다.
    """

    bootstrap_path = get_homez_db_path(confirm=True).resolve()
    engine_path = get_engine_db_path()

    if engine_path is None or engine_path != bootstrap_path:
        logger.error(
            "[%s] DB 경로 불일치 감지 — 이 작업을 중단합니다: "
            "bootstrap=%s, session_engine=%s",
            ERROR_CODE_DB_PATH_MISMATCH, bootstrap_path, engine_path,
        )
        raise InternalServerException(
            f"[{ERROR_CODE_DB_PATH_MISMATCH}] 내부 DB 경로 설정이 "
            "일치하지 않아 이 작업을 안전하게 수행할 수 없습니다. "
            f"bootstrap={bootstrap_path}, session_engine={engine_path}",
        )

    return bootstrap_path


__all__ = [
    "assert_bootstrap_path_matches_engine",
    "ERROR_CODE_DB_PATH_MISMATCH",
]
