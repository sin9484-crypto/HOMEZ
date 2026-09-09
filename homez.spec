# -*- mode: python ; coding: utf-8 -*-
"""
=========================================================
Homez OS

File : homez.spec

Gate Y-6(2026-08-12) — PyInstaller 빌드 스펙(초안). 실제 실행 파일을
만들려면:

    pip install -r requirements-build.txt
    pyinstaller homez.spec --noconfirm

이 세션에서는 실행하지 않았다(패키징 산출물을 만드는 것 자체가
새 바이너리를 디스크에 남기는 행위이고, 서명·배포 파이프라인은
아직 없어 검증되지 않은 실행 파일을 만드는 것이 오히려 혼란을
줄 수 있다 — 스펙 파일과 경로 계약만 이번 단계의 산출물이다).

기존 `app/desktop/paths.py`의 `is_frozen()`/`get_data_dir()`/
`get_logs_dir()`/`get_backups_dir()`/`get_config_dir()` 분기가
이미 `%LOCALAPPDATA%\\HOMEZ\\*` 경로 계약을 구현해 두었다 —
이 스펙 파일은 그 계약이 실제로 성립하도록 필요한 정적 자산
(`app/web/**`, `migrations/*.sql`, `assets/*`)을 실행 파일과 함께
묶는 것과, onedir 배포 형태를 확정하는 역할만 한다.

onefile이 아니라 onedir(COLLECT)을 쓴 이유: onefile은 실행마다
임시 디렉터리에 압축을 풀어야 해서(수백 MB급 sqlalchemy/uvicorn
포함 시) 시작 시간이 눈에 띄게 느려지고, 일부 백신이 "실행 파일이
자기 자신을 임시 폴더에 풀어 실행"하는 패턴 자체를 의심스럽게
본다 — Desktop 상시 실행 앱에는 onedir이 더 적합하다는 판단.
=========================================================
"""

from pathlib import Path

block_cipher = None

REPO_ROOT = Path(SPECPATH)  # noqa: F821 (PyInstaller가 주입하는 전역)

_WEB_DIR = REPO_ROOT / "app" / "web"

datas = [
    (str(_WEB_DIR / "console.html"), "app/web"),
    (str(_WEB_DIR / "console.css"), "app/web"),
    (str(_WEB_DIR / "console.js"), "app/web"),
    (str(_WEB_DIR / "i18n"), "app/web/i18n"),
    (str(_WEB_DIR / "assets"), "app/web/assets"),
    (str(_WEB_DIR / "vendor"), "app/web/vendor"),
    (str(REPO_ROOT / "migrations"), "migrations"),
    (str(REPO_ROOT / "assets"), "assets"),
]

hiddenimports = [
    # 2026-08-16 Live Gate 3 — app/desktop/server.py:294가
    # uvicorn.Config("app.main:app", ...)로 문자열 참조만 하기 때문에
    # PyInstaller 정적 분석이 app.main과 그 하위 20여 개 라우터 모듈
    # 전체를 놓친다(E1002 25초 타임아웃의 근본원인, 실측 확인됨).
    # "app.main"을 명시하면 PyInstaller가 app/main.py의 실제 import문을
    # 따라가 전체 라우터 트리를 자동으로 번들에 포함시킨다.
    "app.main",
    # uvicorn은 조건부 import를 많이 쓰므로 PyInstaller의 정적 분석이
    # 놓치는 경로를 명시적으로 나열한다(실제 프레임워크 스택 재사용,
    # app/desktop/server.py가 그대로 이 이름들을 통해 uvicorn을
    # 구동한다).
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "sqlalchemy.dialects.sqlite",
]

a = Analysis(  # noqa: F821
    ["app/desktop/main.py"],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 개발 전용 도구 — 런타임에 전혀 필요 없다(requirements.txt에도
        # 없음, Gate F-10A와 동일 원칙: Pillow도 런타임 의존성이
        # 아니었다).
        "pytest",
        "unittest",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Homez",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 압축은 쓰지 않는다 — 일부 백신 엔진이 UPX로 압축된 실행
    # 파일을 그 압축 방식 자체만으로 의심스러운 것으로 오탐하는
    # 사례가 흔하다(신생 미서명 실행 파일에는 특히 더).
    upx=False,
    console=False,  # GUI 앱 — 콘솔 창을 띄우지 않는다.
    icon=str(REPO_ROOT / "assets" / "homez-app.ico"),
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Homez",
)
