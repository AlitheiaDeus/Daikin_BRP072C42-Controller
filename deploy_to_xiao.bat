@echo off
set PORT=COM12
if not "%~1"=="" set PORT=%~1

set PYCMD=python
where python >nul 2>nul
if %errorlevel% neq 0 (
    if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" (
        set PYCMD="%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
    ) else (
        set PYCMD=py
    )
)

%PYCMD% upload_to_xiao.py %PORT%
