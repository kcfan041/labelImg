@echo off
rem Double-click to start labelImg without a console window.
rem The first start installs PyQt5 / lxml into .venv (needs internet, 1-2 minutes).
rem Problems are shown in a dialog and written to launcher.log (see tools\launcher.py).
rem Keep this file ASCII-only: cmd reads it in the system code page.
setlocal
cd /d "%~dp0"

rem Already set up: run the launcher with the venv's windowless Python
if exist ".venv\Scripts\pythonw.exe" (
    ".venv\Scripts\python.exe" -c "" >nul 2>nul
    if not errorlevel 1 (
        start "" ".venv\Scripts\pythonw.exe" "tools\launcher.py"
        exit /b 0
    )
)

rem First start: "py" / "pyw" is the python.org launcher (pyw = no window)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul
    if not errorlevel 1 (
        start "" pyw -3 "tools\launcher.py"
        exit /b 0
    )
)

rem Otherwise pythonw.exe on PATH. The Microsoft Store alias (in WindowsApps) only opens the
rem Store, so it is skipped.
for /f "delims=" %%P in ('where pythonw 2^>nul') do (
    echo %%P | find /i "WindowsApps" >nul
    if errorlevel 1 (
        "%%~dpPpython.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul
        if not errorlevel 1 (
            start "" "%%P" "tools\launcher.py"
            exit /b 0
        )
    )
)

powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Python 3.8 or newer was not found.' + [Environment]::NewLine + [Environment]::NewLine + 'Install it from https://www.python.org/downloads/ (tick Add python.exe to PATH), then double-click this file again.', 'labelImg') | Out-Null"
exit /b 1
