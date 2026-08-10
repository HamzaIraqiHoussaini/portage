@echo off
REM Windows launcher / setup. Prefer Portage.vbs for no-console start.
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>&1 && (
    py -3.13 -m venv .venv 2>nul || py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv
  ) || (
    python -m venv .venv
  )
)

call .venv\Scripts\activate.bat

REM Stamp-based install: re-run if requirements hash changed or imports missing
set "STAMP=.venv\.deps-stamp"
set "NEED=0"
if not exist "%STAMP%" set NEED=1
python -c "import hashlib,pathlib; p=pathlib.Path(r'requirements.txt').read_bytes()+b'---'+pathlib.Path(r'requirements-desktop.txt').read_bytes(); h=hashlib.sha256(p).hexdigest(); s=pathlib.Path(r'.venv/.deps-stamp'); raise SystemExit(0 if s.is_file() and s.read_text(encoding='utf-8').strip()==h else 1)" 1>nul 2>nul || set NEED=1
python -c "import fastapi,uvicorn,webview" 1>nul 2>nul || set NEED=1

if "%NEED%"=="1" (
  python -m pip install -q --upgrade pip
  python -m pip install -q -r requirements.txt
  python -m pip install -q -r requirements-desktop.txt
  python -c "import hashlib,pathlib; p=pathlib.Path(r'requirements.txt').read_bytes()+b'---'+pathlib.Path(r'requirements-desktop.txt').read_bytes(); pathlib.Path(r'.venv/.deps-stamp').write_text(hashlib.sha256(p).hexdigest(), encoding='utf-8')"
)

if not exist ".env" if exist ".env.example" copy /Y .env.example .env >nul

if /I "%~1"=="--setup-only" (
  endlocal
  exit /b 0
)

if exist ".venv\Scripts\pythonw.exe" (
  start "" .venv\Scripts\pythonw.exe -m app.desktop
) else (
  start "" .venv\Scripts\python.exe -m app.desktop
)
endlocal
