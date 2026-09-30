@echo off
rem Builds AvisSerialPrinter.exe (no Python needed on the operator laptop).
rem Run this on the PC that already has Python, in the folder with leibinger_serial_printer.py.

py -m pip install --upgrade pyinstaller pandas openpyxl || goto :error
py -m PyInstaller --onefile --console --name AvisSerialPrinter leibinger_serial_printer.py || goto :error

if not exist "AvisSerialPrinter_Laptop" mkdir "AvisSerialPrinter_Laptop"
copy /Y "dist\AvisSerialPrinter.exe" "AvisSerialPrinter_Laptop\" >nul
copy /Y "printer_settings.txt" "AvisSerialPrinter_Laptop\" >nul

echo.
echo Done. Copy the folder AvisSerialPrinter_Laptop to the operator laptop.
pause
exit /b 0

:error
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
