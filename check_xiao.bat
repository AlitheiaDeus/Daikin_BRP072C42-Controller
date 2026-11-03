@echo off
set PORT=COM12
if not "%~1"=="" set PORT=%~1

echo ==================================================
echo  Inspecting XIAO C5 on %PORT%
echo ==================================================

mpremote connect %PORT% fs ls
echo.
mpremote connect %PORT% exec "import os, sys, gc; print('Board:', os.uname().machine if hasattr(os.uname(), 'machine') else os.uname()); print('Free RAM:', gc.mem_free(), 'bytes'); print('Files:', os.listdir())"
