"""
=========================================================
Homez OS

File : app/domains/guides/service.py

HOMEZ V6.5 사용자 가이드 서비스 — DB를 쓰지 않는다(가이드는 저장소에
커밋된 정적 파일이다). 매 요청마다 실제 파일시스템을 확인해 가용
상태를 계산한다 — 나중에 영상 파일이 추가되면 이 코드를 다시 고치지
않아도 다음 요청부터 자동으로 "재생 가능"으로 바뀐다.

보안 핵심은 `resolve_safe_path()`다:
  1) `docs/guides/` 루트 기준으로만 상대 경로를 해석한다.
  2) 정규화한 절대 경로가 실제로 그 루트 "안"에 있는지 확인한다
     (`..`로 벗어나거나 절대경로를 주입해도 벗어날 수 없다).
  3) 확장자가 allowlist(`ALLOWED_FILE_EXTENSIONS`)에 있는지 확인한다.
  4) 파일이 실제로 존재하는지 확인한다.
  하나라도 실패하면 None을 반환한다 — 호출부(router.py)가 404로
  통일해서 응답한다(경로 조작 시도와 단순 미존재를 구분해 정보를
  더 주지 않는다).
=========================================================
"""

from __future__ import annotations

from pathlib import Path

from app.desktop.paths import get_repo_root
from app.domains.guides.constants import ALLOWED_FILE_EXTENSIONS
from app.domains.guides.constants import GUIDE_REGISTRY
from app.domains.guides.schema import GuideAssetStatus
from app.domains.guides.schema import GuideSummary


def get_guides_root() -> Path:

    return get_repo_root() / "docs" / "guides"


def _asset_status(root: Path, relative_path: str | None) -> GuideAssetStatus:

    if not relative_path:
        return GuideAssetStatus(available=False, path=None)

    resolved = resolve_safe_path(relative_path, root=root)
    if resolved is None:
        return GuideAssetStatus(available=False, path=None)

    # 클라이언트에는 로컬 파일시스템 절대경로를 절대 노출하지 않는다
    # — 항상 요청 때 쓴 것과 같은 형태의 상대 경로만 돌려준다.
    return GuideAssetStatus(available=True, path=relative_path)


def _locale_map(root: Path, locale_paths: dict[str, str]) -> dict[str, GuideAssetStatus]:

    return {
        locale: _asset_status(root, path)
        for locale, path in locale_paths.items()
    }


def list_guides() -> list[GuideSummary]:
    # Audit(2026-08-21, AG-0) — 되돌림: 정적 가이드 문서 목록 열람은
    # 파일시스템 서빙일 뿐 AI 판단이 아니다 — AI Capability Registry로
    # 게이트하지 않는다("Dashboard 단순 조회가 AI 비활성 때문에
    # 실패하면 안 된다"는 원칙과 동일). USER_GUIDANCE는 향후 실제
    # "대화형 안내" AI 기능(아직 미구현)에만 적용한다.

    root = get_guides_root()

    summaries = [
        GuideSummary(
            id=g["id"],
            order=g["order"],
            title_key=g["title_key"],
            description_key=g["description_key"],
            estimated_minutes=g["estimated_minutes"],
            category=g["category"],
            document=_locale_map(root, g["document"]),
            pdf=_locale_map(root, g["pdf"]),
            video=_locale_map(root, g["video"]),
            subtitles=_locale_map(root, g["subtitles"]),
            screenshots=[
                s for s in g["screenshots"]
                if resolve_safe_path(s, root=root) is not None
            ],
        )
        for g in GUIDE_REGISTRY
    ]

    return sorted(summaries, key=lambda s: s.order)


def get_guide(guide_id: str) -> GuideSummary | None:

    for summary in list_guides():
        if summary.id == guide_id:
            return summary
    return None


def resolve_safe_path(relative_path: str, *, root: Path | None = None) -> Path | None:
    """
    `docs/guides/` 아래 실제 파일만 안전하게 가리키는 절대 경로를
    반환한다. 경로 탈출·허용되지 않은 확장자·미존재 파일이면 모두
    None을 반환한다(호출부가 전부 동일하게 404 처리).
    """

    if not relative_path:
        return None

    guides_root = (root or get_guides_root()).resolve()

    # 빈 문자열/공백/널 바이트 등 명백히 비정상적인 입력은 즉시 거부한다.
    if "\x00" in relative_path:
        return None

    candidate = (guides_root / relative_path).resolve()

    try:
        candidate.relative_to(guides_root)
    except ValueError:
        # ".."로 루트를 벗어나거나 절대경로가 주입된 경우.
        return None

    if candidate.suffix.lower() not in ALLOWED_FILE_EXTENSIONS:
        return None

    if not candidate.is_file():
        return None

    return candidate


__all__ = [
    "get_guides_root",
    "list_guides",
    "get_guide",
    "resolve_safe_path",
]
