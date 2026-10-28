@echo off
chcp 949 > nul
setlocal enabledelayedexpansion
set PYTHONIOENCODING=cp949:replace
set PYTHONUTF8=0
cd /d "%~dp0"
title 인스타 팔로우 관리

python --version >nul 2>&1
if errorlevel 1 goto NOPYTHON

rem 자동 업데이트 - 새 버전이 있으면 받아서 바꾼다 (인터넷이 안 되면 그냥 넘어감)
if exist "updater.py" python updater.py
for %%F in (*.bat.new) do if /i not "%%~nF"=="시작.bat" move /y "%%F" "%%~nF" >nul
if exist "시작.bat.new" (
  move /y "시작.bat.new" "시작.bat" >nul
  start "" "%~f0"
  exit /b
)

rem 설치.bat 을 건너뛰고 이 파일부터 누르는 분이 실제로 있다.
rem 부품이 없으면 여기서 알아서 설치해 준다.
python -c "import playwright" >nul 2>&1
if errorlevel 1 goto SETUP

:MENU
cls
echo ============================================================
echo                 인스타 팔로우 관리
echo ============================================================
echo.
echo   [ 처음 쓸 때 순서 : 1 - 2 - 3 - 4 ]
echo.
echo    1.  인스타 로그인          (처음 1회)
echo    2.  내 데이터 불러오기      (내보내기 ZIP 넣고)
echo    3.  언팔 - 판단만 보기      (실제로 안 누름)
echo    4.  언팔 시작              ★ 매일 이것만 누르면 됩니다
echo.
echo   -- 팔로우 --
echo    5.  팔로우 시작            ★ 후보도 알아서 모읍니다
echo    6.  팔로우 - 판단만 보기
echo    7.  후보만 미리 모으기      (안 해도 됩니다 - 비슷한 계정)
echo.
echo   -- 그 외 --
echo    8.  진행 현황 보기
echo    9.  2단계 언팔 (보류한 트레이너 정리)
echo.
echo   -- 원주 잠재고객 (새로 생김) --
echo   10.  원주 고객 팔로우      ★ 하루 한 번 누르면 됩니다
echo   11.  원주 고객 - 판단만 보기
echo   12.  원주 고객 현황
echo.
echo    0.  종료
echo.
echo ============================================================
set "sel="
set /p "sel=번호를 입력하고 Enter: "

if "!sel!"=="1" goto LOGIN
if "!sel!"=="2" goto BUILD
if "!sel!"=="3" goto UNDRY
if "!sel!"=="4" goto UNRUN
if "!sel!"=="5" goto FRUN
if "!sel!"=="6" goto FDRY
if "!sel!"=="7" goto FCOLLECT
if "!sel!"=="8" goto STAT
if "!sel!"=="9" goto PHASE2
if "!sel!"=="10" goto LRUN
if "!sel!"=="11" goto LDRY
if "!sel!"=="12" goto LSTAT
if "!sel!"=="0" goto BYE
goto MENU

:LOGIN
cls
echo [ 인스타 로그인 ]
echo 브라우저가 뜨면 직접 로그인하세요. 확인되면 자동으로 저장됩니다.
echo.
python unfollow_bot.py --login
goto DONE

:BUILD
cls
echo [ 내 데이터 불러오기 ]
echo 인스타 내보내기 ZIP 파일이 이 폴더에 있어야 합니다.
echo.
python check.py
echo.
python queue.py build
goto DONE

:UNDRY
cls
echo [ 언팔 - 판단만 보기 ] 실제로 누르지 않습니다.
echo.
set "n="
set /p "n=몇 명까지 볼까요? (그냥 Enter 누르면 30): "
call :NUMCHECK "!n!" 30
python unfollow_bot.py --dry-run --limit !num!
goto DONE

:UNRUN
cls
echo ============================================================
echo   언팔 시작 - 실제로 팔로우를 취소합니다
echo ============================================================
echo.
echo   설정.txt 의 하루 한도까지 하고 자동으로 멈춥니다.
echo   중간에 멈추려면 이 창에서 Ctrl+C 를 누르세요.
echo.
echo   ※ 브라우저 창을 닫지 마세요. 다른 창에서 작업하셔도 됩니다.
echo.
set "ok="
set /p "ok=시작하려면 y 를 누르고 Enter: "
if /i not "!ok!"=="y" goto MENU
echo.
python unfollow_bot.py
goto DONE

:FRUN
cls
echo ============================================================
echo   팔로우 시작 - 실제로 팔로우합니다
echo ============================================================
echo.
echo   후보가 모자라면 트레이너의 「비슷한 계정」에서 알아서 모은 뒤 이어서 팔로우합니다.
echo   트레이너 계정만 팔로우하고, 아닌 계정은 건너뜁니다.
echo.
echo   ※ 언팔과 같은 날 많이 돌리지 마세요.
echo      인스타는 팔로우와 언팔을 같은 행동으로 봅니다.
echo.
set "ok="
set /p "ok=시작하려면 y 를 누르고 Enter: "
if /i not "!ok!"=="y" goto MENU
echo.
python follow_bot.py
goto DONE

:FDRY
cls
echo [ 팔로우 - 판단만 보기 ] 실제로 누르지 않습니다.
echo.
set "n="
set /p "n=몇 명까지 볼까요? (그냥 Enter 누르면 20): "
call :NUMCHECK "!n!" 20
python follow_bot.py --dry-run --limit !num!
goto DONE

:FCOLLECT
cls
echo [ 후보만 미리 모으기 ] 트레이너의 「비슷한 계정」에서 후보를 모으기만 합니다.
echo 팔로우는 하지 않습니다. 5번이 알아서 모으므로 굳이 안 해도 됩니다.
echo.
python follow_bot.py --collect
goto DONE

:STAT
cls
echo [ 언팔 현황 ]
python queue.py stat
echo.
echo [ 팔로우 현황 ]
python follow_bot.py --stat
echo.
echo [ 원주 고객 현황 ]
python local_follow.py --stat
goto DONE

:PHASE2
cls
echo ============================================================
echo   2단계 - 보류해 둔 트레이너 계정 정리
echo ============================================================
echo.
echo   보류해 둔 트레이너 계정을 실제로 언팔합니다.
echo   1단계를 다 끝낸 뒤에 쓰는 기능입니다.
echo.
echo   먼저 무엇이 지워질지 보고 오세요.
echo.
echo   실행하려면 아래에 정확히  2단계실행  이라고 입력하세요.
echo   (그냥 Enter 를 누르면 메뉴로 돌아갑니다)
echo.
set "ok="
set /p "ok=입력: "
if not "!ok!"=="2단계실행" goto MENU
echo.
python unfollow_bot.py --phase 2 --confirm-phase2
goto DONE

:LRUN
cls
echo ============================================================
echo   원주 잠재고객 팔로우 - 오늘 몫
echo ============================================================
echo.
echo   원주 가게 계정의 최근 팔로워 중 진짜 지역 사람만 팔로우합니다.
echo   3주 지나도 맞팔 안 한 사람은 조금씩 팔로우를 취소합니다.
echo   인스타가 막으면 그 자리에서 멈추고 알려 드립니다.
echo.
echo   ※ 언팔·팔로우(4번·5번)와 동시에 돌리지 마세요.
echo.
set "ok="
set /p "ok=시작하려면 y 를 누르고 Enter: "
if /i not "!ok!"=="y" goto MENU
echo.
python local_follow.py
goto DONE

:LDRY
cls
echo [ 원주 고객 - 판단만 보기 ] 실제로 누르지 않습니다.
echo.
set "n="
set /p "n=몇 명까지 볼까요? (그냥 Enter 누르면 10): "
call :NUMCHECK "!n!" 10
python local_follow.py --dry-run --limit !num!
goto DONE

:LSTAT
cls
python local_follow.py --stat
goto DONE

:NUMCHECK
rem 입력값에서 숫자를 다 지워보고 남는 게 있으면 기본값을 쓴다.
rem 명령어가 섞여 들어오는 걸 막는 장치다. echo 를 쓰지 않는 것이 핵심.
set "num=%~2"
set "raw=%~1"
if not defined raw exit /b 0
set "t=!raw!"
for %%D in (0 1 2 3 4 5 6 7 8 9) do if defined t set "t=!t:%%D=!"
if defined t exit /b 0
if "!raw!"=="0" exit /b 0
set "num=!raw!"
exit /b 0

:DONE
echo.
echo ------------------------------------------------------------
pause
goto MENU

:BYE
endlocal
exit /b 0

:SETUP
cls
echo ============================================================
echo   처음 실행이시네요 - 자동으로 설치부터 합니다
echo ============================================================
echo.
echo   프로그램에 필요한 부품(playwright)이 아직 없습니다.
echo   지금 자동으로 설치합니다.
echo.
echo   * 인터넷이 연결돼 있어야 합니다
echo   * 5~10분 정도 걸립니다. 창을 닫지 말고 기다려 주세요
echo.
pause
echo.
echo [1/2] 부품 설치 중... (글자가 지나가는 게 정상입니다)
python -m pip install --quiet --upgrade pip
python -m pip install playwright
python -c "import playwright" >nul 2>&1
if errorlevel 1 goto SETUPFAIL
echo       완료
echo.
echo [2/2] 브라우저 준비 중... (처음이면 몇 분 걸립니다)
python -m playwright install chromium
echo       완료
echo.
echo ============================================================
echo   설치가 끝났습니다. 이제 메뉴로 이동합니다.
echo ============================================================
echo.
pause
goto MENU

:SETUPFAIL
echo.
echo ============================================================
echo   [!] 설치에 실패했습니다
echo ============================================================
echo.
echo   1) 인터넷 연결을 확인해 주세요.
echo   2) 이 창을 닫고 '설치.bat' 을 실행해 보세요.
echo   3) 그래도 안 되면 이 화면을 사진 찍어 보내 주세요.
echo.
pause
exit /b 1

:NOPYTHON
cls
echo.
echo [!] 파이썬이 없습니다. 먼저 '설치.bat' 을 실행해 주세요.
echo.
pause
exit /b 1
