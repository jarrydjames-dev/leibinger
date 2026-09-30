@echo off
rem Regenerates the branded manual PDFs (..\*.pdf) with Microsoft Edge or Google Chrome.
cd /d "%~dp0"
set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%BROWSER%" set "BROWSER=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%BROWSER%" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%BROWSER%" ( echo Edge or Chrome not found. & pause & exit /b 1 )

"%BROWSER%" --headless=new --disable-gpu --no-pdf-header-footer --allow-file-access-from-files --virtual-time-budget=5000 --print-to-pdf="%~dp0..\UMS_Serialine_User_Manual.pdf" "file:///%~dp0user_manual.html"
"%BROWSER%" --headless=new --disable-gpu --no-pdf-header-footer --allow-file-access-from-files --virtual-time-budget=5000 --print-to-pdf="%~dp0..\UMS_Serialine_Technician_Manual.pdf" "file:///%~dp0technician_manual.html"
echo.
echo Done. The PDFs are in the manuals folder.
pause
