# 시안 HTML → PNG (헤드리스 Edge, 외부 네트워크 없음). 사용: powershell -File render.ps1 [이름...]  (이름이 없으면 전부)
param([string[]]$Names)
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$d = $PSScriptRoot
$all = @{
  "00_menu" = @(1580, 990); "01_today" = @(1580, 990); "02_register" = @(1580, 1120);
  "03_order" = @(1580, 990); "04_approvals" = @(1580, 990); "05_mobile" = @(1060, 900)
}
if (-not $Names) { $Names = $all.Keys | Sort-Object }
foreach ($n in $Names) {
  $w = $all[$n][0]; $h = $all[$n][1]
  $null = Start-Process -FilePath $edge -ArgumentList @(
    "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
    "--window-size=$w,$h", "--screenshot=`"$d\$n.png`"", "`"file:///$($d.Replace('\','/'))/$n.html`""
  ) -Wait -NoNewWindow 2>$null
  "{0}: {1} bytes" -f $n, (Get-Item "$d\$n.png").Length
}
