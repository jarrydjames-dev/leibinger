@echo off
rem Builds Serialine.exe (no Python needed on the client PC).
rem Run this on the PC that already has Python, in the folder with serialine.py.

py -m pip install --upgrade pyinstaller pandas openpyxl || goto :error
py -m PyInstaller --onefile --console --name Serialine serialine.py || goto :error

if not exist "Serialine_Package" mkdir "Serialine_Package"
copy /Y "dist\Serialine.exe" "Serialine_Package\" >nul
copy /Y "printer_settings.txt" "Serialine_Package\" >nul

echo.
echo Done. Copy the folder Serialine_Package to the client PC.
pause
exit /b 0

:error
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
