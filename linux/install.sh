#!/bin/bash
# Adds "What Got Plugged?" to your desktop's application menu (and optionally the Desktop).
# Run it from wherever you keep the repo; re-run it if you move the folder.
#   ./linux/install.sh            menu entry only
#   ./linux/install.sh --desktop  menu entry + Desktop icon
#   ./linux/install.sh --uninstall
set -e
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ENTRY="$APPS/what-got-plugged.desktop"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

if [ "$1" = "--uninstall" ]; then
  rm -f "$ENTRY" "$DESKTOP_DIR/what-got-plugged.desktop"
  command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
  echo "Removed."; exit 0
fi

chmod +x "$HERE/what-got-plugged.sh" "$HERE/../what_got_plugged.py"
mkdir -p "$APPS"
cat > "$ENTRY" <<DESK
[Desktop Entry]
Type=Application
Name=What Got Plugged?
Comment=Shows which USB device you just plugged in, and names USB audio devices
Exec="$HERE/what-got-plugged.sh"
Icon=$HERE/what-got-plugged.svg
Terminal=false
Categories=Utility;System;
Keywords=USB;device;audio;
StartupNotify=true
DESK
chmod +x "$ENTRY"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
echo "Added to the application menu: $ENTRY"

if [ "$1" = "--desktop" ]; then
  mkdir -p "$DESKTOP_DIR"
  cp "$ENTRY" "$DESKTOP_DIR/"
  chmod +x "$DESKTOP_DIR/what-got-plugged.desktop"
  # GNOME needs the file marked trusted before it will launch it.
  command -v gio >/dev/null && gio set "$DESKTOP_DIR/what-got-plugged.desktop" metadata::trusted true 2>/dev/null || true
  echo "Added Desktop icon: $DESKTOP_DIR/what-got-plugged.desktop"
fi
