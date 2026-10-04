@echo off
rem Opens the Ultimate Multiplayerator launcher (server list, connect). Needs Python 3 with tkinter.
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0launcher\UMLauncher.pyw" & goto :eof)
where pythonw >nul 2>nul && (start "" pythonw "%~dp0launcher\UMLauncher.pyw" & goto :eof)
echo Python 3 was not found. Install it from python.org (tick "Add python.exe to PATH") and start this again.
pause
