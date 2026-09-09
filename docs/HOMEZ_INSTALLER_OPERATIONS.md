# HOMEZ Desktop 설치 운영서

2026-08-17, V7 Live Gate 4 최종 설치 프로그램 재검증(`docs/
V6_EXECUTION_LEDGER.md` "V7 Live Gate 4 — 최종 설치 프로그램
재검증(2026-08-17)" 섹션) 결과를 기준으로 처음 작성한다. 이 문서는
실제 실행 검증 결과만 기술한다 — 검증하지 못한 항목은 "미검증"으로
명시한다.

## 빌드

```
pip install -r requirements.txt -r requirements-build.txt
pyinstaller homez.spec --noconfirm
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\homez.iss ^
  "/DAppBuildDir=<pyinstaller dist>\Homez" ^
  "/DAppOutputDir=<원하는 출력 경로>"
```

- 항상 clean venv에서 빌드한다(개발 venv를 재사용하지 않는다) —
  requirements-build.txt는 PyInstaller 등 패키징 전용 도구만 담고
  런타임 requirements.txt와 분리돼 있다.
- `homez.spec`의 `hiddenimports`에 `"app.main"`이 반드시 있어야 한다
  — 없으면 PyInstaller 정적 분석이 `app/desktop/server.py`의 문자열
  기반 `uvicorn.Config("app.main:app", ...)` 참조를 놓쳐 전체 라우터
  트리가 번들에서 빠지고, 실행 시 25초 헬스체크 타임아웃(E1002)으로
  이어진다(Live Gate 3에서 확인·수정됨).
- 산출물(`dist/Homez/_internal/migrations`)에 저장소 `migrations/`와
  정확히 같은 개수의 `.sql` 파일이 포함됐는지 반드시 확인한다.

## 설치

- `PrivilegesRequired=lowest` — 관리자 권한 없는 일반 사용자 설치
  가능(필요 시 사용자가 직접 관리자 권한으로 재실행 가능).
- 프로그램 파일은 `{localappdata}\Programs\EVERY HOMEZ`(기본값),
  사용자 데이터(`homez.db`/backups/config/logs/media)는 항상
  `%LOCALAPPDATA%\HOMEZ\*`에 분리 저장된다(`app/desktop/paths.py`
  계약).
- **중요(격리 테스트/자동화 시 반드시 알아야 함)**: Inno Setup의
  `{localappdata}`/`{userdesktop}`/`{group}` 등 상수는 Windows Shell
  Folder API를 통해 해석되며, **프로세스 환경변수(LOCALAPPDATA 등)
  재정의로 전혀 격리되지 않는다** — 실측 확인됨(`.NET
  GetFolderPath`가 재정의된 env var를 무시하고 항상 실제 로그인
  계정의 진짜 경로를 반환). 설치 위치만 격리하려면 반드시 공식
  커맨드라인 오버라이드 `/DIR=<경로>`를 쓴다. Start Menu 그룹/바탕화면
  아이콘은 이 방식으로 격리할 수 없다 — 별도 Windows 사용자 계정이나
  VM 없이는 실제 로그인 계정의 진짜 프로필에 생성된다.
- 반면 패키징된 앱 자신(`Homez.exe`)은 `app/desktop/paths.py`가
  `os.environ["LOCALAPPDATA"]`를 **직접** 읽으므로, 앱 실행 시점에는
  프로세스 env var 재정의로 데이터 디렉터리를 완전히 격리할 수 있다
  (설치 프로그램과는 다른 메커니즘이므로 혼동하지 않는다).
- 조용한 설치(무인) 예시:
  ```
  HOMEZ-Setup-<버전>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART ^
    /DIR="<설치 경로>" /TASKS="desktopicon" /LOG="<로그 경로>"
  ```
  `/GROUP=`으로 Start Menu 폴더명을 바꾸려는 시도는
  `installer/homez.iss`의 `DisableProgramGroupPage=yes` 설정 때문에
  Inno Setup 공식 동작으로 **무시된다**(항상 `DefaultGroupName=HOMEZ`
  사용) — 알려진 제약, 스크립트 결함 아님.

### 중복 설치 감지 + 클린 재설치(2026-08-19 실사용 테스트로 발견·수정)

- **발견된 결함**: 기존에는 AppMutex로 "실행 중인 HOMEZ 종료"만
  요구했을 뿐, 설치 시점에 기존 설치본 존재 여부나 버전 충돌은 전혀
  검사하지 않고 `[Files]`의 `Flags: ignoreversion`으로 그냥 덮어썼다
  — 이전 버전에서 삭제·이름변경된 파일이 새 설치 후에도 설치 폴더에
  그대로 남을 수 있는 결함이었다.
- **동작**: `InitializeSetup()`이 이 설치 프로그램의 고정 AppId
  (`installer/homez.iss`의 `[Setup] AppId` GUID)로 언인스톨 레지스트리
  키(`HKCU`/`HKLM` 양쪽 확인) 존재 여부를 조회해 기존 설치를
  감지한다. 발견 시 대화상자로 [예: 클린 재설치(기존 프로그램 파일
  전체 삭제 후 새로 설치)] / [아니오: 덮어쓰기 업그레이드(기본값,
  기존 동작과 동일)] / [취소: 설치 중단]을 묻는다. 클린 재설치는
  `{app}`(프로그램 설치 디렉터리, 기본값
  `%LOCALAPPDATA%\Programs\EVERY HOMEZ`)만 삭제하며,
  `%LOCALAPPDATA%\HOMEZ`(사용자 데이터)는 이 절차에서 전혀 건드리지
  않는다 — 기존 프로그램 파일/사용자 데이터 분리 원칙을 그대로
  따른다.
- **실측으로 확인한 두 가지 함정(둘 다 최초 구현에서 실패, 재설계로
  해결)**:
  1. `NextButtonClick(wpSelectDir)`에서 감지하도록 처음 구현했으나,
     `/VERYSILENT` 무인 설치는 마법사 페이지 자체를 건너뛰어 이
     이벤트가 전혀 호출되지 않음을 실제 무인 설치 재현으로 확인했다
     — 무인 배포 경로에서 클린 재설치 감지가 조용히 아무 효과가
     없었다. `InitializeSetup()`(대화형·무인 모두 항상 호출됨)으로
     옮겨 해결.
  2. `InitializeSetup()` 안에서 `{app}` 상수를 `ExpandConstant`로
     풀면 "An attempt was made to expand the app constant before it
     was initialized" 런타임 오류로 즉시 죽는다는 것도 실제 재현으로
     확인했다 — `{app}`은 이 시점에 아직 초기화되지 않는다. 그래서
     기존 설치 감지는 `{app}`을 전혀 쓰지 않고 레지스트리 언인스톨
     키만으로 판단하며, 실제 삭제는 `{app}`이 안전하게 초기화되는
     `CurStepChanged(ssInstall)`에서 수행한다.
  3. (부수 발견) `{#SetupSetting("AppId")}` 전처리기 매크로는
     `[Setup]`의 AppId 원본 텍스트를 Inno 자체의 `{{`→`{` 런타임
     이스케이프 적용 **이전** 그대로(이중 중괄호) 반환해, 실제
     레지스트리 키(단일 중괄호)와 일치하지 않는다는 것도 디버그
     로그로 직접 확인했다 — 그래서 GUID를 Pascal 문자열 리터럴로
     직접 옮겨 썼다(`[Setup]`의 AppId가 바뀌면 이 리터럴도 함께
     수동 갱신 필요, `AppVersion`과 동일한 수동 동기화 원칙).
- **무인 설치 안전장치**: `HOMEZ_TEST_FORCE_REINSTALL_DECISION`
  환경변수(`CLEAN`/`UPGRADE`/`CANCEL`)가 설정되지 않은 실제
  `/SILENT`·`/VERYSILENT` 무인 배포에서는 대화상자를 띄울 수 없으므로
  **항상 기존 배포 동작과 동일한 "덮어쓰기 업그레이드"를 유지한다**
  — 무인 설치에서 의도치 않게 프로그램 디렉터리 전체 삭제가 실행되는
  일은 없다.
- **격리 실측 검증(이 세션, `/DIR` 오버라이드로 격리된 임시 경로에서
  실제 컴파일된 exe로 수행)**: 신규 설치 → 존재 마커 파일 생성 →
  `HOMEZ_TEST_FORCE_REINSTALL_DECISION=CLEAN` 재설치(마커 삭제 확인,
  `DirExists`/`DelTree` 실제 실행 로그로 재확인) → 새 마커 생성 →
  `=UPGRADE` 재설치(마커 보존 확인) → `=CANCEL` 재설치(설치 중단,
  종료 코드 비정상, 마커 보존 확인) → 테스트 훅 없는 순수
  `/VERYSILENT` 재설치(마커 보존 확인, 위 "무인 설치 안전장치"와
  일치) 전부 실제 재현으로 통과.
- **알려진 한계**: 대화형 마법사에서 사용자가 "설치 위치 선택"
  페이지에서 기본값과 다른 경로로 직접 바꾼 경우까지는 재검사하지
  않는다(레지스트리 기반 감지 자체는 경로와 무관하게 여전히
  동작하지만, 그 경우 클린 재설치가 삭제하는 대상은 새로 선택한
  경로다 — 사용자가 실제로 기존 설치가 있는 다른 경로를 지정했을
  때만 의미가 갈릴 수 있는 드문 경우).

## 최초 실행 / 관리자 설정

- 최초 관리자 계정 생성 API(`app/core/desktop_setup.py`)는 loopback +
  Desktop 모드 + Desktop session token + **pywebview js_api 전용
  setup nonce** + `users=0` + SUPER_ADMIN 역할 존재 + Host/Origin
  일치 + 비밀번호 정책을 전부 요구한다 — setup nonce는 순수 HTTP
  클라이언트로는 얻을 수 없다(js_api 브리지 전용, 의도된 보안
  설계). 자동화 테스트에서는 게이트 통과 후 실제 로직
  (`app.core.first_admin_setup.atomic_create_first_admin`)만 직접
  호출해 검증한다(HTTP 게이트 자체를 우회하는 게 아니라, 게이트가
  이미 통과시킨 뒤 호출하는 함수와 정확히 같은 함수를 쓰는 것).
- 일반 로그인(`POST /auth/login`)은 pywebview 브리지 없이 순수
  HTTP로 완전히 검증 가능하다.
- 로그인 성공 시 콘솔 프론트엔드 JS가 `save_console_session()`(js_api
  브리지)을 호출해 실제 Windows Credential Manager
  (`CRED_PERSIST_LOCAL_MACHINE`)에 세션을 저장한다 — target name이
  `HOMEZ:console_session:{user_id}`/`HOMEZ:console_active_user`
  (완전 전역, 설치본 구분 없음)로 고정돼 있어, **격리 테스트가 실제
  로그인 UI를 거치면 이 머신의 실제 Credential Manager 항목을
  덮어쓸 수 있다.** 순수 HTTP 로그인(`POST /auth/login` 직접 호출)은
  이 브리지를 거치지 않으므로 안전하다 — 자동화 검증은 항상 이
  경로를 쓴다.

## 백업

- 공식 서비스 경로: `POST /backups`(admin_guard, 로그인 필요) —
  `app.desktop.paths.get_homez_db_path(confirm=True)`로만 실제 DB
  경로를 얻으므로 경로 주입이 불가능하다.
- 응답에 `sha256`/`integrity_check_result`가 포함되며 실측
  검증에서 정상 동작 확인(`integrity_check_result="ok"`).

## 복원 — **2026-08-17 Gate R-2/R-4로 실행 엔드포인트 신설·WinError 5 수정 완료**

- `POST /restores/validate`(읽기 전용 검증), `GET /restores`(이력
  조회)에 더해 **`POST /restores`(실제 실행)가 신설됐다.**
- **이전에 있던 `os.replace()` WinError 5 결함은 두 겹으로 수정됐다**:
  1. `RestoreService.restore()`가 `os.replace()` 직전에 `self.db`의
     bind(Engine)를 명시적으로 `close()`+`dispose()`한다(자기 자신의
     중간 쓰기가 커넥션을 되살리는 문제까지 포함해 계측으로 원인
     확정 후 수정).
  2. **실제 파일 교체는 살아있는 Desktop 서버 프로세스 "안"에서 절대
     하지 않는다** — `POST /restores`는 복원 계획을 저장하고
     `app/desktop/restore_helper.py`를 통해 HOMEZ 전체를 정상
     종료시킨 뒤, 완전히 별도인 Restore Helper 프로세스가 부모 종료를
     확인한 뒤에만 실제 교체를 수행하고 HOMEZ를 재기동한다(멀티스레드
     서버가 자기 자신의 커넥션만 정리해서는 다른 요청 스레드의
     TOCTOU 경쟁을 막을 수 없다는 계측 근거 때문 — 상세는
     `docs/V6_EXECUTION_LEDGER.md` "V7 Live Gate 4 복원 결함 수정"
     섹션).
  3. **2026-08-17 격리 설치 E2E로 전 과정(백업 → 데이터 변경 →
     `POST /restores` 실행 → 앱 자동 종료 → Helper 파일 교체 →
     자동 재기동 → 데이터가 백업 시점 값으로 정확히 복귀)이 실제
     packaged exe에서 성공함을 실측 확인했다.**
- `validate_backup_file()`은 SHA-256 일치 + `PRAGMA integrity_check`에
  더해, **2026-08-17 코디네이터 명시 지시로 필수 테이블
  (`REQUIRED_CORE_TABLES` = `schema_migrations`/`users`/`companies`/
  `roles`) 존재 검증이 재활성화됐다** — HOMEZ 스키마와 무관한(유효한
  SQLite이지만 위 핵심 테이블이 없는) 파일은 이제 `restorable=False`
  로 명확히 거부되고, `reason`에 없는 테이블 이름이 그대로 나열된다.
  이전에 이 판정을 되돌렸던 근거였던 `tests/test_restore_engine.py`의
  전제는 이번 세션에서 이 안전 요구사항에 맞게 갱신됐다(테스트
  삭제가 아니라 기대치 수정 — `docs/V6_EXECUTION_LEDGER.md` 참고).
- **알려진 특성(이번 수정이 만든 새 제약이 아니라 원래부터 있던
  동작, 2026-08-17 재확인)**: `os.replace()`가 target 파일 전체를
  교체하므로, 오래된 백업으로 복원하면 target 자신에 쌓여 있던 최근
  `restore_attempts` 이력도 그 백업 시점 상태로 함께 교체된다(데이터
  손실은 아님 — 그 이력은 각 복원 시점의 사전 안전 백업 파일 안에
  스냅샷으로 남아 있다).

## 제거

- 기본 동작: 프로그램 파일만 제거, 사용자 데이터(`%LOCALAPPDATA%\
  HOMEZ`)는 보존(대화상자 기본 선택 = 보존). 자동화 테스트에서는
  `HOMEZ_TEST_FORCE_DATA_DECISION=KEEP`/`DELETE` env var로 대화상자를
  대체할 수 있다(테스트 전용 훅, 일반 사용자 환경에서는 절대
  개입하지 않음).
- **`DELETE`(완전삭제) 결정을 실제로 실행하는 것은 격리 테스트
  환경에서 위험하다** — 삭제 대상 경로(`{localappdata}\HOMEZ`)가
  설치 프로그램과 같은 이유로 격리 불가능해, 실행하면 이 머신의
  진짜 사용자 데이터가 삭제된다. 별도 Windows 사용자 계정이나 VM
  없이는 이 옵션을 안전하게 자동화 검증할 수 없다 — 2026-08-17
  세션은 이 이유로 실행을 보류했다.
- 언인스톨은 `[Files]`에 등록된 파일만 추적해 삭제한다 — 앱이
  런타임에 만든, 스크립트가 모르는 파일(예: 아래 "알려진 결함" 참고)
  은 삭제되지 않고 설치 폴더에 남을 수 있다.
- 재설치 시 보존된 사용자 데이터(관리자 계정 포함)를 정상적으로
  다시 인식한다(실측 확인).

## 실행 안정성

- cold start(빈 사용자 데이터 디렉터리에서 최초 실행)와 warm
  restart(기존 데이터로 재시작) 모두 2026-08-17 실측에서 25초
  헬스체크 기준을 안정적으로 충족했다(7.4초~17.2초 범위, 3회씩).
- 측정 시 자식 프로세스의 `WorkingDirectory`를 반드시 EXE가 있는
  폴더로 명시해야 한다 — 그렇지 않으면 부모 프로세스의 cwd를
  상속하고, 저장소 루트에서 실행할 경우 저장소의 개발용 `.env`
  (`DATABASE_URL=sqlite:///homez.db`, 상대경로)가 pydantic-settings에
  의해 우선 적용되어 세션 엔진이 실제 저장소 `homez.db`를 가리키게
  된다 — `app/database/session.py::get_engine_db_path()`와
  `app/desktop/main.py`의 "DB 경로 불일치 감지" fail-fast 가드가
  이를 정확히 감지해 즉시 중단시키므로 실제 DB는 안전하지만, 측정
  자체는 실패한다(테스트 하네스 문제, 실제 설치본은 바로가기의
  WorkingDirectory가 이미 올바르고 PyInstaller 번들에 `.env`가
  포함되지 않으므로 이 문제가 발생하지 않는다).

## 알려진 결함 (2026-08-17 갱신)

1. ~~`RestoreService.restore()`의 Windows `os.replace()` 실패~~ —
   **2026-08-17 Gate R-0~R-4로 수정 완료 및 격리 설치 E2E로 실측
   검증됨**(위 "복원" 절 참고).
2. `app/core/logger.py`가 CWD 상대경로(`logs/homez.log`)에 로그를
   써서, 실제 설치본에서도 설치 폴더 안에 로그가 계속 쌓이고
   언인스톨 후에도 남는다. **2026-08-17 세션에서도 원인만 재확인,
   수정하지 않음**(이번 Gate의 명시적 Whitelist 밖).
3. ~~`validate_backup_file()`이 필수 테이블 누락 백업을 거부하지
   않음~~ — **2026-08-17 코디네이터 지시로 재활성화 완료**(위 "복원"
   절 참고).
4. 완전삭제 언인스톨 옵션의 실제 실행 검증은 안전상의 이유로
   완료되지 못했다 — 별도 Windows 계정/VM에서 재검증 필요.
   2026-08-17 세션도 같은 이유로 미실행.
5. 재시작 후 로그인 세션 유지(Windows Credential Manager)도
   실제 Credential Manager를 건드리므로 2026-08-17 세션 역시
   실행하지 않고 코드 리뷰로만 대체했다.
6. (참고) `app/database/seed_role_permission.py`의 `role_permissions`
   시딩이 ORM 관계 미매핑으로 조용한 no-op — 기존에 이미 문서화된
   결함, 이 문서가 다루는 설치 범위 밖.
