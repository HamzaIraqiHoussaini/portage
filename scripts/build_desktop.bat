@echo off
setlocal
cd /d "%~dp0\.."
if not exist .venv (
  py -3.13 -m venv .venv 2>nul || py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements-desktop.txt
echo Building Portage…
if exist VERSION (set /p PORTAGE_VER=<VERSION) else (set PORTAGE_VER=0.4.0)
echo Version %PORTAGE_VER%
pyinstaller --noconfirm --clean Portage.spec
echo.
echo Done. Output: dist\Portage\Portage.exe
echo Double-click Portage.vbs for a no-build launch from this repo.
endlocal
