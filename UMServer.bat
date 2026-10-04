@echo off
rem Starts the Ultimate Multiplayerator server on Windows. Options are passed on, for example:
rem   UMServer.bat --port 7777 --password secret --save "C:\path\GTASAsf1.b"
rem On Linux and macOS use UMServer.sh (same options). Needs Python 3.8 or newer.
setlocal
set "SERVER=%~dp0server\um_server.py"
if not exist "%SERVER%" set "SERVER=%~dp0..\server\um_server.py"
if not exist "%SERVER%" (
  echo The server program was not found: "%~dp0server\um_server.py"
  echo Keep UMServer.bat together with its "server" folder.
  pause
  exit /b 1
)
where py >nul 2>nul && (py -3 "%SERVER%" %* & goto done)
where python >nul 2>nul && (python "%SERVER%" %* & goto done)
echo Python 3 was not found. Install it from python.org (tick "Add python.exe to PATH") and start this again.
pause
exit /b 1
:done
if errorlevel 1 (
  echo.
  echo The server stopped with an error (see above^).
  pause
)
