# deploy_to_xiao.ps1
param (
    [string]$Port = "COM12"
)

# Find working Python
$pyCmd = "python"
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    if (Test-Path "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe") {
        $pyCmd = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
    } elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
        $pyCmd = "py"
    }
}

& $pyCmd upload_to_xiao.py $Port
