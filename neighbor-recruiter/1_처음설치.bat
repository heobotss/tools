@echo off
cd /d "%~dp0"
call :findpy
if defined PYEXE goto run
powershell -NoProfile -EncodedCommand VwByAGkAdABlAC0ASABvAHMAdAAgACcAJwA7ACAAVwByAGkAdABlAC0ASABvAHMAdAAgACcA9M7o1DDR0MUgAAzTdMdsw3THIADGxbTFHMEgAMDJCK4gACTBWM5p1ciy5LIuACAAMgB+ADUAhL0gAHisvbnIsuSyLgAnADsAIABXAHIAaQB0AGUALQBIAG8AcwB0ACAAJwARyQSs0MUgACTBWM4gAMjVqcYgAD3MdMcgACi3dLogAAwwCMYNMHy5IAAMsuy3IAD8yDjBlMYuACcAOwAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAnACcA
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
call :findpy
if defined PYEXE goto run
powershell -NoProfile -EncodedCommand VwByAGkAdABlAC0ASABvAHMAdAAgACcAJwA7ACAAVwByAGkAdABlAC0ASABvAHMAdAAgACcAkMfZsyAAJMFYzgCsIABIxSAAELS1wsiy5LIuACAAwMkIriAA9MWsuZSyIAAM03THbMMgAEjWmNN0x8DJ0MUcwSAAwckRyCAAJMFYznTVIAD8yDjBlMYuACcAOwAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAnACAAIAAxACkAIAB4sYC3IABEAG8AdwBuAGwAbwBhAGQAIABQAHkAdABoAG8AbgAgAIS8vNIgAHTQrbkgAJIhIAAbvEDHIAAM03zHIADkwonVJwA7ACAAVwByAGkAdABlAC0ASABvAHMAdAAgACcAIAAgADIAKQAgAKvMIABU1nS6IADouSAARMWYtyAADDBBAGQAZAAgAHAAeQB0AGgAbwBuAC4AZQB4AGUAIAB0AG8AIABQAEEAVABIAA0wIAB4zkTHIAAtryAAtMxs0CcAOwAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAnACAAIAAzACkAIABJAG4AcwB0AGEAbABsACAATgBvAHcAIAB00K25IACSISAAXbCYsHS6IAB0xyAAPcxExyAA67LgrCAADDAxAF8AmMxMxyTBWM4NMHy5IADkstzCIABUsxS+dNCtuScAOwAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAnACcA
start "" https://www.python.org/downloads/
goto end
:run
if exist "updater.py" "%PYEXE%" %PYARG% updater.py
for %%F in (*.bat.new) do if /i not "%%~nF"=="%~nx0" move /y "%%F" "%%~nF" >nul
if exist "%~nx0.new" (
  move /y "%~nx0.new" "%~nx0" >nul
  start "" "%~f0"
  exit /b
)
"%PYEXE%" %PYARG% -X utf8 run_auto.py --install
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
