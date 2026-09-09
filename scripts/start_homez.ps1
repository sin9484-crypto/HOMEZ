<#
=========================================================
Homez OS

File : scripts/start_homez.ps1

HOMEZ 로컬 런처 (V1, 임시 자산)

기존 homez.db, Migration, 애플리케이션 코드는 건드리지 않는다.
venv\Scripts\python.exe -m uvicorn app.main:app 로 로컬 서버를 띄우고
/console을 연다.

보완 사항(2026-07-28):
- 이 파일은 UTF-8 BOM으로 저장한다(PowerShell 5.1이 BOM 없는 UTF-8
  소스를 시스템 코드페이지로 잘못 읽어 한글이 깨지는 문제 수정).
- 포트가 열려 있어도 /health 응답 필드(success=true, status="healthy")를
  직접 확인해야만 HOMEZ로 인정한다. 다른 프로그램이면 새로 띄우지 않고
  중단한다.
- 서버 준비 실패 시 브라우저를 열지 않고 비정상 종료(exit 1)한다.
- storage\logs\homez-launcher.log 에 시작 시각·포트·성공/실패 사유만
  기록한다(비밀정보 없음).
- 성공 시 /docs가 아니라 /console을 연다.
=========================================================
#>

param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # 콘솔이 인코딩 변경을 지원하지 않아도 계속 진행한다.
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $ProjectRoot "storage\logs"
$LogFile = Join-Path $LogDir "homez-launcher.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] $Message"
    Write-Output $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Test-PortOpen {
    param([string]$TargetHost, [int]$TargetPort)

    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $result = $client.BeginConnect($TargetHost, $TargetPort, $null, $null)
        $success = $result.AsyncWaitHandle.WaitOne(500)
        if ($success -and $client.Connected) {
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Test-HomezHealth {
    param([string]$TargetHost, [int]$TargetPort)

    $url = "http://$TargetHost`:$TargetPort/health"

    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -ne 200) {
            return $false
        }
        $json = $resp.Content | ConvertFrom-Json
        return ($json.success -eq $true -and $json.status -eq "healthy")
    } catch {
        return $false
    }
}

function Exit-WithFailure {
    param([string]$Reason)

    Write-Log "오류: $Reason"
    Write-Log "===== HOMEZ Launcher 실패 ====="
    Write-Output ""

    try {
        # 더블클릭으로 실행된 대화형 콘솔에서는 창이 바로 닫히지 않도록
        # 사용자 입력을 기다린다. 비대화형(테스트/자동화) 환경에서는
        # Read-Host가 실패할 수 있으므로 그 실패 자체가 종료 코드를
        # 가리지 않게 무시한다.
        Read-Host "종료하려면 Enter를 누르세요"
    } catch {
    }

    exit 1
}

Write-Log "===== HOMEZ Launcher 시작 ====="
Write-Log "프로젝트 루트: $ProjectRoot"
Write-Log "대상 주소: $BindHost`:$Port"

$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Exit-WithFailure "venv\Scripts\python.exe 를 찾을 수 없습니다 ($PythonExe). 가상환경을 먼저 생성하세요."
}

Write-Log "Python 확인됨: $PythonExe"

$serverReady = $false
$portOpen = Test-PortOpen -TargetHost $BindHost -TargetPort $Port

if ($portOpen) {

    Write-Log "포트 $Port 가 이미 사용 중입니다 — /health로 HOMEZ 여부를 확인합니다."

    if (Test-HomezHealth -TargetHost $BindHost -TargetPort $Port) {
        Write-Log "이미 실행 중인 HOMEZ 서버를 확인했습니다. 새로 시작하지 않습니다."
        $serverReady = $true
    } else {
        Exit-WithFailure "포트 $Port 를 다른 프로그램이 사용 중입니다(HOMEZ 응답이 아님). 서버를 시작하지 않습니다."
    }

} else {

    Write-Log "HOMEZ 서버를 시작합니다: uvicorn app.main:app --host $BindHost --port $Port"

    $uvicornArgs = @(
        "-m", "uvicorn",
        "app.main:app",
        "--host", $BindHost,
        "--port", $Port
    )

    Start-Process -FilePath $PythonExe `
        -ArgumentList $uvicornArgs `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Normal

    Write-Log "서버 준비를 기다리는 중..."

    $maxWaitSeconds = 30
    $waited = 0.0

    while ($waited -lt $maxWaitSeconds) {
        Start-Sleep -Milliseconds 500
        $waited += 0.5

        if (Test-HomezHealth -TargetHost $BindHost -TargetPort $Port) {
            $serverReady = $true
            break
        }
    }

    if ($serverReady) {
        Write-Log "서버 준비 완료 (대기 시간: ${waited}초)"
    } else {
        Exit-WithFailure "$maxWaitSeconds 초 동안 HOMEZ 서버가 준비되지 않았습니다. 서버 콘솔 창의 오류 메시지를 확인하세요."
    }
}

$url = "http://$BindHost`:$Port/console"
Write-Log "브라우저를 엽니다: $url"
Start-Process $url

Write-Log "===== HOMEZ Launcher 완료 ====="
