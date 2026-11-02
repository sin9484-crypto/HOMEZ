"""
=========================================================
Homez OS

File : tests/test_start_homez_live.py

운영 앱 실행기(`tools/start_homez_live.py`) 규칙을 고정한다. 임시 폴더의 합성 DB만 쓰고 실제 앱을 켜지 않는다(가짜 Popen).
=========================================================
"""

import importlib.util
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("homez_start_live_under_test", ROOT / "tools" / "start_homez_live.py")
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)

NOW = datetime(2026, 11, 2, 12, 30, 0)


class StartHomezLiveTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="homez 실행기 ")
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "repo"
        (self.repo / "venv" / "Scripts").mkdir(parents=True)
        self.pythonw = self.repo / "venv" / "Scripts" / "pythonw.exe"
        self.pythonw.write_bytes(b"synthetic")
        self.db = self.repo / "homez.db"
        conn = sqlite3.connect(self.db)
        conn.execute("create table t (v text)")
        conn.execute("insert into t values ('원본')")
        conn.commit()
        conn.close()
        self.backups = Path(tmp.name) / "backups"
        self.popen = mock.Mock()
        self.notify = mock.Mock()

    def run_main(self, argv=(), now=NOW, environ=None, popen=None):
        return live.main(list(argv), repo=self.repo, backup_root=self.backups, now=now, popen=popen or self.popen,
                         notify=self.notify, environ=environ if environ is not None else {"PATH": "x"})

    def backup_file(self, day="20261102"):
        return self.backups / f"auto_start_{day}" / "homez_pre_start.db"

    def test_it_backs_up_first_then_starts_the_operating_app_from_the_repo(self):
        order = []
        self.popen.side_effect = lambda *a, **k: order.append(self.backup_file().exists())
        self.assertEqual(self.run_main(), live.EXIT_OK)
        self.assertEqual(order, [True])                                     # 켜는 시점에 복원점이 이미 있다
        args, kwargs = self.popen.call_args
        self.assertEqual(args[0], [str(self.pythonw), "-m", "app.desktop.main"])
        self.assertEqual(kwargs["cwd"], str(self.repo))
        self.assertTrue(kwargs["close_fds"])
        self.assertTrue(kwargs["creationflags"] & subprocess.DETACHED_PROCESS)
        self.notify.assert_not_called()

    def test_the_backup_is_a_verified_copy_and_the_source_is_unchanged(self):
        before = self.db.read_bytes()
        self.run_main()
        self.assertEqual(self.db.read_bytes(), before)
        conn = sqlite3.connect(self.backup_file())
        self.assertEqual(conn.execute("select v from t").fetchall(), [("원본",)])
        conn.close()
        self.assertEqual([p.name for p in self.backup_file().parent.iterdir()], ["homez_pre_start.db"])     # 작업 파일이 남지 않는다

    def test_the_source_is_opened_read_only(self):
        real_connect = sqlite3.connect
        seen = []

        def connect(path, **kw):
            seen.append((str(path), kw))
            return real_connect(path, **kw)

        with mock.patch.object(live.sqlite3, "connect", side_effect=connect):
            self.run_main()
        source_path, source_kw = seen[0]
        self.assertTrue(source_path.endswith("homez.db?mode=ro"), source_path)
        self.assertTrue(source_kw["uri"])

    def test_one_backup_a_day_is_kept_and_never_overwritten_but_the_app_still_starts(self):
        self.run_main()
        self.backup_file().write_bytes(b"first of the day")
        self.assertEqual(self.run_main(), live.EXIT_OK)
        self.assertEqual(self.backup_file().read_bytes(), b"first of the day")
        self.assertEqual(self.popen.call_count, 2)

    def test_a_new_day_gets_its_own_backup_and_old_ones_stay(self):
        self.run_main()
        self.run_main(now=datetime(2026, 11, 3, 0, 0, 1))
        self.assertTrue(self.backup_file().exists())
        self.assertTrue(self.backup_file("20261103").exists())

    def test_guard_and_path_variables_do_not_reach_the_app(self):
        self.run_main(environ={"PATH": "x", "PYTHONPATH": "g", "pythonhome": "h", "HOMEZ_GUARD_LOG": "l", "HOMEZ_GUARD_MODE": "m", "HOMEZ_DESKTOP_DEBUG": "0"})
        self.assertEqual(self.popen.call_args.kwargs["env"], {"PATH": "x", "HOMEZ_DESKTOP_DEBUG": "0"})

    def test_a_failed_backup_means_the_app_is_not_started_and_the_user_is_told(self):
        self.db.write_bytes(b"this is not a database at all, " * 40)
        self.assertEqual(self.run_main(), live.EXIT_BACKUP_FAILED)
        self.popen.assert_not_called()
        self.assertIn("켜지 않았습니다", self.notify.call_args.args[0])
        self.assertFalse(self.backup_file().exists())
        self.assertEqual(list(self.backup_file().parent.iterdir()), [])     # 망가진 작업 파일을 복원점처럼 남기지 않는다

    def test_a_backup_that_fails_the_integrity_check_is_not_kept(self):
        def connect(path, **kw):
            conn = mock.Mock()
            if str(path).endswith(".partial"):
                Path(path).write_bytes(b"copied")
                conn.execute.return_value.fetchone.return_value = ("*** corrupted",)
            return conn

        with mock.patch.object(live.sqlite3, "connect", side_effect=connect):
            self.assertEqual(self.run_main(), live.EXIT_BACKUP_FAILED)
        self.popen.assert_not_called()
        self.assertFalse(self.backup_file().exists())
        self.assertEqual(list(self.backup_file().parent.iterdir()), [])

    def test_missing_files_stop_before_anything_happens(self):
        for victim in (self.pythonw, self.db):
            original = victim.read_bytes()
            victim.unlink()
            self.assertEqual(self.run_main(), live.EXIT_MISSING_FILE)
            self.assertIn(str(victim), self.notify.call_args.args[0])
            victim.write_bytes(original)
        self.popen.assert_not_called()
        self.assertFalse(self.backups.exists())

    def test_check_touches_nothing(self):
        with mock.patch("builtins.print") as printed:
            self.assertEqual(self.run_main(["--check"]), live.EXIT_OK)
        self.popen.assert_not_called()
        self.assertFalse(self.backups.exists())
        self.assertIn("app.desktop.main", printed.call_args.args[0])

    def test_a_failed_start_is_reported(self):
        self.assertEqual(self.run_main(popen=mock.Mock(side_effect=OSError("x"))), live.EXIT_START_FAILED)
        self.assertIn("켜지 못했습니다", self.notify.call_args.args[0])

    def test_it_targets_the_same_database_as_the_desktop_shell(self):
        from app.desktop.paths import get_homez_db_path

        self.assertEqual(Path(get_homez_db_path(confirm=True)).resolve(), (live.REPO_ROOT / live.DB_NAME).resolve())
        self.assertEqual(live.BACKUP_ROOT.name, "Homez-Backups")


if __name__ == "__main__":
    unittest.main()
