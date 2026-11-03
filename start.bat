@echo off
setlocal
cd /d "%~dp0"
rem Read this project's isolated environment; bypass its persistent redirector process.
set "LINGSUO_PYHOME="
for /f "tokens=1,* delims==" %%A in (.venv\pyvenv.cfg) do if "%%A"=="home " set "LINGSUO_PYHOME=%%B"
for /f "tokens=*" %%A in ("%LINGSUO_PYHOME%") do set "LINGSUO_PYHOME=%%A"
if not exist "%LINGSUO_PYHOME%\pythonw.exe" goto missing
start "" "%LINGSUO_PYHOME%\pythonw.exe" -I "%~dp0bootstrap.py" %*
exit /b 0
:missing
echo Python environment missing. See README.md to create .venv first.
pause
exit /b 1
