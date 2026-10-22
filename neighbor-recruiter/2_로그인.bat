@echo off
cd /d "%~dp0"
call :findpy
if defined PYEXE goto run
powershell -NoProfile -EncodedCommand VwByAGkAdABlAC0ASABvAHMAdAAgACcAJwA7ACAAVwByAGkAdABlAC0ASABvAHMAdAAgACcARMXBySAAJMFYzgCsIABIxSAAELS1wsiy5LIuACAADDAxAF8AmMxMxyTBWM4NMHy5IAA8ugDIIABUsxS+dNCtuXTVIAD8yDjBlMYuACcAOwAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAnACcA
goto end
:run
if exist "updater.py" "%PYEXE%" %PYARG% updater.py
for %%F in (*.bat.new) do if /i not "%%~nF"=="%~nx0" move /y "%%F" "%%~nF" >nul
if exist "%~nx0.new" (
  move /y "%~nx0.new" "%~nx0" >nul
  start "" "%~f0"
  exit /b
)
"%PYEXE%" %PYARG% -X utf8 run_auto.py --login
:end
echo.
pause
exit /b

:findpy
set "PYEXE="
set "PYARG="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
  set "PYEXE=py"
  set "PYARG=-3"
  exit /b
)
python -c "import sys" >nul 2>nul
if not errorlevel 1 (
  set "PYEXE=python"
  exit /b
)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
exit /b
