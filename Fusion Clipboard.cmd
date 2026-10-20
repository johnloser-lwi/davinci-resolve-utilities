@echo off
rem ---------------------------------------------------------------------------
rem Launch the Fusion Clipboard panel on its own, without Resolve.
rem
rem The script has no Resolve or Fusion dependency - it only moves text on and
rem off the Windows clipboard - so the SAME file serves both: Resolve's Scripts
rem menu runs the deployed copy, and this runs the one in the repo. One source
rem of truth, no second copy to keep in step.
rem
rem Uses pyw / pythonw so no console window tags along. Pin this to the taskbar
rem or Start menu and it behaves like any other app.
rem ---------------------------------------------------------------------------

setlocal
set "SCRIPT=%~dp0Fusion\01_fusion_clipboard.py"

if not exist "%SCRIPT%" (
    echo Could not find the script at:
    echo   %SCRIPT%
    echo Keep this launcher in the repo root, next to the Fusion folder.
    pause
    exit /b 1
)

rem pyw.exe is the Windows Python launcher's no-console variant and picks the
rem newest install on its own, so it is tried first.
where pyw >nul 2>&1 && (
    start "" pyw "%SCRIPT%"
    exit /b 0
)

where pythonw >nul 2>&1 && (
    start "" pythonw "%SCRIPT%"
    exit /b 0
)

echo Neither pyw.exe nor pythonw.exe is on PATH, so this cannot start without
echo a console window. Falling back to python.exe...
where python >nul 2>&1 && (
    start "" python "%SCRIPT%"
    exit /b 0
)

echo No Python found on PATH at all. Install Python, or launch the panel from
echo Resolve's Scripts menu instead.
pause
exit /b 1
