@echo off
chcp 949 > nul
set PYTHONIOENCODING=cp949:replace
set PYTHONUTF8=0
cd /d "%~dp0"
title 인스타 팔로우 관리 - 설치

echo ============================================================
echo   인스타 팔로우 관리 프로그램 - 최초 1회 설치
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 goto NOPYTHON

echo [1/3] 파이썬 확인
python --version
echo.

echo [2/3] 필요한 부품 설치 중... (인터넷 필요, 1~3분)
echo       글자가 지나가는 게 정상입니다.
python -m pip install --quiet --upgrade pip
python -m pip install playwright
rem 설치가 정말 됐는지 실제로 불러봐서 확인한다.
rem (pip 이 성공했다고 해도 다른 파이썬에 깔리는 경우가 있다)
python -c "import playwright" >nul 2>&1
if errorlevel 1 goto PIPFAIL
echo       완료
echo.

echo [3/3] 브라우저 준비 중... (처음이면 5분 정도 걸립니다)
python -m playwright install chromium
echo       완료
echo.

echo ============================================================
echo   설치가 끝났습니다.
echo.
echo   이제 '시작.bat' 을 두 번 눌러서 실행하세요.
echo ============================================================
echo.
pause
exit /b 0

:NOPYTHON
echo.
echo [!] 파이썬이 설치되어 있지 않습니다.
echo.
echo     1. https://www.python.org/downloads/ 에서 내려받으세요
echo     2. 설치할 때 맨 아래 "Add python.exe to PATH" 를 꼭 체크하세요
echo     3. 설치가 끝나면 이 창을 닫고 '설치.bat' 을 다시 실행하세요
echo.
pause
exit /b 1

:PIPFAIL
echo.
echo [!] 부품 설치에 실패했습니다.
echo.
echo     1) 인터넷 연결을 확인하고 이 파일을 다시 실행해 주세요.
echo     2) 그래도 안 되면 이 화면을 사진 찍어 보내 주세요.
echo.
pause
exit /b 1
