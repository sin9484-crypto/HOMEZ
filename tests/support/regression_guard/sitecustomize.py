"""
=========================================================
Homez OS

File : tests/support/regression_guard/sitecustomize.py

격리 회귀 실행용 자원 보호 가드.

사용법(저장소 밖 로그 파일 권장):

    set HOMEZ_GUARD_LOG=C:\\...\\guard.log
    set PYTHONPATH=<저장소>\\tests\\support\\regression_guard;<저장소>
    python -m unittest discover -s tests

`PYTHONPATH`에 이 디렉터리가 있으면 파이썬이 시작할 때 이 파일을 `sitecustomize`로
자동 import하고, 이 프로세스와 (환경을 상속하는) 파이썬 자식 프로세스에서 다음을 감사 훅
(`sys.addaudithook`)으로 **차단**하고 시도를 로그에 남긴다.

  1. 실제 업무 자원의 파일 접근 — 저장소 `homez.db`(+ `-wal`·`-shm`·`-journal`),
     `storage\\backups`, 설치본 `%LOCALAPPDATA%\\HOMEZ`(MSIX 별칭 `Packages\\*\\LocalCache\\Local\\HOMEZ`
     포함), `%USERPROFILE%\\Homez-Backups`. open/sqlite3.connect/이름변경/삭제/복사/링크 생성/
     (SQL `ATTACH DATABASE`는 차단하지 **않는다** — 아래 한계 참조.)
  2. 실제 Windows Credential Manager — `advapi32`의 Cred* 심볼 조회(호출 이전), `cmdkey` 등 자식 실행.
  3. 승인되지 않은 외부 네트워크 — 루프백이 아닌 connect/sendto, 루프백이 아닌 이름 조회(DNS).
  4. 자식 프로세스 — 보호 경로를 인자에 담은 실행, 가드를 잃는 파이썬 자식(-I/-S/-E 또는
     PYTHONPATH를 지운 env) 실행. 그 외 자식(PowerShell·node 등 비파이썬)은 로그에만 남긴다.

경로 비교는 대소문자·슬래시·`\\\\?\\` 접두·8.3 짧은 이름·심볼릭 링크·정션을 `os.path.realpath`로
풀어 한 뒤 하고, 하드링크는 (st_dev, st_ino) 동일성으로 잡는다.

로그 용어(구분해서 읽는다):
  ATTEMPT_BLOCKED   차단된 **시도** — 접근은 일어나지 않았다.
  ACCESS_ALLOWED    (report 모드 전용) 허용된 **접근 성공** — 차단 모드에서는 나오지 않는다.
  CHILD_UNGUARDED   가드가 볼 수 없는 자식(비파이썬) 실행 기록.
  이 가드는 **내용 변경**을 관찰하지 않는다. 파일 크기·수정시각·해시가 같다는 것은 무접촉·중간 쓰기
  부재의 증거가 아니다(같은 값으로 되돌리는 쓰기, 읽기 전용 접근은 구분되지 않는다).

보장하지 못하는 것(한계):
  - 감사 훅을 우회하는 경로: ctypes로 직접 호출하는 Win32 파일 API(CreateFileW 등), C 확장이
    직접 여는 파일, 메모리 매핑된 기존 핸들.
  - 비파이썬 자식 프로세스(PowerShell·node·cmd)의 내부 동작 — 인자에 보호 경로 문자열이 있을 때만
    막고, 스크립트 안에서 계산한 경로는 보지 못한다.
  - `-I`/`-S`/`-E` 없이 sitecustomize 자체를 우회하는 방법(예: 다른 PYTHONPATH를 쓰는 자식은 위에서
    차단하지만, 이미 시작된 별도 프로세스에는 적용되지 않는다).
  - 하드링크 별칭 검사는 가드 시작 시점에 존재하던 보호 파일 기준이다(이후 만들어진 파일 제외).
  - **SQL `ATTACH DATABASE`·`VACUUM INTO '<보호 경로>'`는 차단하지 못한다.** 연결마다 sqlite authorizer(Python 콜백)를 거는
    방식을 시도했으나 멀티스레드 SQLite 사용에서 GIL/뮤텍스 교착으로 회귀가 멈췄다(test_account_registration 동시성 테스트) —
    제거했다. 이 저장소는 ATTACH를 쓰지 않으며, tests/test_regression_guard_selftest.py가 정적 검색으로 그 사실을 지킨다.
  - 보호 경로 목록은 시작 시점 환경변수로 계산한다.
  - 시작 시 보호 대상의 **메타데이터(stat)** 를 읽는다(내용은 읽지 않는다).
=========================================================
"""

from __future__ import annotations

import ipaddress
import os
import re
import sys
import threading
import traceback
from urllib.parse import unquote

_LOG = os.environ.get("HOMEZ_GUARD_LOG")
_MODE = os.environ.get("HOMEZ_GUARD_MODE", "block")  # "block" | "report"
_GUARD_DIR = os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(__file__))))
_ENV_PROTECTED = "HOMEZ_GUARD_PROTECTED_PATHS"
_tl = threading.local()

_SKIP_EXT = (".py", ".pyc", ".pyd", ".dll", ".pth", ".exe", ".so")
_SIDECARS = ("", "-wal", "-shm", "-journal")
_CRED_RE = re.compile(r"^cred(?!free$)\w+$", re.I)
_CRED_TOOLS = ("cmdkey", "vaultcmd", "credwiz")

_P_FILES: set[str] = set()
_P_DIRS: list[str] = []
_P_IDS: set[tuple[int, int]] = set()
_cache: dict[str, str] = {}
_id_cache: dict[str, bool] = {}
_P_TEXT: set[str] = set()   # 자식 프로세스 인자 검사용 표기 변형(정규 경로·입력 원형·8.3 짧은 이름)


# ---------------------------------------------------------------------------
# 로그
# ---------------------------------------------------------------------------

def _log(kind: str, detail: str = "") -> None:
    if not _LOG:
        return
    prev = getattr(_tl, "busy", False)
    _tl.busy = True
    try:
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\t{kind}\t{detail}\n")
    except OSError:
        pass
    finally:
        _tl.busy = prev


def _via() -> str:
    frames = [
        f"{os.path.basename(fr.filename)}:{fr.lineno}:{fr.name}"
        for fr in traceback.extract_stack()
        if "site-packages" not in fr.filename and "unittest" not in fr.filename
        and "sitecustomize" not in fr.filename and "<frozen" not in fr.filename
    ]
    depth = int(os.environ.get("HOMEZ_GUARD_VIA_DEPTH", "3"))
    return " > ".join(frames[-depth:])


# ---------------------------------------------------------------------------
# 경로 정규화·보호 판정
# ---------------------------------------------------------------------------

def _uri_to_path(uri: str) -> str:
    rest = uri[5:].split("?")[0]
    rest = unquote(rest)
    if rest.startswith("///"):
        rest = rest[3:]
    elif rest.startswith("//"):
        rest = rest[2:]
    if len(rest) > 2 and rest[0] == "/" and rest[2] == ":":
        rest = rest[1:]
    return rest


def _canon(p) -> str:
    try:
        if p is None or isinstance(p, int):
            return ""
        if isinstance(p, (bytes, bytearray)):
            p = os.fsdecode(bytes(p))
        elif not isinstance(p, str):
            p = os.fspath(p)
            if isinstance(p, bytes):
                p = os.fsdecode(p)
    except Exception:  # noqa: BLE001
        return ""
    if p.startswith("file:"):
        p = _uri_to_path(p)
    if not p or p == ":memory:":
        return ""
    if p.lower().endswith(_SKIP_EXT):
        return ""
    hit = _cache.get(p)
    if hit is not None:
        return hit
    try:
        c = os.path.normcase(os.path.realpath(os.path.abspath(p)))
    except (OSError, ValueError):
        c = os.path.normcase(os.path.abspath(p))
    if len(_cache) < 50000:
        _cache[p] = c
    return c


def _norm_for_text(s: str) -> str:
    return s.lower().replace("/", "\\")


def _short_path(path: str) -> str:
    """존재하는 경로의 8.3 짧은 이름(없거나 실패하면 빈 문자열). 시작 시점, 훅 설치 전에만 호출한다."""

    if not path or not os.path.exists(path):
        return ""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
        return buf.value if 0 < n < 1024 else ""
    except Exception:  # noqa: BLE001
        return ""


def _add_protected(path: str, *, is_dir: bool | None = None) -> None:
    if not path:
        return
    try:
        canon = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    except (OSError, ValueError):
        canon = os.path.normcase(os.path.abspath(path))
    _P_TEXT.add(canon)
    _P_TEXT.add(os.path.normcase(os.path.abspath(path)))
    short = _short_path(canon)
    if short:
        _P_TEXT.add(os.path.normcase(short))
    treat_dir = os.path.isdir(canon) if is_dir is None else is_dir
    if treat_dir:
        if canon not in _P_DIRS:
            _P_DIRS.append(canon)
    else:
        for suffix in _SIDECARS:
            _P_FILES.add(canon + suffix)


def _default_protected(env) -> list[tuple[str, bool | None]]:
    """환경(env)에서 기본 보호 경로를 계산한다(파일 접근 없음, Packages 별칭 목록 조회만)."""

    out: list[tuple[str, bool | None]] = []
    home = env.get("USERPROFILE") or os.path.expanduser("~")
    local = env.get("LOCALAPPDATA") or (os.path.join(home, "AppData", "Local") if home else "")
    repo = env.get("HOMEZ_GUARD_REAL_REPO_ROOT") or (os.path.join(home, "Homez-OS") if home else "")
    if repo:
        out.append((os.path.join(repo, "homez.db"), False))
        out.append((os.path.join(repo, "storage", "backups"), True))
    if home:
        out.append((os.path.join(home, "Homez-Backups"), True))
    if local:
        out.append((os.path.join(local, "HOMEZ"), True))
        pkgs = os.path.join(local, "Packages")
        try:
            with os.scandir(pkgs) as it:
                for entry in it:
                    alias = os.path.join(entry.path, "LocalCache", "Local", "HOMEZ")
                    if os.path.isdir(alias):
                        out.append((alias, True))
        except OSError:
            pass
    return out


def _is_protected_canon(c: str) -> str | None:
    if not c:
        return None
    if c in _P_FILES:
        return "path"
    for d in _P_DIRS:
        if c == d or c.startswith(d + os.sep):
            return "path"
    if _P_IDS:
        known = _id_cache.get(c)
        if known is None:
            try:
                st = os.stat(c)
                known = (st.st_dev, st.st_ino) in _P_IDS
            except OSError:
                known = False
            if len(_id_cache) < 50000:
                _id_cache[c] = known
        if known:
            return "alias-identity"
    return None


def _build_protected() -> None:
    explicit = os.environ.get(_ENV_PROTECTED, "")
    for item in [x for x in explicit.split(os.pathsep) if x]:
        _add_protected(item)
    if os.environ.get("HOMEZ_GUARD_NO_DEFAULTS") != "1":
        for path, is_dir in _default_protected(os.environ):
            _add_protected(path, is_dir=is_dir)
    # 자식이 상속하도록 확장된 목록을 내보낸다(자식의 LOCALAPPDATA가 달라도 같은 실제 자원을 보호).
    exported = [f for f in sorted(_P_FILES) if not f.endswith(("-wal", "-shm", "-journal"))] + _P_DIRS
    os.environ[_ENV_PROTECTED] = os.pathsep.join(exported)
    if os.environ.get("HOMEZ_GUARD_IDENTITY") != "0":
        count = 0
        for f in list(_P_FILES):
            try:
                st = os.stat(f)
                _P_IDS.add((st.st_dev, st.st_ino))
            except OSError:
                pass
        for d in _P_DIRS:
            for root, _dirs, files in os.walk(d):
                for name in files:
                    count += 1
                    if count > 5000:
                        break
                    try:
                        st = os.stat(os.path.join(root, name))
                        _P_IDS.add((st.st_dev, st.st_ino))
                    except OSError:
                        pass
                if count > 5000:
                    break


# ---------------------------------------------------------------------------
# 판정 → 차단/허용
# ---------------------------------------------------------------------------

def _access_kind_open(args) -> str:
    try:
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        if isinstance(mode, str) and any(ch in mode for ch in "wax+"):
            return "write"
        if isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
            return "write"
        return "read"
    except Exception:  # noqa: BLE001
        return "unknown"


def _deny(kind: str, detail: str, access: str = "unknown") -> None:
    if _MODE == "report":
        _log("ACCESS_ALLOWED", f"{kind} access={access} {detail} via={_via()}")
        return
    _log("ATTEMPT_BLOCKED", f"{kind} access={access} {detail} via={_via()}")
    raise PermissionError(f"regression guard blocked ({kind}): {detail}")


def _check_path(event: str, target, access: str) -> None:
    reason = _is_protected_canon(_canon(target))
    if reason:
        _deny(event, f"{_canon(target)} ({reason})", access)


def _is_local_host(host) -> bool:
    if host is None:
        return True
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "ignore")
    if not isinstance(host, str):
        return True
    h = host.strip().strip("[]").split("%")[0].lower()
    if h in ("", "localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


_PATH_EVENTS = {
    "open": (0,),
    "sqlite3.connect": (0,),
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.mkdir": (0,),
    "os.rename": (0, 1),
    "os.truncate": (0,),
    "os.link": (0, 1),
    "os.symlink": (0, 1),
    "shutil.copyfile": (0, 1),
    "shutil.copymode": (0, 1),
    "shutil.copystat": (0, 1),
    "shutil.copytree": (0, 1),
    "shutil.move": (0, 1),
    "shutil.rmtree": (0,),
}


def _scan_text_for_protected(text: str) -> str | None:
    norm = _norm_for_text(text)
    for variant in _P_TEXT:
        if variant in norm:
            return variant
    for f in _P_FILES:
        if f in norm:
            return f
    for d in _P_DIRS:
        if d in norm:
            return d
    return None


def _split_cmdline(text: str) -> list[str]:
    """Windows 명령줄을 따옴표를 존중해 나눈다(경로에 공백이 있어도 첫 토큰이 깨지지 않게)."""

    return [t.strip('"') for t in re.findall(r'"[^"]*"|\S+', text)]


def _env_pythonpath(env) -> str:
    """Popen 이벤트의 env(매핑, 또는 'KEY=VALUE' 목록/블록)에서 PYTHONPATH 값을 꺼낸다(대소문자 무시)."""

    try:
        items = env.items() if hasattr(env, "items") else [str(e).split("=", 1) for e in env if "=" in str(e)]
        for key, value in items:
            if str(key).upper() == "PYTHONPATH":
                return str(value)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _check_child(event: str, exe, args, env, *, classify: bool = True) -> None:
    if isinstance(args, (list, tuple)):
        tokens = [str(a) for a in args]
    else:
        tokens = _split_cmdline(str(args or ""))
    text = " ".join(tokens)
    exe_s = str(exe or (tokens[0] if tokens else ""))
    hit = _scan_text_for_protected(f"{exe_s} {text}")
    if hit:
        _deny(f"{event}:arg", hit)
    if not classify:
        return
    low = f"{exe_s} {text}".lower()
    for tool in _CRED_TOOLS:
        if re.search(rf"(^|[\/\s\"']){tool}(\.exe)?([\s\"']|$)", low):
            _deny(f"{event}:credential-tool", tool)
    base = os.path.basename(exe_s.strip('"')).lower()
    if base.startswith("python") or base.startswith("pythonw"):
        for tok in tokens[1:] if exe is None else tokens:
            if tok in ("-c", "-m") or not tok.startswith("-"):
                break
            if not tok.startswith("--") and any(ch in "ISE" for ch in tok[1:]):
                _deny(f"{event}:python-child-without-guard", "flags -I/-S/-E drop sitecustomize or PYTHON* env")
        if env is not None:
            py_path = _env_pythonpath(env)
            entries = [os.path.normcase(os.path.realpath(x)) for x in py_path.split(os.pathsep) if x]
            if _GUARD_DIR not in entries:
                _deny(f"{event}:python-child-without-guard", "explicit env lacks the guard directory in PYTHONPATH")
    else:
        _log("CHILD_UNGUARDED", f"{event} exe={base}")


def _audit(event: str, args) -> None:
    if getattr(_tl, "busy", False):
        return
    _tl.busy = True
    try:
        idxs = _PATH_EVENTS.get(event)
        if idxs is not None:
            if event == "open":
                access = _access_kind_open(args)
            elif event == "sqlite3.connect":
                access = "read" if "mode=ro" in str(args[0]).lower() else "unknown"
            else:
                access = "write"
            for i in idxs:
                if i < len(args):
                    _check_path(event, args[i], access)
            return
        if event in ("socket.connect", "socket.sendto"):
            addr = args[1] if len(args) > 1 else None
            if isinstance(addr, tuple) and addr and not _is_local_host(addr[0]):
                _deny(event, f"{addr[0]}:{addr[1] if len(addr) > 1 else ''}")
            return
        if event in ("socket.getaddrinfo",):
            if not _is_local_host(args[0] if args else None):
                _deny(event, str(args[0]))
            return
        if event in ("socket.gethostbyname", "socket.gethostbyname_ex", "socket.gethostbyaddr"):
            if not _is_local_host(args[0] if args else None):
                _deny(event, str(args[0]))
            return
        if event == "ctypes.dlsym":
            name = str(args[1]) if len(args) > 1 else ""
            if _CRED_RE.match(name):
                _deny("credential-manager", f"symbol {name}")
            return
        if event == "subprocess.Popen":
            _check_child(event, args[0], args[1] if len(args) > 1 else None, args[3] if len(args) > 3 else None)
            return
        if event == "os.system":
            _check_child(event, "", args[0] if args else "", None)
            return
        if event in ("os.exec", "os.spawn"):
            base = 1 if event == "os.spawn" else 0
            env = args[base + 2] if len(args) > base + 2 else None
            _check_child(event, args[base] if len(args) > base else "", args[base + 1] if len(args) > base + 1 else None, env)
            return
        if event == "_winapi.CreateProcess":
            _check_child(event, args[0] or "", args[1] if len(args) > 1 else "", None, classify=False)
            return
    finally:
        _tl.busy = False


def _install() -> None:
    _build_protected()
    sys.addaudithook(_audit)
    _log("GUARD_ACTIVE", f"mode={_MODE} protected_files={len(_P_FILES)} protected_dirs={len(_P_DIRS)} identities={len(_P_IDS)}")


if os.environ.get("HOMEZ_GUARD_NO_INSTALL") != "1":
    _install()
