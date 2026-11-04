# deploy_ota.example.ps1 - Wireless WebREPL Deployer for MicroPython ESP32
param (
    [string]$Ip = $(if ($env:XIAO_IP) { $env:XIAO_IP } else { "192.168.1.XXX" }),
    [string]$Password = $env:WEBREPL_PASSWORD
)

if (-not $Password) {
    $Password = Read-Host "Enter WebREPL password" -AsSecureString
    $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
    $Password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
}

$pyCmd = "python"
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pyCmd = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pyCmd = "py"
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Wireless WebREPL Deployer -> $Ip" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

Write-Host "[1/5] Uploading web/index.html..." -ForegroundColor Yellow
& $pyCmd webrepl_cli.py -p $Password micropython/web/index.html "$($Ip):web/index.html"
Start-Sleep -Seconds 1

Write-Host "[2/5] Uploading web/style.css..." -ForegroundColor Yellow
& $pyCmd webrepl_cli.py -p $Password micropython/web/style.css "$($Ip):web/style.css"
Start-Sleep -Seconds 1

Write-Host "[3/5] Uploading web/app.js..." -ForegroundColor Yellow
& $pyCmd webrepl_cli.py -p $Password micropython/web/app.js "$($Ip):web/app.js"
Start-Sleep -Seconds 1

Write-Host "[4/5] Uploading daikin.py engine..." -ForegroundColor Yellow
& $pyCmd webrepl_cli.py -p $Password micropython/daikin.py "$($Ip):daikin.py"
Start-Sleep -Seconds 1

Write-Host "[5/5] Uploading main.py server & parallel poller..." -ForegroundColor Yellow
& $pyCmd webrepl_cli.py -p $Password micropython/main.py "$($Ip):main.py"
Start-Sleep -Seconds 2

Write-Host "[*] Rebooting XIAO board to initialize parallel poller..." -ForegroundColor Magenta
& $pyCmd webrepl_cli.py -p $Password $Ip reboot
Start-Sleep -Seconds 6

Write-Host "`n[+] Wireless deployment complete!" -ForegroundColor Green
Write-Host "    Live Dashboard: http://$Ip" -ForegroundColor Cyan
