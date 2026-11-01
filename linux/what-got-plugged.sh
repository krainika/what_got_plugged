#!/bin/bash
# Launcher for "What Got Plugged?" on Linux: finds a Python 3 with tkinter and runs the app.
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SCRIPT="$HERE/../what_got_plugged.py"
LOG="${XDG_STATE_HOME:-$HOME/.local/state}/what-got-plugged.log"
mkdir -p "$(dirname "$LOG")"

alert() {
  if command -v zenity >/dev/null; then zenity --error --title="What Got Plugged?" --text="$1"
  elif command -v kdialog >/dev/null; then kdialog --title "What Got Plugged?" --error "$1"
  elif command -v notify-send >/dev/null; then notify-send "What Got Plugged?" "$1"
  else echo "$1" >&2; fi
}

PY=""
for c in python3 /usr/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null && "$c" -c "import tkinter" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  alert "Could not find a Python 3 with tkinter. On Debian/Ubuntu/Raspberry Pi OS: sudo apt install python3-tk"
  exit 1
fi

echo "=== $(date)  using $(command -v "$PY")  script $SCRIPT" >> "$LOG"
"$PY" "$SCRIPT" >> "$LOG" 2>&1
rc=$?
[ $rc -ne 0 ] && alert "The app quit with an error (exit code $rc). Details are in $LOG"
exit $rc
