<#
=========================================================
Homez OS

File : scripts/create_homez_shortcut.ps1

바탕화면에 HOMEZ 실행 아이콘(.lnk)을 생성한다.

- 대상(Target): wscript.exe로 scripts/start_homez_desktop.vbs를 실행한다
  (2026-07-30부터 — 이전에는 scripts/start_homez_desktop.cmd를 직접
  가리켰으나, .cmd 실행은 cmd.exe가 콘솔 창을 반드시 띄우는 문제가
  있었다). VBS는 콘솔이 없는 venv\Scripts\pythonw.exe로 앱을 띄워
  검은 창이 전혀 보이지 않는다. wscript.exe 경로는 %WINDIR%\System32\
  wscript.exe로 동적 계산한다(드라이브 문자를 하드코딩하지 않음).
  개발/디버그용 콘솔 실행(scripts/start_homez_desktop.cmd)과 브라우저
  기반 런처(scripts/start_homez.cmd)는 fallback으로 계속 존재하지만
  이 바로가기 대상은 아니다.
- 아이콘: assets/homez-app.ico
- 기존 바탕화면 파일을 덮어써야 하면 먼저 기존 TargetPath/Arguments를
  기록하고, -Force 없이는 중단한다. -Force로 덮어쓸 때도 기존 대상이
  HOMEZ 관련(경로에 "Homez-OS" 또는 "start_homez" 포함)이 아니면
  경고만 출력하고 계속한다(사용자가 -Force로 명시적으로 교체를
  요청했다는 전제).

이 스크립트는 바탕화면에 새 바로가기 1개만 만든다. 다른 시스템 설정,
레지스트리, 시작프로그램 등록은 하지 않는다.

이 파일은 UTF-8 BOM으로 저장한다(PowerShell 5.1 한글 파싱 문제 방지).
=========================================================
#>

param(
    [string]$ShortcutName = "HOMEZ",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

$VbsPath = Join-Path $ProjectRoot "scripts\start_homez_desktop.vbs"
$WscriptPath = Join-Path $env:WINDIR "System32\wscript.exe"
$IconPath = Join-Path $ProjectRoot "assets\homez-app.ico"

if (-not (Test-Path $VbsPath)) {
    throw "실행 대상을 찾을 수 없습니다: $VbsPath"
}

if (-not (Test-Path $WscriptPath)) {
    throw "wscript.exe를 찾을 수 없습니다: $WscriptPath"
}

if (-not (Test-Path $IconPath)) {
    throw "아이콘 파일을 찾을 수 없습니다: $IconPath"
}

$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath ("$ShortcutName.lnk")

$WshShell = New-Object -ComObject WScript.Shell

if (Test-Path $ShortcutPath) {

    $Existing = $WshShell.CreateShortcut($ShortcutPath)
    Write-Output "[HOMEZ Shortcut] 기존 바로가기 발견: $ShortcutPath"
    Write-Output "  기존 TargetPath: $($Existing.TargetPath)"
    Write-Output "  기존 Arguments: $($Existing.Arguments)"

    if (-not $Force) {
        Write-Output "  덮어쓰려면 -Force 옵션으로 다시 실행하세요."
        exit 0
    }

    $looksLikeHomez = (
        $Existing.TargetPath -like "*Homez-OS*" -or
        $Existing.TargetPath -like "*start_homez*" -or
        $Existing.Arguments -like "*Homez-OS*" -or
        $Existing.Arguments -like "*start_homez*"
    )

    if (-not $looksLikeHomez) {
        Write-Output "  [경고] 기존 바로가기가 HOMEZ 관련 경로로 보이지 않습니다 — -Force가 지정되어 그대로 교체합니다."
    }
}

$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $WscriptPath
$Shortcut.Arguments = "`"$VbsPath`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.IconLocation = "$IconPath,0"
$Shortcut.Description = "HOMEZ Desktop (PyWebView 독립 창, 콘솔 숨김)"
$Shortcut.WindowStyle = 1
$Shortcut.Save()

Write-Output "[HOMEZ Shortcut] 생성 완료: $ShortcutPath"
Write-Output "  TargetPath: $WscriptPath"
Write-Output "  Arguments: `"$VbsPath`""
Write-Output "  아이콘: $IconPath"
