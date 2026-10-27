@echo off
chcp 949 >nul
cd /d "%~dp0"
title 키워드 순위 추적기

python --version >nul 2>&1
if errorlevel 1 goto NOPYTHON

rem 자동 업데이트 - 새 버전이 있으면 받아서 바꾼다 (인터넷이 안 되면 그냥 넘어감)
if exist "updater.py" python updater.py
for %%F in (*.bat.new) do if /i not "%%~nF"=="실행.bat" move /y "%%F" "%%~nF" >nul
if exist "실행.bat.new" (
  move /y "실행.bat.new" "실행.bat" >nul
  start "" "%~f0"
  exit /b
)

python -c "import playwright, openpyxl" >nul 2>&1
if errorlevel 1 goto NOPARTS

echo ============================================
echo   키워드 순위 추적 시작 (6~8분 걸립니다)
echo   완료 줄이 뜰 때까지 이 창을 닫지 마세요!
echo ============================================
python 순위추적.py
echo.
echo ============================================
echo   위에 [완료] 가 보이면 성공입니다. 창을 닫으셔도 됩니다.
echo   결과는 이 폴더의 키워드순위표.xlsx 입니다.
echo ============================================
pause
exit /b

:NOPYTHON
echo [중단] 파이썬이 없거나 PATH 에 등록되지 않았습니다.
echo        「먼저 읽어주세요」 2번을 보고 파이썬을 설치하세요.
pause
exit /b 1

:NOPARTS
echo [중단] 필요한 부품이 아직 없습니다. 설치.bat 을 먼저 한 번 실행하세요.
pause
exit /b 1
