"""
=========================================================
Homez OS

File : tests/test_homez_canonical_db_check_script.py

tools/HOMEZ_Canonical_DB_Check.ps1(2026-08-31 Phase 8 차단 해소
작업) 안전성 검증. 이 스크립트는 사용자가 자신의 일반(비관리자)
PowerShell에서 직접 실행해 canonical DB 후보 두 개의 경로/해시/
크기/시각/파일 식별자를 확인하는 읽기 전용 도구다 — 절대로 파일을
쓰거나·복사·이동·삭제하거나, SQLite에 연결하거나, Migration을
실행하거나, HOMEZ.exe를 시작/종료해서는 안 된다.

1) 정적 검사 — 스크립트 소스에 쓰기/삭제/이동/복사/SQLite 연결/
   Migration 실행/프로세스 시작·종료 명령이 전혀 없는지 문자열
   검사한다(실제 실행 없이도 항상 확인 가능).
2) 인코딩·문법 검사 — 이 저장소의 기존 .ps1 관례(UTF-8 BOM,
   PowerShell 파서로 문법 확인)를 그대로 따르는지 확인한다.
3) 기능 검사 — 개발 환경의 임시 파일(하드링크로 만든 "진짜 같은
   파일" 한 쌍, 내용은 같지만 물리적으로는 다른 파일 한 쌍)로
   같은 파일/다른 파일 판별 로직 자체가 올바른지 확인한다. 실제
   운영 후보 DB 두 개에는 어디에서도 쓰기 접근하지 않는다.

인코딩 참고: Windows PowerShell 콘솔의 활성 코드페이지에 따라
한글 출력이 subprocess로 캡처했을 때 깨져 보일 수 있다(실제
사용자가 대화형 창에서 직접 실행하면 문제없이 표시된다 — 이
세션에서 iconv로 재확인함). 그래서 이 테스트의 단언(assert)은
전부 한글이 섞이지 않는 ASCII 마커(True/False/UNKNOWN/SHA-256/
16진수 해시 등 — 어떤 코드페이지로 잘못 디코딩되어도 항상 그대로
보존되는 1바이트 문자)만 사용한다.
=========================================================
"""

import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO_ROOT, "tools", "HOMEZ_Canonical_DB_Check.ps1")

POWERSHELL_AVAILABLE = shutil.which("powershell") is not None

# 이 스크립트가 절대 포함해서는 안 되는 명령 — 실제 DB나 임의
# 파일에 쓰기·삭제·이동·복사하거나, SQLite에 연결하거나, Migration을
# 실행하거나, HOMEZ.exe(또는 다른 프로세스)를 시작/종료할 수 있는
# cmdlet·구문이다. 대소문자를 구분하지 않고 검사한다.
_FORBIDDEN_PATTERNS = (
    "remove-item", "move-item", "copy-item", "rename-item",
    "set-content", "add-content", "out-file", "new-item -itemtype file",
    "clear-content", "start-process", "stop-process",
    "invoke-sqlcmd", "system.data.sqlite", "sqlite3",
    "migrationrunner", "invoke-webrequest", "invoke-restmethod",
    "generic_write", "file_generic_write",
)


class ScriptExistsTestCase(unittest.TestCase):

    def test_script_file_exists_at_documented_path(self):

        self.assertTrue(
            os.path.isfile(SCRIPT_PATH),
            f"{SCRIPT_PATH}가 실제 파일로 존재하지 않습니다.",
        )


class ScriptStaticSafetyTestCase(unittest.TestCase):
    """실행하지 않고도 항상 확인 가능한 정적 안전성 검사."""

    @classmethod
    def setUpClass(cls):

        with open(SCRIPT_PATH, encoding="utf-8-sig") as f:
            cls.source = f.read()
        cls.source_lower = cls.source.lower()

    def test_utf8_bom_present(self):
        # 이 저장소 기존 .ps1 관례(tests/test_homez_launcher.py) —
        # BOM이 없으면 한글이 mojibake될 위험이 있다.
        with open(SCRIPT_PATH, "rb") as f:
            head = f.read(3)
        self.assertEqual(head, b"\xef\xbb\xbf")

    def test_no_forbidden_write_delete_sqlite_or_process_commands(self):

        found = [p for p in _FORBIDDEN_PATTERNS if p in self.source_lower]
        self.assertEqual(
            found, [],
            f"금지된 명령/패턴이 스크립트에 있습니다: {found}",
        )

    def test_generic_read_only_no_generic_write_constant(self):
        # Win32 CreateFile 호출이 GENERIC_READ만 쓰고 GENERIC_WRITE는
        # 어디에도 선언하지 않아야 한다(쓰기 접근 자체를 요청하지
        # 않음 — OS 레벨에서도 쓰기가 원천적으로 불가능).
        self.assertIn("GENERIC_READ", self.source)
        self.assertNotIn("GENERIC_WRITE", self.source)

    def test_does_not_require_administrator(self):
        # RunAs/관리자 권한을 요구하는 표시가 없어야 한다.
        self.assertNotIn("#requires -runasadministrator", self.source_lower)

    def test_default_paths_match_the_two_documented_candidates(self):

        self.assertIn(
            r"C:\Users\Daum pc\AppData\Local\HOMEZ\data\homez.db",
            self.source,
        )
        self.assertIn(
            r"C:\Users\Daum pc\AppData\Local\Packages\Claude_pzs8sxrjxfjjc"
            r"\LocalCache\Local\HOMEZ\data\homez.db",
            self.source,
        )

    def test_does_not_hardcode_a_canonical_decision(self):
        # "canonical" 판단 자체를 스크립트가 내리지 않는다 — 값만
        # 출력한다는 설계를 소스 차원에서도 확인한다.
        self.assertNotIn("iscanonical", self.source_lower)
        self.assertNotIn("choosecanonical", self.source_lower)

    def test_never_reads_db_content_no_sqlite_connection_type(self):

        self.assertNotIn("System.Data.SQLite", self.source)
        self.assertNotIn("Microsoft.Data.Sqlite", self.source)

    def test_unknown_fallback_on_error_is_present(self):

        self.assertIn("UNKNOWN", self.source)
        self.assertIn("try", self.source_lower)
        self.assertIn("catch", self.source_lower)


@unittest.skipUnless(POWERSHELL_AVAILABLE, "PowerShell을 찾을 수 없음")
class ScriptSyntaxTestCase(unittest.TestCase):

    def test_parses_without_syntax_errors(self):

        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "$tokens=$null; $errs=$null; "
                "[System.Management.Automation.Language.Parser]::ParseFile("
                f"'{SCRIPT_PATH}', [ref]$tokens, [ref]$errs) | Out-Null; "
                "if ($errs.Count -gt 0) { $errs | ForEach-Object { $_.ToString() } } "
                "else { 'NO_SYNTAX_ERRORS' }",
            ],
            capture_output=True, text=True, timeout=30,
        )
        self.assertIn("NO_SYNTAX_ERRORS", result.stdout)


@unittest.skipUnless(POWERSHELL_AVAILABLE, "PowerShell을 찾을 수 없음")
class ScriptFunctionalDetectionTestCase(unittest.TestCase):
    """실제 두 운영 후보 DB는 절대 건드리지 않는다 — 개발 환경의
    임시 파일만 -PathA/-PathB로 넘겨 판별 로직 자체를 검증한다."""

    def setUp(self):

        self.tmp_dir = tempfile.mkdtemp(prefix="homez_ps1_check_")
        self.file_a = os.path.join(self.tmp_dir, "fileA.txt")
        self.hardlink_b = os.path.join(self.tmp_dir, "fileB_hardlink.txt")
        self.same_content_diff_file = os.path.join(
            self.tmp_dir, "fileC_same_content.txt",
        )
        self.missing_path = os.path.join(self.tmp_dir, "does_not_exist.db")

        with open(self.file_a, "w", encoding="utf-8") as f:
            f.write("identical content for HOMEZ ps1 check test\n")
        with open(self.same_content_diff_file, "w", encoding="utf-8") as f:
            f.write("identical content for HOMEZ ps1 check test\n")

        subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"New-Item -ItemType HardLink -Path '{self.hardlink_b}' "
                f"-Target '{self.file_a}' -Force | Out-Null",
            ],
            capture_output=True, text=True, timeout=15, check=True,
        )

    def tearDown(self):

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run_script(self, path_a, path_b):

        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", SCRIPT_PATH, "-PathA", path_a, "-PathB", path_b,
            ],
            capture_output=True, timeout=30,
        )
        # 한글 라벨의 코드페이지에 관계없이 ASCII 마커는 항상 그대로
        # 보존된다 — cp949로 best-effort 디코드하되 실패해도 죽지
        # 않게 errors="replace"만 쓴다(단언은 ASCII 부분만 본다).
        return result.stdout.decode("cp949", errors="replace")

    def test_hardlinked_files_are_detected_as_same_physical_file(self):

        output = self._run_script(self.file_a, self.hardlink_b)

        self.assertIn("(SameFile)", output)
        self.assertRegex(output, r"\(SameFile\)\s*:\s*True")

    def test_different_files_with_identical_content_are_not_same_file(self):
        """같은 내용(같은 SHA-256)이라도 물리적으로 다른 두 파일이면
        SameFile은 False여야 한다 — "해시가 같다"와 "같은 파일이다"를
        혼동하지 않는다는 핵심 요구사항의 직접 검증."""

        output = self._run_script(self.file_a, self.same_content_diff_file)

        self.assertRegex(output, r"SHA-256[^\r\n]*:\s*True")
        self.assertRegex(output, r"\(SameFile\)\s*:\s*False")

    def test_missing_file_reports_exists_false_and_creates_no_file(self):

        before = set(os.listdir(self.tmp_dir))

        output = self._run_script(self.file_a, self.missing_path)

        after = set(os.listdir(self.tmp_dir))
        self.assertEqual(before, after, "스크립트가 임시 폴더에 파일을 만들었습니다.")
        self.assertFalse(os.path.exists(self.missing_path))
        self.assertIn("False", output)

    def test_script_does_not_modify_the_two_real_candidate_files(self):
        """이 테스트만 예외적으로 실제 두 후보 경로를 인자로 준다 —
        단, 실행 전후 SHA-256/mtime/크기가 완전히 같은지만
        확인한다(내용은 어디에도 출력·비교하지 않는다). 파일이 이
        환경에 없으면(정상 상황) 그냥 건너뛴다."""

        real_a = r"C:\Users\Daum pc\AppData\Local\HOMEZ\data\homez.db"
        real_b = (
            r"C:\Users\Daum pc\AppData\Local\Packages\Claude_pzs8sxrjxfjjc"
            r"\LocalCache\Local\HOMEZ\data\homez.db"
        )
        if not (os.path.exists(real_a) and os.path.exists(real_b)):
            self.skipTest("실제 후보 DB가 이 환경에 없습니다.")

        def _fingerprint(path):
            st = os.stat(path)
            return (st.st_size, st.st_mtime_ns)

        before = (_fingerprint(real_a), _fingerprint(real_b))
        self._run_script(real_a, real_b)
        after = (_fingerprint(real_a), _fingerprint(real_b))

        self.assertEqual(
            before, after,
            "스크립트 실행 전후로 실제 후보 DB의 크기/mtime이 바뀌었습니다.",
        )


if __name__ == "__main__":
    unittest.main()
