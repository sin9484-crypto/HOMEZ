"""
=========================================================
Homez OS

File : tools/rc_baseline_manifest.py

출시 후보 검증 기준선 목록 생성기(2026-10-06, 68차). 변경·신규 파일 전체의 파일별 SHA-256,
분류(제품/프런트/Migration/테스트/문서/스킬/도구/기타), 분류별 집계 해시, 실행 환경을 한 파일로 남긴다.

67차에서 발견한 결함의 구조적 방지: 이전 스크립트는 `git status --short` 출력 전체에 `strip()`을
적용해 **첫 줄의 앞 공백(" M ...")이 사라지면서 첫 파일 경로가 깨져 목록에서 빠졌다.** 이 모듈은
`git status --porcelain=v1 -z --untracked-files=all`(NUL 구분, 따옴표·이스케이프 없음)을 바이트로 받아
항목 단위로 파싱하며 어떤 문자열에도 `strip()`을 적용하지 않는다. 이름 변경/복사 항목의 원래 경로
토큰도 처리한다.

수정 시각은 기록하되 **참고용**이다 — 내용 일치의 증명은 SHA-256이다.
=========================================================
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

CATEGORY_ORDER = ("product", "frontend", "migration", "test", "doc", "skill", "tool", "other")
# 집계 해시에 쓰는 "코드" 범주 = 문서를 뺀 전부
CODE_CATEGORIES = tuple(c for c in CATEGORY_ORDER if c != "doc")


@dataclass(frozen=True)
class StatusEntry:
    xy: str                  # porcelain v1의 두 글자 상태(앞 공백 보존: " M", "??", "A " 등)
    path: str
    orig_path: str | None = None   # 이름 변경/복사의 원래 경로

    @property
    def kind(self) -> str:
        x, y = self.xy
        if self.xy == "??":
            return "untracked"
        if "R" in self.xy:
            return "renamed"
        if "C" in self.xy:
            return "copied"
        if "D" in self.xy:
            return "deleted"
        if "A" in self.xy:
            return "added"
        if "M" in self.xy or "T" in self.xy:
            return "modified"
        return "other"


def parse_porcelain_z(raw: bytes) -> list[StatusEntry]:
    """`git status --porcelain=v1 -z` 출력을 항목으로 파싱한다. 항목은 `XY<공백>경로\\0`이고,
    X 또는 Y가 R/C이면 바로 뒤 토큰이 원래 경로다. 문자열을 다듬지(strip) 않는다."""

    entries: list[StatusEntry] = []
    tokens = raw.split(b"\0")
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if not token:
            continue
        text = token.decode("utf-8", errors="surrogateescape")
        if len(text) < 4 or text[2] != " ":
            raise ValueError(f"porcelain -z 항목 형식이 올바르지 않다: {text[:30]!r}")
        xy, path = text[:2], text[3:]
        orig = None
        if ("R" in xy or "C" in xy) and i < len(tokens):
            orig = tokens[i].decode("utf-8", errors="surrogateescape")
            i += 1
        entries.append(StatusEntry(xy, path, orig))
    return entries


def git_status_entries(repo: Path, git: str = "git") -> list[StatusEntry]:
    proc = subprocess.run(
        [git, "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True, check=True,
    )
    return parse_porcelain_z(proc.stdout)


def categorize(path: str) -> str:
    p = path.replace("\\", "/")
    if p.startswith("migrations/"):
        return "migration"
    if p.startswith("tests/"):
        return "test"
    if p.startswith("docs/"):
        return "doc"
    if p.startswith("app/web/"):
        return "frontend"
    if p.startswith("app/"):
        return "product"
    if p.startswith(".claude/"):
        return "skill"
    if p.startswith(("tools/", "scripts/")):
        return "tool"
    return "other"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class ManifestRow:
    kind: str
    category: str
    path: str
    size: int | None
    sha256: str | None
    mtime: str | None        # 참고용
    orig_path: str | None = None


def build_rows(repo: Path, exclude: set[str], exclude_prefixes: tuple[str, ...] = ()) -> list[ManifestRow]:
    """`exclude`는 정확한 상대 경로, `exclude_prefixes`는 경로 접두사(예: 다른 작업자의 `docs/research/`)."""

    rows: list[ManifestRow] = []
    for entry in git_status_entries(repo):
        rel = entry.path.replace("\\", "/")
        if rel in exclude or rel.startswith(exclude_prefixes):
            continue
        full = repo / rel
        if entry.kind == "deleted" or not full.is_file():
            rows.append(ManifestRow(entry.kind, categorize(rel), rel, None, None, None, entry.orig_path))
            continue
        rows.append(ManifestRow(
            entry.kind, categorize(rel), rel, full.stat().st_size, file_sha256(full),
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(full.stat().st_mtime)),
            entry.orig_path,
        ))
    return sorted(rows, key=lambda r: (CATEGORY_ORDER.index(r.category), r.path))


def aggregate(rows: list[ManifestRow], categories: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    for r in sorted((r for r in rows if r.category in categories), key=lambda r: r.path):
        h.update(r.path.encode("utf-8", errors="surrogateescape"))
        h.update((r.sha256 or f"<{r.kind}>").encode())
    return h.hexdigest()


def environment_lines(repo: Path, db_paths: list[Path]) -> list[str]:
    def run(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace").stdout.strip()
        except OSError:
            return "(실행 불가)"

    lines = [
        f"OS: {platform.platform()}",
        f"Python: {sys.version.split()[0]} ({sys.executable})",
        f"Node: {run('node', '--version')} ({shutil.which('node')})",
        f"git: {run('git', '--version')}",
    ]
    for pkg in ("SQLAlchemy", "fastapi", "starlette", "uvicorn", "pydantic", "requests", "PyInstaller"):
        try:
            lines.append(f"{pkg}: {md.version(pkg)}")
        except md.PackageNotFoundError:
            lines.append(f"{pkg}: (미설치)")
    freeze = run(sys.executable, "-m", "pip", "freeze").splitlines()
    lines.append(
        f"pip freeze 항목 수: {len(freeze)} / sha256: "
        f"{hashlib.sha256(chr(10).join(sorted(freeze)).encode()).hexdigest()}")
    for name in ("requirements.txt", "tests/support/regression_guard/sitecustomize.py"):
        f = repo / name
        if f.is_file():
            lines.append(f"{name} sha256: {file_sha256(f)}")
    lines.append("HOMEZ_* 환경변수(이름만, 값 미기록): "
                 + (", ".join(sorted(k for k in os.environ if k.upper().startswith("HOMEZ_"))) or "(없음)"))
    lines.append("DATABASE_URL 설정 여부: " + ("설정됨" if os.environ.get("DATABASE_URL") else "미설정"))
    for p in db_paths:
        if p.is_file():
            lines.append(f"DB {p.name}: size={p.stat().st_size} sha256={file_sha256(p)}")
        else:
            lines.append(f"DB {p}: (파일 없음)")
    return lines


def render(repo: Path, rows: list[ManifestRow], exclude: set[str], db_paths: list[Path],
           title: str, exclude_prefixes: tuple[str, ...] = ()) -> str:
    head = run_git(repo, "rev-parse", "HEAD")
    out = [
        f"# {title}",
        "# 내용 일치의 증명은 sha256이다. 수정 시각(mtime)은 참고용이다.",
        "# 제외: " + (", ".join(sorted(exclude) + [f"{p}* (접두사)" for p in exclude_prefixes]) or "(없음)"),
        "",
        f"생성 시각: {time.strftime('%Y-%m-%d %H:%M:%S')} (로컬)",
        f"HEAD: {head}",
        f"origin/main: {run_git(repo, 'rev-parse', 'origin/main')}",
        "생성기: tools/rc_baseline_manifest.py (git status --porcelain=v1 -z, strip 미사용)",
        "",
        "## 실행 환경",
        *environment_lines(repo, db_paths),
        "",
        f"## 대상 파일 {len(rows)}개",
    ]
    for cat in CATEGORY_ORDER:
        n = sum(1 for r in rows if r.category == cat)
        if n:
            out.append(f"  {cat}: {n}개, 분류 집계 sha256 {aggregate(rows, (cat,))}")
    out.append(f"코드 집계 sha256 (문서 제외): {aggregate(rows, CODE_CATEGORIES)}")
    out.append(f"전체 집계 sha256 (문서 포함): {aggregate(rows, CATEGORY_ORDER)}")
    out.append("")
    out.append("kind\tcategory\tsize\tsha256\tmtime(참고)\tpath")
    for r in rows:
        extra = f"  (원래 경로: {r.orig_path})" if r.orig_path else ""
        out.append(f"{r.kind}\t{r.category}\t{r.size}\t{r.sha256}\t{r.mtime}\t{r.path}{extra}")
    return "\n".join(out) + "\n"


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.rstrip("\r\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="출시 후보 검증 기준선 목록 생성")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--out", required=True, help="목록을 쓸 파일(목록 자신은 자동 제외)")
    ap.add_argument("--exclude", action="append", default=[], help="제외할 상대 경로(반복 가능)")
    ap.add_argument("--exclude-prefix", action="append", default=[],
                    help="제외할 경로 접두사(반복 가능, 예: docs/research/ — 다른 작업자의 조사 문서)")
    ap.add_argument("--db", action="append", default=[], help="지문을 남길 DB 파일(읽기만, 반복 가능)")
    ap.add_argument("--title", default="HOMEZ 출시 후보 검증 기준선 목록")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    out = Path(args.out)
    try:
        out_rel = out.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        out_rel = None
    exclude = set(args.exclude) | ({out_rel} if out_rel else set())
    prefixes = tuple(args.exclude_prefix)
    rows = build_rows(repo, exclude, prefixes)
    text = render(repo, rows, exclude, [Path(p) for p in args.db], args.title, prefixes)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"written {out} files={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
