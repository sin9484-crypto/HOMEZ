"""
=========================================================
Homez OS

File : tests/test_rc_baseline_manifest.py

`tools/rc_baseline_manifest.py`(출시 후보 기준선 목록 생성기) 집중 테스트(2026-10-06, 68차).
67차 결함 재발 방지: `git status --short` 출력에 `strip()`을 적용하면 첫 줄의 앞 공백(" M ...")이
사라져 첫 파일 경로가 깨지고 목록에서 빠졌다. 임시 git 저장소로 첫 항목·공백/한글 경로·수정·신규·
삭제·이름 변경·신규 폴더 펼침·제외·내용/시각 구분을 확인한다. 실제 저장소·DB는 건드리지 않는다.
=========================================================
"""

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rc_baseline_manifest", ROOT / "tools" / "rc_baseline_manifest.py")
manifest = importlib.util.module_from_spec(_spec)
sys.modules["rc_baseline_manifest"] = manifest   # dataclass가 모듈을 sys.modules에서 찾는다
_spec.loader.exec_module(manifest)

GIT = shutil.which("git")


class ParsePorcelainZTestCase(unittest.TestCase):

    def test_leading_space_of_the_first_entry_is_preserved_and_the_path_is_exact(self):
        """67차 결함 재현: 앞 공백이 있는 첫 항목의 경로가 한 글자도 깨지지 않는다."""

        raw = b" M app/domains/channel_policy/service.py\0?? docs/new file.md\0"
        entries = manifest.parse_porcelain_z(raw)
        self.assertEqual(entries[0].xy, " M")
        self.assertEqual(entries[0].path, "app/domains/channel_policy/service.py")
        self.assertEqual(entries[0].kind, "modified")
        self.assertEqual(entries[1].path, "docs/new file.md")
        self.assertEqual(entries[1].kind, "untracked")

        # 옛 방식(출력 전체 strip + l[3:])은 같은 입력에서 첫 경로를 깬다 — 이 테스트가 그 결함을 잡는다
        legacy_first = raw.decode().replace("\0", "\n").strip().splitlines()[0][3:]
        self.assertNotEqual(legacy_first, "app/domains/channel_policy/service.py")

    def test_rename_and_copy_consume_the_original_path_token(self):
        raw = b"R  new name.py\0old name.py\0 M other.py\0C  copy.py\0src.py\0A  added.py\0 D gone.py\0"
        entries = manifest.parse_porcelain_z(raw)
        self.assertEqual(
            [(e.xy, e.path, e.orig_path, e.kind) for e in entries],
            [("R ", "new name.py", "old name.py", "renamed"), (" M", "other.py", None, "modified"),
             ("C ", "copy.py", "src.py", "copied"), ("A ", "added.py", None, "added"),
             (" D", "gone.py", None, "deleted")],
        )

    def test_korean_and_special_characters_survive_without_quoting(self):
        raw = "?? docs/한글 문서 (1).md\0 M tests/test_é.py\0".encode("utf-8")
        entries = manifest.parse_porcelain_z(raw)
        self.assertEqual([e.path for e in entries], ["docs/한글 문서 (1).md", "tests/test_é.py"])

    def test_malformed_entry_fails_loudly_instead_of_dropping_files(self):
        with self.assertRaises(ValueError):
            manifest.parse_porcelain_z(b"X\0")
        self.assertEqual(manifest.parse_porcelain_z(b""), [])


class CategorizeTestCase(unittest.TestCase):

    def test_categories(self):
        c = manifest.categorize
        self.assertEqual(c("app/domains/x/service.py"), "product")
        self.assertEqual(c("app/web/console.js"), "frontend")
        self.assertEqual(c("migrations/20261005_00_x.sql"), "migration")
        self.assertEqual(c("tests/test_x.py"), "test")
        self.assertEqual(c("docs/a.md"), "doc")
        self.assertEqual(c(".claude/skills/x/SKILL.md"), "skill")
        self.assertEqual(c("tools/rc_baseline_manifest.py"), "tool")
        self.assertEqual(c("README.md"), "other")


@unittest.skipUnless(GIT, "git 실행 파일이 없어 건너뜀")
class TemporaryRepoTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="homez_manifest_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = Path(self.tmp)
        self._git("init", "-q")
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "tester")
        self._git("config", "core.autocrlf", "false")
        for rel, text in {
            "app/a_first.py": "1\n", "app/domains/channel_policy/service.py": "base\n",
            "tests/test_one.py": "t\n", "docs/keep.md": "d\n", "docs/to rename.md": "r\n",
            "migrations/20260101_00_x.sql": "-- x\n", "docs/to delete.md": "x\n",
        }.items():
            self._write(rel, text)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "base")

    def _git(self, *args):
        subprocess.run([GIT, "-C", self.tmp, *args], check=True, capture_output=True)

    def _write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")

    def _rows(self, exclude=()):
        return {r.path: r for r in manifest.build_rows(self.repo, set(exclude))}

    def test_first_status_entry_and_all_change_kinds_are_listed_with_exact_hashes(self):
        # 정렬상 첫 항목이 될 수정 파일(= 67차에서 빠진 경우와 같은 위치)
        self._write("app/a_first.py", "changed first\n")
        self._write("app/domains/channel_policy/service.py", "modified\n")
        self._write("docs/new file with space.md", "new\n")          # 신규(공백 포함)
        self._write("docs/한글 신규.md", "한글\n")                    # 신규(한글)
        self._write("app/newpkg/sub dir/mod.py", "x = 1\n")           # 신규 폴더 → 파일 단위로 펼침
        (self.repo / "docs/to delete.md").unlink()                     # 삭제
        self._git("mv", "docs/to rename.md", "docs/renamed here.md")   # 이름 변경(스테이징)
        rows = self._rows()

        for path in ("app/a_first.py", "app/domains/channel_policy/service.py",
                     "docs/new file with space.md", "docs/한글 신규.md",
                     "app/newpkg/sub dir/mod.py", "docs/to delete.md", "docs/renamed here.md"):
            self.assertIn(path, rows, f"누락: {path}")
        self.assertEqual(rows["app/a_first.py"].kind, "modified")
        self.assertEqual(rows["docs/new file with space.md"].kind, "untracked")
        self.assertEqual(rows["docs/to delete.md"].kind, "deleted")
        self.assertIsNone(rows["docs/to delete.md"].sha256)
        self.assertEqual(rows["docs/renamed here.md"].kind, "renamed")
        self.assertEqual(rows["docs/renamed here.md"].orig_path, "docs/to rename.md")
        # 파일별 해시는 실제 내용의 SHA-256이다
        for path in ("app/a_first.py", "docs/한글 신규.md", "app/newpkg/sub dir/mod.py"):
            expected = hashlib.sha256((self.repo / path).read_bytes()).hexdigest()
            self.assertEqual(rows[path].sha256, expected, path)
        # 분류
        self.assertEqual(rows["app/newpkg/sub dir/mod.py"].category, "product")
        self.assertEqual(rows["docs/한글 신규.md"].category, "doc")

    def test_exclusions_remove_only_the_named_files(self):
        self._write("docs/research/foreign.md", "other worker\n")
        self._write("app/a_first.py", "changed\n")
        rows = self._rows(exclude={"docs/research/foreign.md"})
        self.assertNotIn("docs/research/foreign.md", rows)
        self.assertIn("app/a_first.py", rows)
        self.assertIn("docs/research/foreign.md", self._rows())

    def test_prefix_exclusion_skips_every_file_of_another_workers_directory(self):
        self._write("docs/research/a.md", "x\n")
        self._write("docs/research/sub/b.md", "y\n")
        self._write("docs/research_notes.md", "mine\n")        # 접두사와 비슷하지만 다른 경로 — 제외하지 않는다
        rows = {r.path for r in manifest.build_rows(self.repo, set(), ("docs/research/",))}
        self.assertNotIn("docs/research/a.md", rows)
        self.assertNotIn("docs/research/sub/b.md", rows)
        self.assertIn("docs/research_notes.md", rows)

    def test_aggregates_follow_content_not_modification_time_and_split_code_from_docs(self):
        self._write("app/a_first.py", "v1\n")
        self._write("docs/note.md", "n1\n")
        base_rows = manifest.build_rows(self.repo, set())
        code1 = manifest.aggregate(base_rows, manifest.CODE_CATEGORIES)
        all1 = manifest.aggregate(base_rows, manifest.CATEGORY_ORDER)

        future = time.time() + 3600                     # 시각만 바꾼다 — 내용 불변
        os.utime(self.repo / "app/a_first.py", (future, future))
        rows = manifest.build_rows(self.repo, set())
        self.assertEqual(manifest.aggregate(rows, manifest.CODE_CATEGORIES), code1)

        self._write("docs/note.md", "n2\n")             # 문서만 변경 → 코드 집계 불변, 전체 집계 변경
        rows = manifest.build_rows(self.repo, set())
        self.assertEqual(manifest.aggregate(rows, manifest.CODE_CATEGORIES), code1)
        self.assertNotEqual(manifest.aggregate(rows, manifest.CATEGORY_ORDER), all1)

        self._write("app/a_first.py", "v2\n")           # 코드 내용 변경 → 코드 집계 변경
        rows = manifest.build_rows(self.repo, set())
        self.assertNotEqual(manifest.aggregate(rows, manifest.CODE_CATEGORIES), code1)

    def test_cli_writes_a_manifest_that_lists_every_row_and_excludes_itself(self):
        self._write("app/a_first.py", "changed\n")
        out = self.repo / "docs" / "MANIFEST.txt"
        out.parent.mkdir(exist_ok=True)
        self.assertEqual(manifest.main(["--repo", str(self.repo), "--out", str(out)]), 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("app/a_first.py", text)
        self.assertNotIn("\tdocs/MANIFEST.txt", text)      # 목록 자신은 제외
        self.assertIn("코드 집계 sha256", text)
        self.assertIn("strip 미사용", text)


if __name__ == "__main__":
    unittest.main()
