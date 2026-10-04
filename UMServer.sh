#!/bin/sh
# Starts the Ultimate Multiplayerator server on Linux and macOS. Options are passed on, for example:
#   ./UMServer.sh --port 7777 --password secret --max-players 50
# On a VPS, to keep it running after you log out:  nohup ./UMServer.sh --password secret > server.log 2>&1 &
# Needs Python 3.8 or newer (Ubuntu has it; otherwise: sudo apt install python3).
dir="$(cd "$(dirname "$0")" && pwd)"
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$dir/server/um_server.py" "$@"
fi
echo "python3 was not found. Install it (Ubuntu: sudo apt install python3) and start this again."
exit 1
