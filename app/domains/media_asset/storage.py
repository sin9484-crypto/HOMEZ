"""
=========================================================
Homez OS

File : app/domains/media_asset/storage.py

실제 이미지 바이트를 디스크에 쓰고 읽는 얇은 계층. storage_path는
항상 image_validation.py::build_storage_path()가 만든 서버 계산
값이지만, 여기서도 한 번 더 방어한다 — 최종적으로 계산된 절대
경로가 base_dir 밖으로 벗어나면(어떤 경로든) 즉시 차단한다(defense
in depth, path traversal).

원본 파일은 절대 덮어쓰지 않는다 — write_media_file()은 같은
경로에 이미 파일이 있으면 아무것도 하지 않고 그대로 둔다(신규
바이트를 무시하는 것이 아니라, 같은 storage_path는 같은
sha256로부터만 계산되므로 내용이 달라질 수 없다는 전제 — 그래도
방어적으로 덮어쓰지 않는다).
=========================================================
"""

from pathlib import Path

from app.core.exceptions import BadRequestException


def _resolve_safe_path(base_dir: Path, storage_path: str) -> Path:

    if storage_path.startswith("/") or storage_path.startswith("\\"):
        raise BadRequestException("storage_path는 절대 경로일 수 없습니다.")

    candidate = (base_dir / storage_path).resolve()
    base_resolved = base_dir.resolve()

    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise BadRequestException(
            "storage_path가 저장소 기준 경로를 벗어납니다(path traversal 차단).",
        ) from exc

    return candidate


def write_media_file(base_dir: Path, storage_path: str, data: bytes) -> Path:

    target = _resolve_safe_path(base_dir, storage_path)

    if target.exists():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    return target


def read_media_file(base_dir: Path, storage_path: str) -> bytes:

    target = _resolve_safe_path(base_dir, storage_path)

    return target.read_bytes()


def media_file_exists(base_dir: Path, storage_path: str) -> bool:

    return _resolve_safe_path(base_dir, storage_path).exists()


__all__ = [
    "write_media_file",
    "read_media_file",
    "media_file_exists",
]
