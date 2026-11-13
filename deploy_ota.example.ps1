# deploy_ota.example.ps1 - Wireless WebREPL Deployer for MicroPython ESP32
# Copy to scripts/deploy_ota.ps1 (git-ignored) and adjust. Run from anywhere.
#
# NEVER add boot.py or daikin_config.json to this list: they hold the board's
# Wi-Fi and static-IP settings, and overwriting them remotely can leave the
# board unreachable (it can then only be recovered over USB).
param (
    [string]$Ip = $(if ($env:XIAO_IP) { $env:XIAO_IP } else { "192.168.1.XXX" }),
    [string]$Password = $env:WEBREPL_PASSWORD
)

if (-not $Password) {
    $Password = Read-Host "Enter WebREPL password" -AsSecureString
    $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
    $Password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
}

# Always run from the repo root (works from the repo root or from scripts/)
$root = if (Test-Path (Join-Path $PSScriptRoot 'webrepl_cli.py')) { $PSScriptRoot } else { Join-Path $PSScriptRoot '..' }
Set-Location (Resolve-Path $root)

$pyCmd = "python"
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pyCmd = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pyCmd = "py"
}

# local path -> remote path
$files = @(
    @("micropython/web/index.html",    "web/index.html"),
    @("micropython/web/index.html.gz", "web/index.html.gz"),
    @("micropython/web/style.css",     "web/style.css"),
    @("micropython/web/style.css.gz",  "web/style.css.gz"),
    @("micropython/web/app.js",        "web/app.js"),
    @("micropython/web/app.js.gz",     "web/app.js.gz"),
    @("micropython/config.py",         "config.py"),
    @("micropython/discover.py",       "discover.py"),
    @("micropython/daikin.py",         "daikin.py"),
    @("micropython/main.py",           "main.py")
)

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Wireless WebREPL Deployer -> $Ip" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$n = 0
foreach ($f in $files) {
    $n++
    Write-Host "[$n/$($files.Count)] Uploading $($f[1])..." -ForegroundColor Yellow
    & $pyCmd webrepl_cli.py -p $Password $f[0] "$($Ip):$($f[1])"
    Start-Sleep -Milliseconds 600
}
Start-Sleep -Seconds 2

Write-Host "[*] Rebooting board to initialize uasyncio architecture..." -ForegroundColor Magenta
& $pyCmd webrepl_cli.py -p $Password $Ip reboot
Start-Sleep -Seconds 6

Write-Host "`n[+] Wireless deployment complete!" -ForegroundColor Green
