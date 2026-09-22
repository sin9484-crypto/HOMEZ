"""
=========================================================
Homez OS

File : tests/test_regression_guard_selftest.py

격리 회귀 가드(tests/support/regression_guard/sitecustomize.py) 자체 시험.

**합성 보호 파일과 가짜 대상 경로만 쓴다.** 실제 저장소·설치본 homez.db, 실제 백업, 실제
Credential Manager는 열지도 참조하지도 않는다(가드의 기본 보호 목록은 끄고
HOMEZ_GUARD_NO_DEFAULTS=1, 이 시험이 만든 임시 디렉터리만 보호 대상으로 준다).

각 시나리오는 가드를 PYTHONPATH로 물려받은 **자식 파이썬 프로세스**에서 실행한다 — 그래서
"자식 프로세스에도 보호가 적용되는가"를 실제로 시험하고, 이 시험 자체가 바깥 회귀 가드 아래에서
실행돼도(같은 가드 디렉터리를 PYTHONPATH로 넘기므로) 동작한다.

네트워크 시험은 RFC 5737 문서용 주소(192.0.2.1, 어떤 호스트에도 할당되지 않음)와 예약 도메인
(.invalid)만 대상으로 하며, 가드가 연결 이전에 막아야 한다. 루프백은 허용을 확인한다.
=========================================================
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_DIR = REPO_ROOT / "tests" / "support" / "regression_guard"

PRELUDE = r'''
import json, os, shutil, socket, sqlite3, subprocess, sys
R = {}

def t(name, fn):
    try:
        fn()
        R[name] = "ALLOWED"
    except PermissionError:
        R[name] = "BLOCKED"
    except sqlite3.Error as e:
        R[name] = "BLOCKED" if "not authorized" in str(e) else "ERR:" + str(e)[:60]
    except Exception as e:
        R[name] = "ERR:" + type(e).__name__ + ":" + str(e)[:60]

def rd(path):
    with open(path, "rb") as f:
        f.read(1)

def done():
    print("RESULT" + json.dumps(R))
'''


@unittest.skipUnless(sys.platform == "win32", "Windows 경로·정션·8.3 별칭 시험")
class RegressionGuardSelfTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        cls.root = Path(tempfile.mkdtemp(prefix="homez_guard_selftest_"))
        cls.protected = cls.root / "protected long name dir"   # 공백·긴 이름 → 8.3 별칭이 생긴다
        cls.protected.mkdir()
        cls.db = cls.protected / "homez.db"
        cls.db.write_bytes(b"SYNTHETIC-PROTECTED-DB")
        (cls.protected / "sub").mkdir()
        cls.backup = cls.protected / "sub" / "backup.db"
        cls.backup.write_bytes(b"SYNTHETIC-BACKUP")
        cls.control = cls.root / "control.txt"
        cls.control.write_bytes(b"not protected")
        cls.other = cls.root / "other"
        cls.other.mkdir()

        # 별칭: 하드링크·정션·(가능하면) 심볼릭 링크 — 모두 보호 파일/디렉터리를 가리킨다.
        cls.hardlink = cls.other / "innocent_name.dat"
        os.link(cls.db, cls.hardlink)
        cls.junction = cls.other / "junction_to_protected"
        try:
            import _winapi
            _winapi.CreateJunction(str(cls.protected), str(cls.junction))
        except Exception:  # noqa: BLE001
            cls.junction = None
        cls.symlink = cls.other / "symlink_to_db"
        try:
            os.symlink(cls.db, cls.symlink)
        except (OSError, NotImplementedError):
            cls.symlink = None

        cls.short_db = None
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            n = ctypes.windll.kernel32.GetShortPathNameW(str(cls.protected), buf, 1024)
            if n and buf.value.lower() != str(cls.protected).lower():
                cls.short_db = str(Path(buf.value) / "homez.db")
        except Exception:  # noqa: BLE001
            pass

        cls.snapshot = cls._state()

    @classmethod
    def tearDownClass(cls):

        for link in (cls.junction, cls.symlink):
            if link is not None:
                try:
                    os.rmdir(link) if link == cls.junction else os.remove(link)
                except OSError:
                    pass
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def _state(cls):

        st = cls.db.stat()
        return (cls.db.read_bytes(), st.st_size, st.st_mtime_ns, cls.backup.read_bytes(),
                sorted(p.name for p in cls.protected.iterdir()))

    # ------------------------------------------------------------------
    def run_child(self, body, *, mode="block", log_name="guard.log", extra_env=None, python_args=()):

        log = self.root / log_name
        if log.exists():
            log.unlink()
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(GUARD_DIR),
            "HOMEZ_GUARD_PROTECTED_PATHS": str(self.protected),
            "HOMEZ_GUARD_NO_DEFAULTS": "1",
            "HOMEZ_GUARD_LOG": str(log),
            "HOMEZ_GUARD_MODE": mode,
        })
        env.pop("HOMEZ_GUARD_NO_INSTALL", None)
        env.update(extra_env or {})
        code = PRELUDE + f"\nP = {str(self.db)!r}\nBK = {str(self.backup)!r}\nPD = {str(self.protected)!r}\n" \
                         f"CTL = {str(self.control)!r}\nOTHER = {str(self.other)!r}\n" + body + "\ndone()\n"
        proc = subprocess.run(
            [sys.executable, *python_args, "-c", code], env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=120, stdin=subprocess.DEVNULL,
        )
        result = {}
        for line in proc.stdout.splitlines():
            if line.startswith("RESULT"):
                result = json.loads(line[len("RESULT"):])
        log_text = log.read_text(encoding="utf-8") if log.exists() else ""
        self.assertTrue(result, f"자식이 결과를 내지 못함: rc={proc.returncode} stderr={proc.stderr[-400:]}")
        return result, log_text

    def assertProtectedStateUnchanged(self):

        self.assertEqual(self._state(), self.snapshot, "합성 보호 파일의 내용·크기·mtime·목록이 바뀜")

    # ------------------------------------------------------------------
    def test_direct_access_and_path_aliases_are_blocked(self):
        """대소문자·슬래시·확장 접두·8.3 짧은 이름·정션·심볼릭 링크·하드링크 별칭으로도 우회되지 않는다."""

        upper = str(self.db).upper()
        slash = str(self.db).replace("\\", "/")
        extended = "\\\\?\\" + str(self.db)
        body = f'''
t("read", lambda: rd(P))
t("upper", lambda: rd({upper!r}))
t("forward_slash", lambda: rd({slash!r}))
t("extended_prefix", lambda: rd({extended!r}))
t("hardlink_alias", lambda: rd({str(self.hardlink)!r}))
t("write_wb", lambda: open(P, "wb"))
t("write_ab", lambda: open(P, "ab"))
t("write_rplus", lambda: open(P, "r+b"))
t("dir_file_via_backup", lambda: rd(BK))
t("sqlite_plain", lambda: sqlite3.connect(P))
t("sqlite_uri_ro", lambda: sqlite3.connect("file:" + P.replace("\\\\", "/") + "?mode=ro", uri=True))
t("control_read", lambda: rd(CTL))
t("stat_metadata_only", lambda: os.stat(P))
t("exists_metadata_only", lambda: os.path.exists(P))
'''
        if self.short_db:
            body += f't("short_8dot3", lambda: rd({self.short_db!r}))\n'
        if self.junction is not None:
            body += f't("junction_alias", lambda: rd({str(self.junction / "homez.db")!r}))\n'
        if self.symlink is not None:
            body += f't("symlink_alias", lambda: rd({str(self.symlink)!r}))\n'
        result, log = self.run_child(body)

        blocked = ["read", "upper", "forward_slash", "extended_prefix", "hardlink_alias", "write_wb", "write_ab",
                   "write_rplus", "dir_file_via_backup", "sqlite_plain", "sqlite_uri_ro"]
        blocked += [k for k in ("short_8dot3", "junction_alias", "symlink_alias") if k in result]
        for name in blocked:
            self.assertEqual(result[name], "BLOCKED", f"{name}: {result[name]}")
        for name in ("control_read", "stat_metadata_only", "exists_metadata_only"):
            self.assertEqual(result[name], "ALLOWED", name)
        self.assertGreaterEqual(log.count("ATTEMPT_BLOCKED"), len(blocked))
        self.assertNotIn("ACCESS_ALLOWED", log)
        self.assertProtectedStateUnchanged()

    def test_file_operations_are_blocked(self):

        body = f'''
t("rename_from", lambda: os.rename(P, OTHER + "\\\\moved.db"))
t("remove", lambda: os.remove(P))
t("mkdir_inside", lambda: os.mkdir(PD + "\\\\newdir"))
t("rmtree_sub", lambda: shutil.rmtree(PD + "\\\\sub"))
t("copy_from", lambda: shutil.copyfile(P, OTHER + "\\\\copy.db"))
t("copy_into", lambda: shutil.copyfile(CTL, PD + "\\\\injected.db"))
t("move_from", lambda: shutil.move(BK, OTHER + "\\\\bk.db"))
t("link_to_protected", lambda: os.link(P, OTHER + "\\\\new_alias.db"))
'''
        result, log = self.run_child(body)
        for name in ("rename_from", "remove", "mkdir_inside", "rmtree_sub", "copy_from", "copy_into", "move_from",
                     "link_to_protected"):
            self.assertEqual(result[name], "BLOCKED", f"{name}: {result[name]}")
        self.assertProtectedStateUnchanged()
        self.assertFalse((self.other / "moved.db").exists())
        self.assertFalse((self.other / "copy.db").exists())
        self.assertFalse((self.protected / "injected.db").exists())
        self.assertFalse((self.protected / "newdir").exists())

    def test_child_processes_are_guarded_or_refused(self):

        ps = shutil.which("powershell")
        body = f'''
def grandchild_inherits():
    r = subprocess.run([sys.executable, "-c", "open(%r,'rb')" % P], capture_output=True, text=True)
    if "PermissionError" not in r.stderr:
        raise RuntimeError("grandchild not guarded: rc=%s err=%s" % (r.returncode, r.stderr[-200:]))
t("python_child_inherits_guard", grandchild_inherits)
t("python_child_with_clean_env_refused", lambda: subprocess.run([sys.executable, "-c", "pass"], env={{"SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}}))
t("python_child_isolated_flag_refused", lambda: subprocess.run([sys.executable, "-I", "-c", "pass"]))
t("python_child_no_site_flag_refused", lambda: subprocess.run([sys.executable, "-S", "-c", "pass"]))
t("shell_command_with_protected_path", lambda: os.system('type "%s"' % P))
t("credential_tool_refused", lambda: subprocess.run(["cmdkey", "/list"]))
t("benign_shell_child_allowed", lambda: subprocess.run(["cmd", "/c", "echo", "ok"], capture_output=True))
'''
        if ps:
            body += f't("powershell_with_protected_path", lambda: subprocess.run([{ps!r}, "-NoProfile", "-Command", "Get-Item \'%s\'" % P]))\n'
        result, log = self.run_child(body)
        for name in ("python_child_inherits_guard", "python_child_with_clean_env_refused",
                     "python_child_isolated_flag_refused", "python_child_no_site_flag_refused",
                     "shell_command_with_protected_path", "credential_tool_refused"):
            self.assertEqual(result[name], "ALLOWED" if name == "python_child_inherits_guard" else "BLOCKED", f"{name}: {result[name]}")
        if ps:
            self.assertEqual(result["powershell_with_protected_path"], "BLOCKED")
        self.assertEqual(result["benign_shell_child_allowed"], "ALLOWED")
        self.assertIn("CHILD_UNGUARDED", log, "가드가 볼 수 없는 비파이썬 자식 실행이 기록돼야 한다")
        self.assertProtectedStateUnchanged()

    def test_external_network_and_dns_are_blocked_loopback_allowed(self):

        body = '''
def ext_connect():
    s = socket.socket(); s.settimeout(0.5)
    try:
        s.connect(("192.0.2.1", 9))
    finally:
        s.close()
def ext_connect_ex():
    s = socket.socket(); s.settimeout(0.5)
    try:
        s.connect_ex(("192.0.2.1", 9))
    finally:
        s.close()
def ext_sendto():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.sendto(b"x", ("192.0.2.1", 9))
    finally:
        s.close()
def loopback():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    cli = socket.socket(); cli.settimeout(2)
    cli.connect(("127.0.0.1", srv.getsockname()[1]))
    cli.close(); srv.close()
t("external_connect", ext_connect)
t("external_connect_ex", ext_connect_ex)
t("external_sendto", ext_sendto)
t("dns_getaddrinfo", lambda: socket.getaddrinfo("guard-selftest.invalid", 80))
t("dns_gethostbyname", lambda: socket.gethostbyname("guard-selftest.invalid"))
t("loopback_connect", loopback)
t("localhost_name_lookup", lambda: socket.getaddrinfo("localhost", 80))
'''
        result, log = self.run_child(body)
        for name in ("external_connect", "external_connect_ex", "external_sendto", "dns_getaddrinfo", "dns_gethostbyname"):
            self.assertEqual(result[name], "BLOCKED", f"{name}: {result[name]}")
        for name in ("loopback_connect", "localhost_name_lookup"):
            self.assertEqual(result[name], "ALLOWED", f"{name}: {result[name]}")

    def test_credential_manager_symbols_are_blocked_before_any_call(self):

        body = '''
import ctypes
lib = ctypes.WinDLL("advapi32", use_last_error=True)
t("CredReadW_symbol", lambda: lib.CredReadW)
t("CredWriteW_symbol", lambda: lib.CredWriteW)
t("CredDeleteW_symbol", lambda: lib.CredDeleteW)
t("CredEnumerateW_symbol", lambda: lib.CredEnumerateW)
t("unrelated_symbol_allowed", lambda: lib.RegCloseKey)
'''
        result, _log = self.run_child(body)
        for name in ("CredReadW_symbol", "CredWriteW_symbol", "CredDeleteW_symbol", "CredEnumerateW_symbol"):
            self.assertEqual(result[name], "BLOCKED", f"{name}: {result[name]}")
        self.assertEqual(result["unrelated_symbol_allowed"], "ALLOWED")

    def test_attempts_accesses_and_content_changes_are_reported_separately(self):
        """차단 시도 / 접근 성공 / 내용 변경을 구분한다 — 크기·mtime·내용 불변만으로는 접근 부재를 증명하지 못한다."""

        original = self.db.read_bytes()
        original_stat = self.db.stat()
        try:
            # (1) 차단 모드: 시도만 기록되고 접근은 없다.
            result, log = self.run_child('t("w", lambda: open(P, "ab"))\nt("r", lambda: rd(P))', log_name="block.log")
            self.assertEqual((result["w"], result["r"]), ("BLOCKED", "BLOCKED"))
            self.assertIn("ATTEMPT_BLOCKED", log)
            self.assertNotIn("ACCESS_ALLOWED", log)
            self.assertProtectedStateUnchanged()

            # (2) report 모드: 접근이 성공한다. 같은 값을 다시 쓰고 mtime까지 되돌리면 상태 비교로는 변화가 없다.
            body = f'''
def rewrite_same_bytes():
    with open(P, "rb") as f:
        data = f.read()
    with open(P, "wb") as f:
        f.write(data)
    os.utime(P, ns=({original_stat.st_atime_ns}, {original_stat.st_mtime_ns}))
t("rewrite_same_bytes", rewrite_same_bytes)
'''
            result, log = self.run_child(body, mode="report", log_name="report.log")
            self.assertEqual(result["rewrite_same_bytes"], "ALLOWED")
            self.assertIn("ACCESS_ALLOWED", log)
            self.assertIn("access=write", log)
            self.assertNotIn("ATTEMPT_BLOCKED", log)
            after = self.db.stat()
            self.assertEqual(self.db.read_bytes(), original)            # 내용 동일
            self.assertEqual(after.st_size, original_stat.st_size)      # 크기 동일
            self.assertEqual(after.st_mtime_ns, original_stat.st_mtime_ns)  # mtime 동일
            # → 상태 비교만으로는 "쓰기 접근이 있었다"는 사실을 알 수 없다. 가드 로그만이 그것을 보여준다.
        finally:
            self.db.write_bytes(original)
            os.utime(self.db, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    def test_repository_does_not_use_sql_attach_or_vacuum_into(self):
        """가드는 SQL ATTACH·VACUUM INTO를 차단하지 못한다(연결마다 authorizer를 거는 방식은 멀티스레드 SQLite에서
        교착이 나 제거했다). 그래서 저장소가 그 기능을 쓰지 않는다는 사실을 정적으로 지킨다 — 생기면 이 시험이 실패한다."""

        pattern = re.compile(r"\battach\s+database\b|\bvacuum\s+into\b", re.IGNORECASE)
        skip = {"test_regression_guard_selftest.py", "sitecustomize.py"}
        offenders = []
        for base, suffixes in ((REPO_ROOT / "app", (".py", ".sql")), (REPO_ROOT / "tests", (".py",)),
                               (REPO_ROOT / "migrations", (".sql",)), (REPO_ROOT / "scripts", (".py", ".ps1", ".sql"))):
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if path.is_file() and path.suffix in suffixes and path.name not in skip:
                    if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                        offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [], "ATTACH/VACUUM INTO 사용이 생겼다 — 가드가 막지 못하는 경로이므로 가드 또는 이 시험을 재검토")

    def test_default_protected_paths_are_derived_from_environment_without_touching_real_files(self):

        spec = importlib.util.spec_from_file_location("homez_guard_under_test", GUARD_DIR / "sitecustomize.py")
        module = importlib.util.module_from_spec(spec)
        previous = os.environ.get("HOMEZ_GUARD_NO_INSTALL")
        os.environ["HOMEZ_GUARD_NO_INSTALL"] = "1"      # import만 — 감사 훅을 설치하지 않는다
        try:
            spec.loader.exec_module(module)
        finally:
            if previous is None:
                os.environ.pop("HOMEZ_GUARD_NO_INSTALL", None)
            else:
                os.environ["HOMEZ_GUARD_NO_INSTALL"] = previous

        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "Local"
            alias = local / "Packages" / "Vendor.App_abc" / "LocalCache" / "Local" / "HOMEZ"
            alias.mkdir(parents=True)
            fake_env = {"USERPROFILE": r"C:\Users\Someone", "LOCALAPPDATA": str(local)}
            derived = module._default_protected(fake_env)
        as_text = {(os.path.normcase(p), d) for p, d in derived}
        expected = {
            (os.path.normcase(r"C:\Users\Someone\Homez-OS\homez.db"), False),
            (os.path.normcase(r"C:\Users\Someone\Homez-OS\storage\backups"), True),
            (os.path.normcase(r"C:\Users\Someone\Homez-Backups"), True),
            (os.path.normcase(str(local / "HOMEZ")), True),
            (os.path.normcase(str(alias)), True),
        }
        self.assertEqual(as_text, expected)
        # 사이드카(-wal/-shm/-journal)도 파일 보호에 포함된다.
        module._P_FILES.clear()
        module._add_protected(r"C:\Users\Someone\Homez-OS\homez.db", is_dir=False)
        self.assertEqual(
            sorted(module._P_FILES),
            sorted(os.path.normcase(r"C:\Users\Someone\Homez-OS\homez.db") + s for s in ("", "-wal", "-shm", "-journal")),
        )


if __name__ == "__main__":
    unittest.main()
