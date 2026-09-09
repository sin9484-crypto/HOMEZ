"""
=========================================================
Homez OS

File : app/desktop/paths.py

HOMEZ Desktop Shell — 경로 계약

개발 단계(이번 Phase)에서는 항상 현재 프로젝트 경로를 사용한다 —
homez.db를 옮기거나 복사하지 않고, 새 DB를 만들지 않는다. 이 파일은
"향후 PyInstaller로 패키징됐을 때 데이터/로그/백업/설정 경로를
%LOCALAPPDATA%\\HOMEZ\\* 로 분리할 수 있다"는 계약만 미리 만들어
둔다 — 이번 단계에서 실제로 그 경로를 사용하거나 만들지는 않는다
(is_frozen()이 True가 되는 것은 PyInstaller로 빌드된 실행 파일에서
뿐이며, 이번 Phase는 그 빌드를 하지 않는다).
=========================================================
"""

import os
import sys
from pathlib import Path


class ProductionDbAccessNotConfirmedError(RuntimeError):
    """
    2026-08-11 Gate V-1 — 2026-08-10 사고(경로 격리 없이 실행된
    bootstrap_environment()가 실제 homez.db에 승인 없는 backfill
    이력을 남김, docs/V6_EXECUTION_LEDGER.md "Gate U 사고 정정" 절
    참고) 재발 방지. get_homez_db_path()는 confirm=True 없이는 실제
    운영 DB 경로를 반환하지 않는다 — 공식 Desktop 부팅 흐름과 승인된
    Migration Runner 경로에서만 명시적으로 사용해야 한다. 테스트·
    임시 스크립트·단순 import는 항상 자체 임시 DB 경로를 명시해야
    하며, 이 예외를 받으면 그것이 올바른 신호다(우회하지 않는다).
    """


def is_frozen() -> bool:
    """PyInstaller 등으로 패키징된 실행 파일에서 실행 중인지 여부."""

    return getattr(sys, "frozen", False)


def get_repo_root() -> Path:
    """개발 모드 기준 저장소 루트(app/desktop/paths.py 기준 두 단계 위)."""

    return Path(__file__).resolve().parent.parent.parent


def _frozen_data_root() -> Path:
    """
    2026-08-24 Section 7 후속(패키징 격리 재작업) — 패키징 모드에서
    data/logs/backups/config/media가 전부 공유하는 루트 디렉터리.

    기본값은 기존 계약 그대로 `%LOCALAPPDATA%\\HOMEZ`다 — 아무 것도
    설정하지 않은 일반 사용자 실행(공식 배포)에서는 이 함수가 항상
    이 값만 반환하며, 동작이 전혀 바뀌지 않는다.

    `HOMEZ_DATA_ROOT` 환경변수가 명시적으로 설정돼 있으면(빈 문자열
    제외) 그 값을 대신 쓴다 — 격리 설치 검증 전용 오버라이드다. 이
    이름은 일반적인 Windows 환경변수도, HOMEZ의 다른 어떤 기존 로직도
    자동으로 설정하지 않는 이름이므로, 이 값이 존재한다는 사실
    자체가 "의도적으로 격리 테스트 중"이라는 명시적 신호다 — 실수로
    켜질 수 있는 조건이 아니다. 값이 있는데 빈 문자열이면 설정
    자체를 무시하고 기본값(공식 경로)으로 fail-safe한다(빈 값으로
    조용히 잘못된 경로를 만들지 않는다).
    """

    import os

    override = os.environ.get("HOMEZ_DATA_ROOT", "").strip()
    if override:
        return Path(override)

    return Path(os.environ["LOCALAPPDATA"]) / "HOMEZ"


def get_data_dir() -> Path:
    """
    homez.db 등 데이터가 위치하는 디렉터리.

    개발 모드: 저장소 루트(homez.db가 실제로 있는 곳) 그대로 사용한다
    — 옮기거나 복사하지 않는다.
    패키징 모드: `_frozen_data_root() / "data"`(기본값
    `%LOCALAPPDATA%\\HOMEZ\\data` — `HOMEZ_DATA_ROOT`로만 격리 가능).
    """

    if is_frozen():
        return _frozen_data_root() / "data"

    return get_repo_root()


def get_homez_db_path(*, confirm: bool = False) -> Path:
    """
    현재 사용할 homez.db의 실제 경로.

    개발 모드에서는 항상 저장소 루트의 기존 homez.db를 가리킨다 —
    이 함수 자체는 파일을 생성·이동·복사하지 않는다(경로만 계산).

    2026-08-11 Gate V-1 — confirm=True를 명시하지 않으면 즉시
    ProductionDbAccessNotConfirmedError를 던진다(ProductionDb
    AccessNotConfirmedError 문서 참고). 호출자는 이 값이 실제
    운영 DB를 가리켜도 안전한 위치(공식 Desktop 부팅 흐름, 승인된
    Migration Runner 경로)에서만 confirm=True를 넘겨야 한다.
    """

    if not confirm:
        raise ProductionDbAccessNotConfirmedError(
            "get_homez_db_path()는 confirm=True 없이는 실제 운영 DB "
            "경로를 반환하지 않습니다. 테스트·임시 스크립트라면 자체 "
            "임시 DB 경로를 명시하세요 — 이 값이 정말 필요한 공식 "
            "경로라면 get_homez_db_path(confirm=True)를 명시적으로 "
            "호출해야 합니다.",
        )

    return get_data_dir() / "homez.db"


def get_logs_dir() -> Path:
    """
    Desktop 런처 로그 디렉터리.

    개발 모드: 저장소의 기존 storage\\logs (scripts/start_homez.ps1과
    동일한 위치, 로그 정책을 이원화하지 않기 위함).
    패키징 모드(향후): %LOCALAPPDATA%\\HOMEZ\\logs.
    """

    if is_frozen():
        return _frozen_data_root() / "logs"

    return get_repo_root() / "storage" / "logs"


def get_backups_dir() -> Path:
    """
    향후 자동/수동 백업 저장 위치(계약만 — 이번 단계는 백업을 자동
    생성하지 않는다).
    """

    if is_frozen():
        return _frozen_data_root() / "backups"

    return get_repo_root() / "storage" / "backups"


def get_config_dir() -> Path:
    """향후 사용자별 설정 저장 위치(계약만 — 이번 단계는 사용하지 않음)."""

    if is_frozen():
        return _frozen_data_root() / "config"

    return get_repo_root() / "storage" / "config"


def get_assets_dir() -> Path:
    """아이콘 등 정적 자산(개발/패키징 공통 — 실행 파일에 번들됨)."""

    return get_repo_root() / "assets"


def get_media_dir() -> Path:
    """
    상품 이미지(원본/AI 생성본/채널 변환본) 실제 저장 위치
    (app/domains/media_asset). 다른 storage/* 디렉터리와 동일한
    개발/패키징 경로 분리 계약을 따른다 — homez.db를 실행 파일에
    내장하지 않는 것과 같은 원칙으로, 사용자가 생성한 이미지도
    실행 파일과 분리된 사용자 데이터 위치에 둔다.

    `HOMEZ_TEST_MEDIA_ROOT`가 설정돼 있으면 그 경로를 그대로
    쓴다(2026-08-31, E2E/격리 테스트 전용 — `DATABASE_URL`을 격리
    DB로 돌리는 것과 동일한 원칙으로, 테스트 fixture 이미지가 실제
    저장소(storage/media)에 섞이지 않게 한다). 이 환경변수가 없으면
    기존 동작과 완전히 동일하다.

    2026-08-31 Phase 7.6 감사 수정 — 이 오버라이드는 **개발 모드
    (is_frozen()==False)에서만** 동작한다. `HOMEZ_DATA_ROOT`(패키징
    모드 전용, `_frozen_data_root()` 참고)와 정확히 반대 방향의
    계약이다 — 실제 설치된 실행 파일(is_frozen()==True)은 이
    환경변수가 어떤 값으로 설정돼 있어도 절대 참조하지 않는다.
    E2E 테스트는 항상 `python -m uvicorn ...`(개발 모드)으로만
    실행되므로 이 제한으로 테스트 기능이 줄지 않으며, 반대로 이
    제한이 없으면 실 사용자 PC에 남거나 실수로 설정된 환경변수 하나로
    운영 상품 이미지 경로가 임의의(공격자 제어 가능한) 위치로
    조용히 바뀔 수 있었다 — 그 경로를 막는다(fail-closed).
    """

    if not is_frozen():
        override = os.environ.get("HOMEZ_TEST_MEDIA_ROOT", "").strip()
        if override:
            return Path(override)

    if is_frozen():
        return _frozen_data_root() / "media"

    return get_repo_root() / "storage" / "media"


__all__ = [
    "is_frozen",
    "get_repo_root",
    "get_data_dir",
    "get_homez_db_path",
    "get_logs_dir",
    "get_backups_dir",
    "get_config_dir",
    "get_assets_dir",
    "get_media_dir",
]
