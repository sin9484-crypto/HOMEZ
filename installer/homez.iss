; =========================================================
; Homez OS
;
; File : installer/homez.iss
;
; HOMEZ V7 Live Gate 4 (2026-08-16) — Inno Setup 6 설치 프로그램 스크립트.
; CTO가 명시적으로 Inno Setup을 선정한 뒤 처음 작성된 공식 .iss다.
;
; 이 스크립트가 패키징하는 산출물은 `homez.spec`(Live Gate 3에서
; hiddenimports에 "app.main"을 추가해 E1002 헬스체크 타임아웃 근본원인을
; 수정한 최신 버전)으로 만든 PyInstaller onedir 빌드다. `[Files]`의
; `Source`는 빌드 산출물 디렉터리를 가리키며, 저장소를 오염시키지
; 않기 위해 항상 저장소 밖(스크래치패드 등) 경로를 사용한다 —
; 아래 `AppBuildDir` 값을 실제 `pyinstaller homez.spec --noconfirm`
; 산출물(`dist/Homez`) 위치로 바꿔서 사용한다.
;
; 설계 원칙:
;   1) 관리자 권한 최소화 — 기본 설치 위치를 {localappdata}\Programs\
;      EVERY HOMEZ로 두어 관리자 권한 없이 설치 가능(PrivilegesRequired
;      =lowest). 필요하면 사용자가 직접 관리자 권한으로 재실행할 수
;      있게 PrivilegesRequiredOverridesAllowed=dialog를 허용한다.
;   2) 프로그램 파일과 사용자 데이터 분리 — 프로그램은 설치 경로에,
;      사용자 데이터(homez.db/backups/logs/config/media)는 항상
;      %LOCALAPPDATA%\HOMEZ\* (app/desktop/paths.py의 기존 계약)에
;      남는다. 이 스크립트는 %LOCALAPPDATA%\HOMEZ를 설치 대상으로
;      전혀 건드리지 않는다(설치는 프로그램 파일만 배치).
;   3) 언인스톨 시 사용자 데이터 보존 정책 확정 — 기본은 "보존"이다.
;      [Code]의 CurUninstallStepChanged가 언인스톨 마지막 단계에서
;      사용자에게 명시적으로 물어보고, "예"를 선택한 경우에만
;      %LOCALAPPDATA%\HOMEZ 전체를 삭제한다(완전 삭제는 opt-in).
;   4) 이미 실행 중인 HOMEZ 인스턴스가 있으면 설치/제거 전 종료를
;      요구한다(AppMutex — app/desktop/single_instance.py의 실제
;      Named Mutex와 동일한 이름을 참조).
;   5) 한국어/영어 설치 UI 동시 지원([Languages] Korean/English).
;   6) 중복 설치 감지 + 클린 재설치 옵션(2026-08-19 실사용 테스트로
;      발견된 결함 수정) — 기존에는 AppMutex로 "실행 중 종료"만
;      요구했을 뿐, 설치 시점에 기존 설치본 존재 여부나 버전 충돌은
;      전혀 검사하지 않고 `Flags: ignoreversion`으로 그냥 덮어썼다
;      (이전 버전에서 삭제·이름변경된 파일이 새 설치 후에도 설치
;      폴더에 그대로 남을 수 있는 결함). [Code]의 `InitializeSetup`이
;      이 설치 프로그램의 고정 AppId로 언인스톨 레지스트리 키
;      존재 여부를 조회해 기존 설치를 감지한다(`{app}` 상수는 이
;      시점에 아직 초기화되지 않아 대화형/무인 설치 모두에서 런타임
;      오류가 나므로 절대 여기서 쓰지 않는다 — 실제 재현으로 확인한
;      제약, 아래 함수 주석 참고). 발견 시 클린 재설치(기존 프로그램
;      파일 전체 삭제 후 새로 설치) / 덮어쓰기 업그레이드(기본값,
;      기존 동작 유지) / 설치 중단을 사용자에게 묻는다. 실제 삭제는
;      `CurStepChanged(ssInstall)`에서 `{app}` 상수가 안전하게
;      초기화된 시점에 수행하며, `{app}`(프로그램 설치 디렉터리)만
;      삭제하고 %LOCALAPPDATA%\HOMEZ(사용자 데이터)는 이 절차에서
;      전혀 건드리지 않는다 — 원칙 2)의 분리를 그대로 따른다.
;      `HOMEZ_TEST_FORCE_REINSTALL_DECISION` 환경변수(CLEAN/UPGRADE/
;      CANCEL)는 CurUninstallStepChanged의 기존 테스트 훅과 동일한
;      목적의 테스트 전용 오버라이드로, 설정되지 않은 일반 사용자
;      환경(실제 배포)에서는 절대 개입하지 않고 항상 대화상자를
;      띄운다(단, 실제 /SILENT·/VERYSILENT 무인 배포에서 테스트
;      훅도 없으면 대화상자를 띄울 수 없으므로 기존 배포 동작과
;      동일한 "덮어쓰기 업그레이드"를 유지한다 — 무인 설치에서
;      의도치 않게 전체 삭제가 실행되는 일은 없다).
; =========================================================

#ifdef HOMEZ_TEST_INSTALLER
  #define AppName "HOMEZ TEST ISOLATED"
#else
  #define AppName "HOMEZ"
#endif
#define AppPublisher "EVERY HOMEZ"
#define AppVersion "2.1.4"
; app/core/version.py(MAJOR=2, MINOR=1, PATCH=4)와 동일해야 한다 —
; version.py가 바뀌면 이 값도 함께 수동으로 갱신한다(빌드 자동화가
; 아직 이 값을 자동 추출하지 않는다, 알려진 한계).
#define AppURL "https://everyhomez.example"
#define AppExeName "Homez.exe"

; ---------------------------------------------------------
; 빌드 산출물 위치. 실제 사용 시 pyinstaller 산출물(dist/Homez) 경로로
; 지정한다. 커맨드라인에서 /DAppBuildDir=... 로 덮어쓸 수 있다
; (ISCC.exe "installer\homez.iss" "/DAppBuildDir=<dist 경로>").
; ---------------------------------------------------------
#ifndef AppBuildDir
  #define AppBuildDir "..\dist\Homez"
#endif

#ifndef AppOutputDir
  #define AppOutputDir "..\installer-output"
#endif

[Setup]
#ifdef HOMEZ_TEST_INSTALLER
AppId={{000D9771-99FC-4AC7-BACC-1A115E256113}
#else
AppId={{6F5B2E6E-6C9C-4C7B-9C2B-3A6D4E9F1A02}
#endif
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
#ifdef HOMEZ_TEST_INSTALLER
DefaultDirName={localappdata}\Programs\HOMEZ-ISOLATED-TEST
DefaultGroupName=HOMEZ TEST ISOLATED
#else
DefaultDirName={localappdata}\Programs\EVERY HOMEZ
DefaultGroupName=HOMEZ
#endif
DisableProgramGroupPage=yes
; 관리자 권한 없이 설치 가능(사용자별 설치) — 필요 시 사용자가 직접
; "관리자 권한으로 실행"을 선택하면 override 가능.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#AppOutputDir}
#ifdef HOMEZ_TEST_INSTALLER
OutputBaseFilename=HOMEZ-TEST-ISOLATED-Setup-{#AppVersion}
#else
OutputBaseFilename=HOMEZ-Setup-{#AppVersion}
#endif
SetupIconFile=..\assets\homez-app.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 이미 실행 중인 HOMEZ 인스턴스가 있으면 설치/제거 전 종료를 요구한다.
; app/desktop/single_instance.py::MUTEX_NAME과 정확히 동일한 이름.
AppMutex=Global\HOMEZ_Desktop_App_SingleInstance_Mutex
ArchitecturesInstallIn64BitMode=x64compatible
DisableWelcomePage=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
korean.PreserveDataTitle=사용자 데이터 보존
korean.PreserveDataPrompt=HOMEZ 사용자 데이터(회사 정보, DB, 백업, 로그 — %LOCALAPPDATA%\HOMEZ)를 유지하시겠습니까?%n%n[예]를 선택하면 데이터를 그대로 보존합니다(재설치 시 이어서 사용 가능).%n[아니오]를 선택하면 사용자 데이터까지 완전히 삭제합니다(복구 불가).
korean.LaunchAfterInstall=설치를 마친 후 HOMEZ 실행
korean.DataSeparationInfo=HOMEZ는 프로그램 파일과 사용자 데이터(%LOCALAPPDATA%\HOMEZ)를 분리해서 관리합니다. 제거 시 별도로 묻기 전까지 사용자 데이터는 보존됩니다.
english.PreserveDataTitle=Preserve User Data
english.PreserveDataPrompt=Do you want to keep your HOMEZ user data (company info, database, backups, logs — %LOCALAPPDATA%\HOMEZ)?%n%nChoose Yes to keep the data (can resume after reinstalling).%nChoose No to permanently delete the user data as well (cannot be undone).
english.LaunchAfterInstall=Launch HOMEZ after installation
english.DataSeparationInfo=HOMEZ keeps program files and user data (%LOCALAPPDATA%\HOMEZ) separate. User data is preserved on uninstall unless you explicitly choose to remove it.
korean.ExistingInstallPrompt=기존 HOMEZ 설치가 발견되었습니다(설치된 버전: %1, 새 버전: %2).%n%n[예]를 선택하면 기존 프로그램 파일을 모두 삭제한 뒤 새로 설치합니다(클린 재설치 — 사용자 데이터 %LOCALAPPDATA%\HOMEZ는 영향받지 않습니다).%n[아니오]를 선택하면 기존 파일 위에 덮어써서 업그레이드합니다(기본값, 기존 동작과 동일).%n[취소]를 선택하면 설치를 중단합니다.
korean.UnknownVersion=알 수 없음
english.ExistingInstallPrompt=An existing HOMEZ installation was found (installed version: %1, new version: %2).%n%nChoose Yes to delete all existing program files and perform a clean reinstall (your user data at %LOCALAPPDATA%\HOMEZ is not affected).%nChoose No to upgrade by overwriting existing files (default, same as prior behavior).%nChoose Cancel to abort setup.
english.UnknownVersion=Unknown

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#AppBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
#ifdef HOMEZ_TEST_INSTALLER
; 테스트 설치는 공식 바로가기·시작메뉴와 충돌할 수 있는 항목을
; 만들지 않는다. 테스트 실행은 검증 harness가 명시적으로 수행한다.
#else
Name: "{group}\HOMEZ"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,HOMEZ}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\HOMEZ"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon
#endif

#ifndef HOMEZ_TEST_INSTALLER
[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchAfterInstall}"; Flags: nowait postinstall skipifsilent; Check: ShouldLaunchAfterInstall
#endif

[Code]
var
  { InitializeSetup()에서 결정되고 CurStepChanged(ssInstall)에서
    소비된다. 기존 설치가 없으면 False로 유지되며 아무 동작도 하지
    않는다(기존 ignoreversion 덮어쓰기 동작 그대로). }
  CleanReinstallRequested: Boolean;

{ 운영 설치의 대화형 실행에만 쓰는 이중 방어. 테스트 설치는 전처리
  단계에서 [Run] 섹션 자체를 제외하므로 이 함수에 의존하지 않는다. }
function ShouldLaunchAfterInstall(): Boolean;
begin
  Result := not WizardSilent();
end;

function IsPreviouslyInstalled(out DisplayVersion: String): Boolean;
var
  RegPath: String;
begin
  { 이 설치 프로그램은 PrivilegesRequired=lowest라 보통 HKCU 아래
    등록되지만, 사용자가 관리자 권한으로 재실행했을 수도 있으므로
    HKCU를 먼저 확인하고 HKLM도 보조로 확인한다. AppId는 [Setup]에
    고정된 GUID와 항상 동일하다(재설치·업그레이드 시에도 불변).
    존재 판정은 언인스톨 레지스트리 키 자체로 하고, DisplayVersion은
    표시용으로만 별도 조회한다 — DisplayVersion 값이 어떤 이유로든
    비어 있어도(정상적으로는 발생하지 않음, AppVersion이 항상 설정돼
    있으므로) "기존 설치 없음"으로 잘못 판정하지 않기 위함이다. }
  { 실측 확인(2026-08-19): SetupSetting 매크로로 AppId 설정값을 읽으면
    Setup 섹션 원본 텍스트를 Inno 자체의 이중 중괄호 런타임 이스케이프
    적용 이전 그대로(이중 중괄호) 반환한다 — 실제 레지스트리 키는
    이스케이프 적용 후의 단일 중괄호 형태이므로 그 매크로로 만든
    경로는 절대 일치하지 않는다(실측으로 발견, 디버그 로그로 직접
    확인). 따라서 Setup 섹션에 고정 리터럴로 박혀 있는 이 GUID를
    Pascal 문자열 리터럴로 직접 그대로 옮겨 쓴다 — Setup 섹션의
    AppId 줄이 바뀌면 이 줄도 함께 수동으로 갱신해야 한다(AppVersion과
    동일한 수동 동기화 원칙, 파일 상단 주석 참고). }
#ifdef HOMEZ_TEST_INSTALLER
  RegPath := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{000D9771-99FC-4AC7-BACC-1A115E256113}_is1';
#else
  RegPath := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F5B2E6E-6C9C-4C7B-9C2B-3A6D4E9F1A02}_is1';
#endif
  Result := RegKeyExists(HKCU, RegPath) or RegKeyExists(HKLM, RegPath);
  DisplayVersion := '';
  if Result then
  begin
    if not RegQueryStringValue(HKCU, RegPath, 'DisplayVersion', DisplayVersion) then
      RegQueryStringValue(HKLM, RegPath, 'DisplayVersion', DisplayVersion);
  end;
end;

#ifdef HOMEZ_TEST_INSTALLER
procedure CreateTestDataSentinel(); forward;
#endif

function InitializeSetup(): Boolean;
var
  InstalledVersion: String;
  PromptText: String;
  Decision: Integer;
  TestOverride: String;
begin
  Result := True;
  { 실측 확인(2026-08-19), 두 차례 시행착오 기록:
    1) NextButtonClick(wpSelectDir)은 /VERYSILENT 무인 설치에서 전혀
       호출되지 않는다 — 마법사 페이지 자체를 건너뛰기 때문이다.
    2) InitializeSetup() 안에서 app 상수를 ExpandConstant로 풀면
       "An attempt was made to expand the app constant before it was
       initialized" 런타임 오류로 즉시 죽는다 — app 상수는 이 시점에
       아직 초기화되지 않는다(대화형/무인 모두 동일하게 실패, 실제
       재현으로 확인).
    따라서 기존 설치 감지는 app 상수(디렉터리)를 전혀 쓰지 않고 이
    설치 프로그램의 AppId로 고정된 레지스트리 언인스톨 키만으로
    판단한다 — InitializeSetup에서 항상 안전하게 조회 가능하고
    대화형/무인 양쪽에서 동일하게 호출된다. 실제 삭제 대상 디렉터리는
    아래 CurStepChanged(ssInstall)에서, app 상수가 안전하게 초기화된
    시점에 별도로 구한다. }
  if IsPreviouslyInstalled(InstalledVersion) then
  begin
    if InstalledVersion = '' then
      InstalledVersion := CustomMessage('UnknownVersion');

    { 주의: Inno Setup 컴파일러는 Pascal 코드 안에서도 줄 맨 앞이
      '['이면 새 섹션 헤더로 잘못 해석한다 — 배열 리터럴의 '['을
      절대 줄 맨 앞에 두지 않는다. }
    PromptText := FmtMessage(CustomMessage('ExistingInstallPrompt'), [
      InstalledVersion, '{#AppVersion}']);

    { 테스트 전용 훅 — CurUninstallStepChanged의
      HOMEZ_TEST_FORCE_DATA_DECISION과 정확히 같은 목적·같은 원칙.
      네이티브 MsgBox는 이 저장소의 자동화 도구로 클릭할 수 없어
      격리된 자동 검증을 위해 환경변수로 결정을 강제할 수 있게
      한다(무인 설치에서도 대화상자 없이 결정을 확정할 수 있다는
      뜻이므로 실제 /VERYSILENT 무인 배포에서도 유용하다). 설정되지
      않은 일반 대화형 환경(실제 배포)에서는 절대 개입하지 않고
      항상 대화상자를 띄운다 — 프로덕션 동작은 변하지 않는다. }
    TestOverride := GetEnv('HOMEZ_TEST_FORCE_REINSTALL_DECISION');
    if TestOverride = 'CLEAN' then
      Decision := IDYES
    else if TestOverride = 'UPGRADE' then
      Decision := IDNO
    else if TestOverride = 'CANCEL' then
      Decision := IDCANCEL
    else if WizardSilent() then
      { 테스트 훅이 없는 실제 무인(/SILENT, /VERYSILENT) 배포에서는
        MsgBox를 띄울 수 없으므로(응답 불가로 멈춤) 기존 배포 동작과
        동일한 "덮어쓰기 업그레이드"를 그대로 유지한다 — 대화형
        기본값(MB_DEFBUTTON2, 아래)과 동일한 선택. }
      Decision := IDNO
    else
      { 기본 선택(Enter 키)은 "아니오"(덮어쓰기 업그레이드) —
        기존 배포 동작을 바꾸지 않는 쪽이 기본값이 되도록
        MB_DEFBUTTON2를 "아니오"에 배치했다(삭제가 기본값이
        아니어야 한다는 원칙은 CurUninstallStepChanged의
        MB_DEFBUTTON1 선택과 동일 철학). }
      Decision := MsgBox(PromptText, mbConfirmation,
        MB_YESNOCANCEL or MB_DEFBUTTON2);

    case Decision of
      IDYES:
        CleanReinstallRequested := True;
      IDNO:
        CleanReinstallRequested := False;
      IDCANCEL:
        Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssInstall) and CleanReinstallRequested then
  begin
    { app 상수(프로그램 설치 디렉터리)만 삭제한다 — %LOCALAPPDATA%\HOMEZ
      (사용자 데이터)는 이 절차에서 절대 참조하지 않는다. 삭제 후
      파일 설치 단계가 정상적으로 그 디렉터리를 재생성하며 새 버전을
      설치한다. app 상수는 여기서는 이미 안전하게 초기화돼 있다
      (InitializeSetup 시점과 달리 ssInstall은 디렉터리 확정 이후에
      발생한다, 실측 확인). }
    if DirExists(ExpandConstant('{app}')) then
      DelTree(ExpandConstant('{app}'), True, True, True);
  end;
#ifdef HOMEZ_TEST_INSTALLER
  if CurStep = ssPostInstall then
  begin
    CreateTestDataSentinel();
  end;
#endif
end;

function CanonicalPath(Value: String): String;
begin
  Value := Trim(Value);
  if Value = '' then
  begin
    Result := '';
    exit;
  end;
  Result := RemoveBackslashUnlessRoot(ExpandFileName(Value));
end;

function IsSameOrChildPath(PathValue: String; RootValue: String): Boolean;
var
  PathCanonical: String;
  RootCanonical: String;
begin
  PathCanonical := CanonicalPath(PathValue);
  RootCanonical := CanonicalPath(RootValue);
  if (PathCanonical = '') or (RootCanonical = '') then
  begin
    Result := False;
    exit;
  end;

  Result := (CompareText(PathCanonical, RootCanonical) = 0) or
    (Pos(Uppercase(AddBackslash(RootCanonical)),
      Uppercase(AddBackslash(PathCanonical))) = 1);
end;

function PathsOverlap(FirstPath: String; SecondPath: String): Boolean;
begin
  Result := IsSameOrChildPath(FirstPath, SecondPath) or
    IsSameOrChildPath(SecondPath, FirstPath);
end;

function IsDriveOrLocalAppDataRoot(PathValue: String): Boolean;
var
  PathCanonical: String;
  LocalAppDataCanonical: String;
begin
  PathCanonical := CanonicalPath(PathValue);
  LocalAppDataCanonical := CanonicalPath(ExpandConstant('{localappdata}'));
  Result := (PathCanonical = '') or
    (CompareText(PathCanonical, ExtractFileDrive(PathCanonical) + '\') = 0) or
    (CompareText(PathCanonical, LocalAppDataCanonical) = 0);
end;

function IsSafeTestDataDir(DataDir: String): Boolean;
var
  CanonicalDataDir: String;
  OfficialDataDir: String;
  SentinelPath: String;
begin
  CanonicalDataDir := CanonicalPath(DataDir);
  OfficialDataDir := CanonicalPath(ExpandConstant('{localappdata}\HOMEZ'));
  SentinelPath := AddBackslash(CanonicalDataDir) +
    '.homez-isolated-test-root';

  Result := (CanonicalDataDir <> '') and
    (not IsDriveOrLocalAppDataRoot(CanonicalDataDir)) and
    (not PathsOverlap(CanonicalDataDir, OfficialDataDir)) and
    FileExists(SentinelPath);
end;

#ifdef HOMEZ_TEST_INSTALLER
procedure CreateTestDataSentinel();
var
  TestDataDir: String;
  SentinelPath: String;
begin
  TestDataDir := CanonicalPath(GetEnv('HOMEZ_DATA_ROOT'));
  if (TestDataDir = '') or
     IsDriveOrLocalAppDataRoot(TestDataDir) or
     PathsOverlap(TestDataDir, ExpandConstant('{localappdata}\HOMEZ')) then
  begin
    Log('HOMEZ test data sentinel not created: unsafe HOMEZ_DATA_ROOT');
    exit;
  end;

  if not ForceDirectories(TestDataDir) then
  begin
    Log('HOMEZ test data sentinel not created: directory creation failed');
    exit;
  end;

  SentinelPath := AddBackslash(TestDataDir) +
    '.homez-isolated-test-root';
  if not SaveStringToFile(SentinelPath, 'HOMEZ_ISOLATED_TEST', False) then
    Log('HOMEZ test data sentinel creation failed');
end;
#endif

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  DeleteData: Integer;
  TestOverride: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
#ifdef HOMEZ_TEST_INSTALLER
    DataDir := CanonicalPath(GetEnv('HOMEZ_DATA_ROOT'));
    TestOverride := GetEnv('HOMEZ_TEST_FORCE_DATA_DECISION');
    if TestOverride <> 'DELETE' then
    begin
      Log('HOMEZ isolated test data preserved: DELETE not explicitly requested');
      exit;
    end;
    if not IsSafeTestDataDir(DataDir) then
    begin
      Log('HOMEZ isolated test data deletion blocked: unsafe path or missing sentinel');
      exit;
    end;
    DelTree(DataDir, True, True, True);
#else
    DataDir := ExpandConstant('{localappdata}\HOMEZ');
    if DirExists(DataDir) then
    begin
      { 2026-08-16 Live Gate 4 — 테스트 전용 훅. 네이티브 Windows
        MsgBox는 이 저장소의 자동화 도구로 클릭할 수 없으므로, 격리된
        자동 검증을 위해 환경변수로 결정을 강제할 수 있게 한다.
        HOMEZ_TEST_FORCE_DATA_DECISION이 설정되지 않은 일반 사용자
        환경(실제 배포)에서는 절대 개입하지 않고 항상 대화상자를
        띄운다 — 프로덕션 동작은 변하지 않는다. }
      TestOverride := GetEnv('HOMEZ_TEST_FORCE_DATA_DECISION');
      if TestOverride = 'DELETE' then
        DeleteData := IDNO
      else if TestOverride = 'KEEP' then
        DeleteData := IDYES
      else
        DeleteData := MsgBox(
          ExpandConstant('{cm:PreserveDataPrompt}'),
          mbConfirmation,
          MB_YESNO or MB_DEFBUTTON1
        );
      { 기본 선택(YES, 즉 Enter 키)은 "보존"이다 — 데이터 손실 방향이
        아니라 안전한 방향이 기본값이 되도록 MB_DEFBUTTON1을 "예/보존"
        에 배치했다. }
      if DeleteData = IDNO then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
#endif
  end;
end;
