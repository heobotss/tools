@echo off
chcp 949 >nul
echo ============================================
echo   최초 1회 설치 (파이썬이 먼저 깔려 있어야 합니다)
echo   파이썬이 없다면 python.org 에서 설치하면서
echo   "Add python.exe to PATH" 체크박스를 꼭 켜세요
echo ============================================
python --version >nul 2>&1
if errorlevel 1 (
    echo [중단] 파이썬이 없거나 PATH에 등록되지 않았습니다.
    echo        python.org 에서 설치 후 이 파일을 다시 실행하세요.
    pause
    exit /b 1
)
echo 1/2 필요한 부품 설치 중...
python -m pip install --quiet playwright openpyxl
echo 2/2 브라우저 엔진 설치 중... (1~2분)
python -m playwright install chromium
echo.
echo 설치 완료! 이제부터는 실행.bat 만 누르면 됩니다.
pause
