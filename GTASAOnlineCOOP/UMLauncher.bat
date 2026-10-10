@echo off
rem Opens the Ultimate Multiplayerator launcher (server list, connect). Needs Python 3 with tkinter.
setlocal
set "APP=%~dp0launcher\UMLauncher.pyw"
if not exist "%APP%" set "APP=%~dp0..\launcher\UMLauncher.pyw"
if not exist "%APP%" (
  echo The launcher was not found: "%~dp0launcher\UMLauncher.pyw"
  pause
  exit /b 1
)
where pyw >nul 2>nul && (start "" pyw -3 "%APP%" & goto :eof)
where pythonw >nul 2>nul && (start "" pythonw "%APP%" & goto :eof)
echo Python 3 was not found. Install it from python.org (tick "Add python.exe to PATH") and start this again.
pause
