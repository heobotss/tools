@echo off
cd /d "%~dp0"
title Relay Slides

REM ASCII only. Korean text inside a .bat is read with the PC's code page
REM (949 on a Korean Windows) and turns into garbage -- the staff PC failed
REM exactly that way. Keep this file pure ASCII, CRLF line endings.

REM Korean output needs a UTF-8 console. A Korean Windows defaults to code
REM page 949, where the program's dashes and marks cannot be written.
chcp 65001 > nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

where python > nul 2>&1
if errorlevel 1 (
  echo.
  echo   [ERROR] Python not found. Reinstall Python with "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

if exist "updater.py" python updater.py
for %%F in (*.bat.new) do if /i not "%%~nF"=="%~nx0" move /y "%%F" "%%~nF" >nul
if exist "%~nx0.new" (
  move /y "%~nx0.new" "%~nx0" >nul
  start "" "%~f0"
  exit /b
)
python -m tools.relay_menu
pause
