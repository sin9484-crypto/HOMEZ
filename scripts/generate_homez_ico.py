"""
=========================================================
Homez OS

File : scripts/generate_homez_ico.py

HOMEZ Windows 아이콘(assets/homez-app.ico) 재현 생성 도구
(2026-08-07, Gate F-10A).

승인된 로고 원본(app/web/assets/homez-logo.png)에서 Windows가 요구하는
9개 해상도(16/20/24/32/40/48/64/128/256)를 갖춘 .ico를 결정적으로
생성한다. 새 로고를 디자인하지 않는다 — 같은 원본을 고품질
리샘플링만 한다.

이 스크립트는 다음을 절대 하지 않는다.
  - HOMEZ 앱 시작 시 자동 실행되지 않는다(개발자가 필요할 때만
    수동 실행하는 1회성 자산 생성 도구다).
  - 로고 원본이나 아이콘을 네트워크에서 내려받지 않는다 — 저장소
    안의 파일만 읽는다.
  - 원본 PNG(app/web/assets/homez-logo.png)를 수정하지 않는다.

의존성: Pillow(PIL). 이 스크립트를 실행할 때만 필요한 선택적
개발 도구 의존성이며, 운영 requirements.txt에는 포함하지 않는다
(HOMEZ 앱 런타임 코드는 어디에서도 PIL을 import하지 않는다 —
전수 확인됨, docs/V6_EXECUTION_LEDGER.md Gate F-10A 참고). 실행 전
별도로 설치한다.

    venv\\Scripts\\python.exe -m pip install Pillow

사용 예:
    venv\\Scripts\\python.exe scripts\\generate_homez_ico.py

같은 원본으로 반복 실행하면 항상 동일한 바이트 결과가 나온다
(Pillow ICO writer가 결정적이므로 별도 시드 처리가 필요 없다).
"""

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print(
        "Pillow가 설치돼 있지 않습니다. 먼저 설치하세요:\n"
        "  venv\\Scripts\\python.exe -m pip install Pillow",
        file=sys.stderr,
    )
    raise SystemExit(1)


REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PNG = REPO_ROOT / "app" / "web" / "assets" / "homez-logo.png"
TARGET_ICO = REPO_ROOT / "assets" / "homez-app.ico"

REQUIRED_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def generate() -> None:

    if not SOURCE_PNG.exists():
        raise SystemExit(f"승인된 로고 원본을 찾을 수 없습니다: {SOURCE_PNG}")

    src = Image.open(SOURCE_PNG)

    if src.size[0] != src.size[1]:
        raise SystemExit(f"원본이 정사각형이 아닙니다: {src.size}")

    if src.mode != "RGBA":
        raise SystemExit(f"원본에 알파 채널이 없습니다(mode={src.mode}).")

    max_required = max(REQUIRED_SIZES)
    if src.size[0] < max_required:
        raise SystemExit(
            f"원본 해상도({src.size[0]}px)가 요구 최대 해상도"
            f"({max_required}px)보다 작습니다.",
        )

    # Pillow ICO writer는 sizes 각 항목마다 base 이미지(src, 원본
    # 그대로)에서 자체적으로 LANCZOS thumbnail을 생성한다 — base를
    # 미리 축소해서 넘기면 그보다 큰 요청 해상도가 조용히 스킵된다.
    TARGET_ICO.parent.mkdir(parents=True, exist_ok=True)
    src.save(
        TARGET_ICO,
        format="ICO",
        sizes=[(s, s) for s in REQUIRED_SIZES],
    )

    print(f"생성 완료: {TARGET_ICO} ({len(REQUIRED_SIZES)}개 해상도)")


if __name__ == "__main__":
    generate()
