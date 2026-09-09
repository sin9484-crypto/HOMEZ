<#
=========================================================
HOMEZ canonical DB 확인 스크립트 (읽기 전용)

목적
  두 후보 DB 파일(A, B)의 경로/해시/크기/시각/파일 식별자를
  있는 그대로 출력한다. 어느 쪽이 canonical인지는 이 스크립트가
  아니라 사람이 그 값을 보고 판단한다.

왜 사용자가 "직접" 실행해야 하는가
  Claude가 자신의 도구 실행 환경(패키지 가상화가 적용된 별도
  컨테이너)에서 %LOCALAPPDATA% 아래 경로를 읽으면, Windows가 그
  경로를 다른 물리적 파일로 조용히 바꿔치기(redirect)하는 현상이
  이 세션에서 이미 확인됐다. 이 스크립트를 사용자 본인의 평소
  PowerShell 창에서 실행해야만 이 문제를 피할 수 있다 — Claude가
  대신 실행하면 같은 문제가 재발한다.

이 스크립트가 절대 하지 않는 일(안전장치)
  - 관리자 권한을 요구하지 않는다.
  - DB 파일의 내용을 열거나 조회하지 않는다(SQLite 연결 없음) —
    경로·해시·크기·시각·파일 식별자만 본다.
  - 아무 파일도 새로 만들거나, 수정하거나, 복사·이동·삭제하지
    않는다(이 스크립트 자신도 포함).
  - Migration을 실행하지 않는다.
  - HOMEZ.exe를 시작하거나 종료하지 않는다.
  - Credential이나 업무 데이터(주소·연락처·상품 데이터 등)를
    출력하지 않는다 — 오직 파일 시스템 메타데이터만 다룬다.
  - 오류가 나면 그 항목만 "UNKNOWN"으로 표시하고 계속 진행한다
    (파일을 만들거나 수정해서 오류를 피하지 않는다).
  - 두 후보 중 어느 쪽이 canonical인지 스스로 판단하지 않는다.

사용법
  일반(비관리자) PowerShell 창에서:
    powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Daum pc\Homez-OS\tools\HOMEZ_Canonical_DB_Check.ps1"
  출력 전체를 그대로 복사해 알려주면 된다.
=========================================================
#>

[CmdletBinding()]
param(
    # 정상적인 사용(사용자가 그냥 이 파일을 실행)에서는 항상 아래
    # 기본값(실제 두 후보 DB)을 그대로 쓴다. 이 파라미터는 오직
    # 자동화된 안전성 테스트가 실제 DB를 건드리지 않고 "같은 파일/
    # 다른 파일 판별 로직" 자체를 임시 파일로 검증할 수 있게 하기
    # 위한 것이다 — 값을 주지 않으면 지금까지와 동일하게 동작한다.
    [string]$PathA = "C:\Users\Daum pc\AppData\Local\HOMEZ\data\homez.db",
    [string]$PathB = "C:\Users\Daum pc\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\HOMEZ\data\homez.db"
)

Set-StrictMode -Version Latest

# ---------------------------------------------------------
# 두 후보 경로 — 기본값은 고정된 실제 후보 DB 두 개다.
# ---------------------------------------------------------
$CandidateA = $PathA
$CandidateB = $PathB

# ---------------------------------------------------------
# 파일 식별자(volume serial + file index) 조회 — Win32
# GetFileInformationByHandle을 그대로 사용한다. 파일을 GENERIC_READ +
# 모든 공유 모드(FILE_SHARE_READ/WRITE/DELETE)로 열어 메타데이터만
# 질의한다 — 내용을 읽지 않고(ReadFile 호출 없음), HOMEZ.exe가 같은
# 파일을 동시에 쓰고 있어도 잠그지 않는다. 관리자 권한이 필요 없다.
# ---------------------------------------------------------
try {
    Add-Type -Language CSharp -ErrorAction Stop -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class HomezFileIdentityNative
{
    [StructLayout(LayoutKind.Sequential)]
    public struct BY_HANDLE_FILE_INFORMATION
    {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern SafeFileHandle CreateFile(
        string lpFileName, uint dwDesiredAccess, uint dwShareMode,
        IntPtr lpSecurityAttributes, uint dwCreationDisposition,
        uint dwFlagsAndAttributes, IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle hFile, out BY_HANDLE_FILE_INFORMATION lpFileInformation);

    private const uint GENERIC_READ = 0x80000000;
    private const uint FILE_SHARE_READ = 0x1;
    private const uint FILE_SHARE_WRITE = 0x2;
    private const uint FILE_SHARE_DELETE = 0x4;
    private const uint OPEN_EXISTING = 3;
    // 디렉터리가 아닌 일반 파일만 다루므로 BACKUP_SEMANTICS는 쓰지
    // 않는다(관리자 권한 없이 동작해야 하므로 최소 권한만 요청).
    private const uint FILE_ATTRIBUTE_NORMAL = 0x80;

    // 내용을 읽지 않는다 — 핸들을 열어 메타데이터만 질의하고 바로
    // 닫는다. 실패하면 null을 반환할 뿐 예외로 스크립트를 멈추지
    // 않는다(호출자가 UNKNOWN으로 표시).
    public static object GetInfo(string path)
    {
        using (SafeFileHandle handle = CreateFile(
            path, GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            IntPtr.Zero, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, IntPtr.Zero))
        {
            if (handle == null || handle.IsInvalid) return null;
            BY_HANDLE_FILE_INFORMATION info;
            if (!GetFileInformationByHandle(handle, out info)) return null;
            return info;
        }
    }
}
"@
    $script:NativeAvailable = $true
} catch {
    $script:NativeAvailable = $false
    Write-Host "[경고] 네이티브 파일 식별자 조회 모듈 로드 실패 — 해당 항목은 UNKNOWN으로 표시합니다." -ForegroundColor Yellow
}

function Get-Unknown { "UNKNOWN" }

function Get-CandidateReport {
    param([string]$Label, [string]$InputPath)

    $result = [ordered]@{
        Label            = $Label
        InputPath        = $InputPath
        ResolvedPath     = Get-Unknown
        Exists           = $false
        Sha256           = Get-Unknown
        SizeBytes        = Get-Unknown
        CreationTimeUtc  = Get-Unknown
        LastWriteTimeUtc = Get-Unknown
        VolumeSerial     = Get-Unknown
        FileIndex        = Get-Unknown
        LinkType         = Get-Unknown
        LinkTarget       = Get-Unknown
        _FileId          = $null
    }

    # Resolve-Path — 실제로 파일을 열지 않고 경로만 정규화한다.
    # 이 단계에서 이미 입력 경로와 달라진다면(예: 심볼릭 링크·
    # junction을 실제로 따라간 경우) 그 자체가 유의미한 신호다.
    try {
        $resolved = Resolve-Path -LiteralPath $InputPath -ErrorAction Stop
        $result.ResolvedPath = $resolved.Path
    } catch {
        $result.ResolvedPath = "(경로를 확인할 수 없음 — 파일이 없거나 접근 불가)"
    }

    if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) {
        $result.Exists = $false
        return $result
    }
    $result.Exists = $true

    try {
        $item = Get-Item -LiteralPath $InputPath -ErrorAction Stop
        $result.SizeBytes = $item.Length
        $result.CreationTimeUtc = $item.CreationTimeUtc.ToString("o")
        $result.LastWriteTimeUtc = $item.LastWriteTimeUtc.ToString("o")

        # 재구문 분석 지점(symlink/junction/hardlink 표시) 여부 —
        # PowerShell 5.1의 Get-Item은 LinkType/Target을 그대로
        # 제공한다(추가 조회 없음, 읽기 전용).
        if ($item.PSObject.Properties.Match("LinkType").Count -gt 0 -and $item.LinkType) {
            $result.LinkType = $item.LinkType
            if ($item.PSObject.Properties.Match("Target").Count -gt 0 -and $item.Target) {
                $result.LinkTarget = ($item.Target -join "; ")
            }
        } else {
            $result.LinkType = "(일반 파일 — 심볼릭 링크/junction 아님)"
        }
    } catch {
        # 위 항목들은 이미 초기값(UNKNOWN)이 들어있으므로 그대로 둔다.
    }

    try {
        # Windows PowerShell의 Get-FileHash가 일부 하드링크/실행 환경에서
        # 비결정적으로 실패해도 동일한 읽기 전용 계약을 유지한다.
        $stream = [System.IO.File]::OpenRead($InputPath)
        try {
            $sha256 = [System.Security.Cryptography.SHA256]::Create()
            try {
                $hashBytes = $sha256.ComputeHash($stream)
                $result.Sha256 = [System.BitConverter]::ToString($hashBytes).Replace("-", "")
            } finally {
                $sha256.Dispose()
            }
        } finally {
            $stream.Dispose()
        }
    } catch {
        # UNKNOWN 유지.
    }

    if ($script:NativeAvailable) {
        try {
            $info = [HomezFileIdentityNative]::GetInfo($InputPath)
            if ($null -ne $info) {
                $result.VolumeSerial = "0x{0:X8}" -f $info.VolumeSerialNumber
                $fileIndexHigh = [uint64]$info.FileIndexHigh
                $fileIndexLow = [uint64]$info.FileIndexLow
                $fileIndex64 = ($fileIndexHigh -shl 32) -bor $fileIndexLow
                $result.FileIndex = "0x{0:X16}" -f $fileIndex64
                $result._FileId = [PSCustomObject]@{
                    VolumeSerial = $info.VolumeSerialNumber
                    FileIndexHigh = $info.FileIndexHigh
                    FileIndexLow = $info.FileIndexLow
                }
            }
        } catch {
            # UNKNOWN 유지.
        }
    }

    return $result
}

function Write-CandidateReport {
    param($Report)

    Write-Host ""
    Write-Host ("[{0}]" -f $Report.Label) -ForegroundColor Cyan
    Write-Host ("  입력 경로(Input Path)         : {0}" -f $Report.InputPath)
    Write-Host ("  Resolve-Path 결과             : {0}" -f $Report.ResolvedPath)
    Write-Host ("  존재 여부(Exists)             : {0}" -f $Report.Exists)
    if (-not $Report.Exists) {
        Write-Host "  (파일이 없어 나머지 항목은 조회하지 않았습니다.)" -ForegroundColor DarkGray
        return
    }
    Write-Host ("  SHA-256                       : {0}" -f $Report.Sha256)
    Write-Host ("  크기(bytes)                   : {0}" -f $Report.SizeBytes)
    Write-Host ("  CreationTimeUtc               : {0}" -f $Report.CreationTimeUtc)
    Write-Host ("  LastWriteTimeUtc              : {0}" -f $Report.LastWriteTimeUtc)
    Write-Host ("  볼륨 시리얼(Volume Serial)     : {0}" -f $Report.VolumeSerial)
    Write-Host ("  파일 인덱스(File Index)        : {0}" -f $Report.FileIndex)
    Write-Host ("  재구문 분석 지점 유형(LinkType) : {0}" -f $Report.LinkType)
    if ($Report.LinkTarget -and $Report.LinkTarget -ne (Get-Unknown)) {
        Write-Host ("  링크 대상(LinkTarget)          : {0}" -f $Report.LinkTarget)
    }
}

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host " HOMEZ canonical DB 확인 (읽기 전용, 관리자 권한 불필요)" -ForegroundColor Cyan
Write-Host (" 실행 시각: {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan

$reportA = Get-CandidateReport -Label "후보 A (일반 AppData 경로)" -InputPath $CandidateA
$reportB = Get-CandidateReport -Label "후보 B (Claude 패키지 LocalCache 경로)" -InputPath $CandidateB

Write-CandidateReport -Report $reportA
Write-CandidateReport -Report $reportB

Write-Host ""
Write-Host "[두 후보 비교]" -ForegroundColor Cyan

if (-not $reportA.Exists -or -not $reportB.Exists) {
    Write-Host "  최소 한쪽 파일이 없어 비교할 수 없습니다."
} else {
    # SHA-256 비교 — 둘 다 UNKNOWN이 아닐 때만 의미 있는 비교.
    if ($reportA.Sha256 -ne (Get-Unknown) -and $reportB.Sha256 -ne (Get-Unknown)) {
        $sameHash = ($reportA.Sha256 -eq $reportB.Sha256)
        Write-Host ("  SHA-256 동일 여부              : {0}" -f $sameHash)
    } else {
        Write-Host "  SHA-256 동일 여부              : UNKNOWN(해시 조회 실패)"
    }

    # 파일 식별자 비교 — 같은 volume serial + file index면 동일한
    # 물리적 파일(하드링크 관계 포함)이다.
    if ($null -ne $reportA._FileId -and $null -ne $reportB._FileId) {
        $sameFile = (
            $reportA._FileId.VolumeSerial -eq $reportB._FileId.VolumeSerial -and
            $reportA._FileId.FileIndexHigh -eq $reportB._FileId.FileIndexHigh -and
            $reportA._FileId.FileIndexLow -eq $reportB._FileId.FileIndexLow
        )
        Write-Host ("  같은 물리적 파일 여부(SameFile) : {0}" -f $sameFile)
        Write-Host ("  하드링크 관계 여부              : {0}" -f $sameFile)
        if ($sameFile) {
            Write-Host "    → 두 경로가 실제로는 완전히 같은 파일을 가리킵니다." -ForegroundColor Yellow
        } else {
            Write-Host "    → 두 경로는 서로 다른 물리적 파일입니다." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  같은 물리적 파일 여부(SameFile) : UNKNOWN(파일 식별자 조회 실패)"
        Write-Host "  하드링크 관계 여부              : UNKNOWN(파일 식별자 조회 실패)"
    }

    # 경로 재지정 의심 여부 — Resolve-Path 결과가 입력 경로와 다르면
    # (심볼릭 링크/junction을 실제로 통과했다는 뜻) 의심 신호로 본다.
    $aRedir = ($reportA.Exists -and $reportA.ResolvedPath -and
        $reportA.ResolvedPath -ne $reportA.InputPath -and
        $reportA.ResolvedPath -notlike "*경로를 확인할 수 없음*")
    $bRedir = ($reportB.Exists -and $reportB.ResolvedPath -and
        $reportB.ResolvedPath -ne $reportB.InputPath -and
        $reportB.ResolvedPath -notlike "*경로를 확인할 수 없음*")
    Write-Host ("  경로 재지정 의심(후보 A)        : {0}" -f $aRedir)
    Write-Host ("  경로 재지정 의심(후보 B)        : {0}" -f $bRedir)
}

Write-Host ""
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host " 완료 — 위 내용 전체를 그대로 복사해 알려주세요." -ForegroundColor Cyan
Write-Host " 이 스크립트는 어느 경로가 canonical인지 스스로 판단하지" -ForegroundColor Cyan
Write-Host " 않았습니다 — 있는 그대로의 사실만 출력했습니다." -ForegroundColor Cyan
Write-Host " 아무 파일도 만들거나 수정하지 않았습니다." -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan
