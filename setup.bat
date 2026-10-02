@echo off
rem aside-threads setup - safe to run again.
rem NOTE: .bat files must stay ASCII + CRLF (Korean cmd reads UTF-8/LF wrong).
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1

echo [1/5] Python venv (.venv)
set "PYEXE=python"
where py >nul 2>nul && set "PYEXE=py -3"
if not exist ".venv\Scripts\python.exe" (
  %PYEXE% -m venv .venv
  if errorlevel 1 goto :fail
)
".venv\Scripts\python" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo Python 3.11 or newer is required.
  goto :fail
)

echo [2/5] Python packages
".venv\Scripts\python" -m pip install -q --upgrade pip
".venv\Scripts\python" -m pip install -q -r requirements.txt
if errorlevel 1 goto :fail

echo [3/5] Playwright Chromium (card rendering)
".venv\Scripts\python" -m playwright install chromium
if errorlevel 1 goto :fail

echo [4/5] Fonts (Pretendard)
".venv\Scripts\python" -m aside fonts

echo [5/5] Doctor (Codex login, Chrome, fonts)
where codex >nul 2>nul
if errorlevel 1 echo WARNING: codex CLI not found. Install it, then run: codex login
".venv\Scripts\python" -m aside doctor

echo.
echo Setup finished.
echo Next: 1) codex login   (once, your company ChatGPT account)
echo       2) run.bat       (opens the side panel)
pause
exit /b 0

:fail
echo.
echo SETUP FAILED - read the messages above.
pause
exit /b 1
