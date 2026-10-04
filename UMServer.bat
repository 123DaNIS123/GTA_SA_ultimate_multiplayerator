@echo off
rem Starts the Ultimate Multiplayerator server on Windows. Options are passed on, for example:
rem   UMServer.bat --port 7777 --password secret --max-players 50
rem On Linux and macOS use UMServer.sh (same options). Needs Python 3.8 or newer.
where py >nul 2>nul && (py -3 "%~dp0server\um_server.py" %* & goto :eof)
where python >nul 2>nul && (python "%~dp0server\um_server.py" %* & goto :eof)
echo Python 3 was not found. Install it from python.org (tick "Add python.exe to PATH") and start this again.
pause
