# HOMEZ V3 Desktop Shell

# Current Version

**콘솔 숨김 런처 완료(2026-07-30).** 바탕화면 아이콘이 이제
`wscript.exe`로 `scripts/start_homez_desktop.vbs`를 실행한다 — 이전에는
`.cmd`를 직접 가리켜 cmd.exe의 검은 콘솔 창이 항상(최소화 설정과
무관하게) 나타났다. VBS는 콘솔이 없는 `venv\Scripts\pythonw.exe -m
app.desktop.bootstrap`을 숨김(WindowStyle=0)으로 실행해 pywebview 창만
보이게 한다. `app.desktop.bootstrap`은 `app.desktop.main`을 import하는
단계까지 포함해 감싸는 최상위 진입점이다(그 안의 `run()`은 이미
자기 예외를 대부분 처리하지만, import 단계 자체의 실패는 `run()`
호출 전이라 보호받지 못하기 때문 — 실제로 이런 계층 분리가 필요한
근거를 발견해 추가했다, 아래 "실제 재현·검증" 참고).

`pythonw.exe`에는 콘솔이 없어 시작 실패 시 기존 `print()` 출력이
사라지므로, 다음을 추가했다:
- `%LOCALAPPDATA%\HOMEZ\logs\homez-desktop.log`에 오류 코드가 포함된
  수명주기 로그를 남긴다(기존 `storage\logs\desktop-launcher.log`
  로거는 대체하지 않고 그대로 유지).
- 네이티브 MessageBox에 오류 코드 + 로그 경로만 표시(원본 예외/
  traceback 없음): `E1000`=bootstrap/import 단계 실패,
  `E1001`=포트 충돌, `E1002`=서버 준비 시간 초과, `E1003`=처리되지
  않은 예외.

**한계(정직하게 명시)**: 이 세션의 자동화 도구로 반복 검증하는
과정에서, `ctypes.windll.user32.MessageBoxW` 호출이 이 특정 자동화
세션에서는 화면에 실제로 표시되지 않고 즉시 반환되는 현상을
관찰했다(같은 세션에서 pywebview 네이티브 창 자체는 정상적으로
보였던 것과 대조적). 실제 사용자의 인터랙티브 데스크톱 세션에서
동일하게 재현되는지는 이 세션의 도구만으로는 100% 확인하지
못했다 — `MB_SETFOREGROUND | MB_TOPMOST` 플래그를 추가해 표시
가능성을 높였지만, 최종적으로 신뢰할 수 있는 진단 경로는 항상
`%LOCALAPPDATA%\HOMEZ\logs\homez-desktop.log` 파일이다(이 로그
파일은 실제로 생성·기록됨을 반복 확인했다).

**실제 재현·검증(2026-07-30)**: 실제 바탕화면 아이콘으로 최초 설정을
완료해 실 계정(`sin9484@gmail.com`, SUPER_ADMIN)이 생성된 뒤, 이후
재실행에서 `HomezHealthCheckFailed`(오류 코드 E1002)가 실제로
발생한 사례를 로그로 확인했다 — `app.desktop.bootstrap` 시작부터
`app.desktop.main.run()` 진입까지 약 10초가 FastAPI 전체 앱 import에
소요돼, 기존 10초 서버 준비 타임아웃을 그대로 넘겨버린 것이 원인
이었다(코드 결함이 아니라 콜드 스타트 조건 대비 타임아웃이 너무
짧았던 것 — 2026-07-29 스모크 테스트 기록에도 이미 "콜드 스타트
자원 경합" 변동성이 언급돼 있었다). `HEALTH_TIMEOUT_SECONDS`를
10.0 → 25.0으로 늘려 대응했다.

개발/디버그용 콘솔 실행(`scripts/start_homez_desktop.cmd`, 여전히
`app.desktop.main`을 직접 실행)은 삭제하지 않고 그대로 남겨두었다.
자세한 내용은 아래 "콘솔 숨김 실행" 절 참고.

1단계(개발용 Desktop Shell) 완료(2026-07-29). 기존 브라우저 기반 실행
방식을 대체해, PyWebView(Microsoft Edge WebView2 기반) 독립 창에서
HOMEZ 운영자 Console(`/console`)을 표시한다. PyInstaller 패키징,
`HOMEZ.exe`, 설치 프로그램은 이번 단계 범위 밖이며 다음 Gate로 분리
되어 있다.

# 기술 결정

- Electron/Tauri/React 등 신규 프론트엔드 스택을 도입하지 않았다 —
  기존 FastAPI + `app/web/**` Console을 그대로 재사용한다.
- 신규 설치 패키지는 `pywebview==6.2.1` 하나뿐이다(의존성:
  `pythonnet==3.1.0`, `clr_loader==0.3.1`, `proxy_tools==0.1.0`,
  `bottle==0.13.4` — 전부 pywebview 자체의 PyPI 의존성, 외부 실행 파일
  다운로드 없음). `PyInstaller`는 설치하지 않았다.
- Windows에서는 pywebview가 Microsoft Edge WebView2 Runtime을
  사용한다(이 머신에는 이미 150.0.4078.105가 설치되어 있음을 레지스트리
  로 확인했다).

# Desktop 실행 Flow

```
scripts/start_homez_desktop.cmd
  → venv\Scripts\python.exe -m app.desktop.main
  → SingleInstanceGuard.acquire()  (Windows named mutex)
  → app.desktop.server.start_server()
      → 127.0.0.1의 사용 가능한 임의 포트 확보
      → FastAPI(app.main:app)를 백그라운드 스레드에서 uvicorn으로 시작
      → /health가 success==True, status=="healthy", service=="HOMEZ"를
        전부 만족할 때까지 폴링(기본 10초 타임아웃)
  → webview.create_window(title="HOMEZ | EVERY HOMEZ", url=".../console", ...)
  → webview.start(icon="assets/homez-app.ico")
  → (창 종료) → DesktopServerHandle.shutdown() → SingleInstanceGuard.release()
```

# 프로세스 수명주기

`app/desktop/server.py`의 `DesktopServerHandle`이 `uvicorn.Server`와
그 서버를 실행하는 `threading.Thread` 객체를 직접 보관한다 —
`shutdown()`은 오직 이 핸들 자신이 시작한 서버/스레드만 대상으로
하며, 포트 번호나 프로세스 이름으로 다른 프로세스를 찾아 종료하는
코드 경로는 존재하지 않는다(`taskkill /F` 등 광범위 종료 없음).

- 서버 시작 실패(다른 서비스가 포트를 사용 중이거나 타임아웃) →
  `webview.create_window()`를 호출하지 않는다(빈 창을 열지 않음).
- 창 생성/시작 실패 → `finally` 블록에서 이미 시작된 서버가 있으면
  반드시 `shutdown()`한다.
- 모든 정리 작업은 `run()`의 `try/finally`에서 수행된다.

# 단일 인스턴스

`app/desktop/single_instance.py`가 Windows named mutex
(`Global\HOMEZ_Desktop_App_SingleInstance_Mutex`)를 사용한다(ctypes만
사용, pywin32 등 추가 패키지 불필요). named mutex는 프로세스가 비정상
종료되면 OS가 자동으로 소유권을 해제하므로 파일 락 특유의 stale lock
문제가 구조적으로 발생하지 않는다.

이미 실행 중이면 새 서버를 만들지 않고, `FindWindowW`+
`SetForegroundWindow`로 기존 창을 앞으로 가져오기를 best-effort로
시도한다(실패해도 예외를 던지지 않고 "HOMEZ가 이미 실행 중입니다"
안내 후 그냥 종료).

# 포트와 HOMEZ 서버 식별

고정 포트(8000 등)에 의존하지 않는다 — 매번 `find_free_port()`로
127.0.0.1의 임의 포트를 확보한다. `/health` 응답에 새 필드
`"service": "HOMEZ"`를 추가했다(`app/main.py`, 기존 `success`/`status`
필드는 그대로 유지되는 **추가적** 변경 — 기존 계약을 깨지 않음).
`is_homez_health_response()`가 `success`/`status`/`service` 세 필드를
전부 검사하며, 포트가 열려 있어도 이 조건을 만족하지 않으면(다른
프로그램) `PortIsOtherServiceError`를 던지고 절대 진행하지 않는다.

# 로컬 보안

- `app/desktop/server.py`는 `host`가 `127.0.0.1`/`localhost`가 아니면
  `ValueError`로 거부한다 — `0.0.0.0` 바인딩 자체가 불가능하다.
- Desktop 세션마다 `secrets.token_urlsafe(32)`로 세션 토큰을 생성해
  메모리에만 보관한다(`DesktopServerHandle.session_token`, 어디에도
  기록·전송하지 않음).
- **알려진 제약**: 이 세션 토큰은 현재 어떤 API 엔드포인트도 보호하지
  않는다 — 기존 `admin_guard`/인증 코드(`app/core/auth.py`,
  `authorization.py`, `guard.py`)는 이번 작업 Whitelist 밖이라 수정하지
  않았고, 임의로 우회 경로를 만들지도 않았다. 따라서 API 접근 통제는
  여전히 기존 로그인(`/auth/login`, 현재 passlib/bcrypt 비호환으로
  실사용 불가 — 별도 기지 결함)에 의존한다. 이 Phase의 실제 보안
  경계는 loopback 전용 바인딩(외부 네트워크에서 절대 도달 불가)이다.
- CORS 설정은 기존 `app/main.py` 구성을 그대로 두었다(변경 없음).
- 로그(`storage\logs\desktop-launcher.log`)에는 세션 토큰·비밀번호·
  `.env` 내용·요청/응답 본문을 기록하지 않는다(테스트로 확인:
  `test_log_file_never_contains_session_token`).

# 데이터/파일 경로

`app/desktop/paths.py`가 개발/패키징 경로 분리 계약을 제공한다:

| 함수 | 개발 모드(이번 단계) | 패키징 모드(향후, `sys.frozen`) |
|---|---|---|
| `get_data_dir()` | 저장소 루트(기존 homez.db 위치) | `%LOCALAPPDATA%\HOMEZ\data` |
| `get_logs_dir()` | `storage\logs`(기존 launcher와 동일) | `%LOCALAPPDATA%\HOMEZ\logs` |
| `get_backups_dir()` | `storage\backups` | `%LOCALAPPDATA%\HOMEZ\backups` |
| `get_config_dir()` | `storage\config` | `%LOCALAPPDATA%\HOMEZ\config` |

이번 단계는 `homez.db`를 옮기거나 복사하지 않고, 새 DB를 만들거나
Migration을 실행하지 않으며, 백업을 자동 생성하지 않는다 — 검증
과정에서도 항상 `DATABASE_URL` 환경변수로 임시 DB를 가리키게 해
실제 `homez.db`를 건드리지 않았다(크기 303104 bytes·mtime
2026-07-27 22:38:38 완전히 동일 유지 재확인).

V2.4/V3(8개 테이블)·Coupang(8개 테이블) 스키마가 실제 homez.db에
미적용인 상태는 이 Desktop Shell이 그대로 노출한다 — Console이 이미
구현해 둔 "V3 DB 스키마 적용 필요" 상태 표시를 감추거나 자동으로
고치지 않는다(Desktop Shell 자체는 DB/Migration을 전혀 건드리지
않으므로 이 동작은 구조적으로 보장된다).

# 로깅

`storage\logs\desktop-launcher.log`(UTF-8). 기록: 시작/종료 시각,
선택된 로컬 포트, 서버 준비 성공/실패, 창 시작 시도, 창 종료 이벤트,
예외 종류. 2MB를 넘으면 `.old`로 교체하는 간단한 크기 기반 rotation을
적용했다.

# 기존 런처와의 관계

- `scripts/start_homez_desktop.cmd`(신규, 기본) — Desktop Shell 실행.
- `scripts/start_homez.cmd`(기존) — 브라우저 기반 실행, fallback으로
  계속 존재. 이번 작업에서 내용을 변경하지 않았다.
- `scripts/create_homez_shortcut.ps1` — 바탕화면 바로가기 대상을
  `start_homez_desktop.cmd`로 변경했다(기존 `HOMEZ.lnk` 하나만
  대상, 다른 바로가기는 건드리지 않음).

# 테스트

`tests/test_homez_desktop.py` — 23개, GUI 없이 검증 가능한 항목은
실제로 서버를 띄워 검증한다(모킹이 아님): 임의 포트 확보, loopback
전용 강제, health 식별 로직(6가지 페이로드 조합), 다른 서비스가 점유한
포트 거부(실제 더미 HTTP 서버로 재현), 응답 없음 타임아웃, 정상
시작→health→`/console` 로드→종료 전체 사이클(각 단계에서 homez.db
스냅샷 비교), 서로 다른 두 서버 중 하나만 종료해도 나머지는 안 죽는지,
로그에 세션 토큰이 남지 않는지, 단일 인스턴스 뮤텍스(3가지 시나리오),
`app.desktop.main.run()`의 분기 로직(이미 실행 중/서버 실패/창 생성
실패 시 각각 올바르게 처리하는지, mock 기반), 런처 스크립트 자체
(ASCII, 바로가기 대상).

# 실제 Desktop 스모크 테스트 (2026-07-29 수행)

`venv\Scripts\python.exe -m app.desktop.main`을 실제로 여러 차례 실행
(매번 `DATABASE_URL`을 임시 파일로 오버라이드해 실제 homez.db는 전혀
사용하지 않음):

- **성공 확인**: 한 차례는 실제로 제목 "HOMEZ | EVERY HOMEZ"인 Windows
  창이 생성되어 30초 연속 폴링 동안 계속 존재함을 `Get-Process`/
  `FindWindowW`로 직접 확인했다. 이 프로세스를 종료하자 서버와 WebView
  호스트 프로세스가 함께 깨끗하게 종료되고 포트도 즉시 해제되었다
  (다른 프로세스는 전혀 영향받지 않음).
- **관찰된 변동성**: 짧은 시간 내에 반복 실행(수 분 내 5회 이상)하자
  일부 실행에서 서버 준비가 기본 10초 타임아웃 내에 완료되지 못하거나
  (`HomezHealthCheckFailed`로 안전하게 실패 — 빈 창을 열지 않음),
  창이 15~40초의 짧은 폴링 창 안에서 감지되지 않는 경우가 있었다.
  로그를 대조한 결과 이는 반복적인 콜드 스타트(매번 새 CLR/WebView2
  런타임 초기화 + 전체 FastAPI 앱 import)로 인한 이 샌드박스
  환경에서의 자원 경합으로 보이며, 코드 로직 결함으로 판단되는 실패는
  없었다(항상 정직하게 실패를 보고하고 빈 창을 열지 않았다).
- **한계**: 이 세션에는 OS 레벨 스크린샷 도구가 없어(Claude Browser
  도구는 웹 탭 전용이며 네이티브 WinForms 창을 캡처하지 못한다), 텍스트
  렌더링·색상·캐릭터 표시 등 픽셀 단위 시각 검증은 수행하지 못했다.
  창 존재·제목·프로세스 수명주기는 실제로 확인했지만, 실제 화면
  내용의 시각적 정확성은 사용자가 직접 확인해야 한다.

# 다음 단계 (이번 단계 범위 밖, 별도 승인 필요)

1. PyInstaller로 `HOMEZ.exe` 패키징(이번 단계는 미승인·미설치).
2. Windows 설치 프로그램, 시작 메뉴 등록, 제거 프로그램, 코드 서명.
3. `app/desktop/paths.py`의 패키징 모드 경로(`%LOCALAPPDATA%\HOMEZ\*`)
   실제 활성화 및 이관 스크립트.
4. Desktop 세션 토큰을 실제 API 인증 경계에 연결(현재는 생성만 하고
   미사용 — `app/core` 인증 파일 변경이 필요해 별도 승인 대상).
5. 사용자 환경에서의 직접 시각 확인(텍스트/색상/캐릭터 표시).

# Last Updated

2026-07-29

# Updated By

Claude
