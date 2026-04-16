#!/bin/bash
# ── emulator-manager installer ──────────────────────────────────────
#
# Installs the unified touch shim + tray app for emulators.
#
# Usage:
#   ./install.sh              # install
#   ./install.sh --uninstall  # remove

set -euo pipefail

APP_NAME="emulator-manager"
INSTALL_DIR="/opt/$APP_NAME"
WRAPPER_LINK="/usr/local/bin/emu-wrapper"
CONFIG_DIR="$HOME/.config/$APP_NAME"
ICON_DIR="/usr/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="/usr/share/applications"
AUTOSTART_DIR="/etc/xdg/autostart"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Uninstall ──

if [[ "${1:-}" == "--uninstall" ]]; then
    echo "Removing $APP_NAME..."
    pkill -f "emulator-manager-tray" 2>/dev/null || true
    pkill -f "idle-bridge.py" 2>/dev/null || true
    pkill -f "touch_shim.py" 2>/dev/null || true
    sudo rm -rf "$INSTALL_DIR"
    sudo rm -f "$WRAPPER_LINK"
    sudo rm -f "$ICON_DIR/emulator-manager.svg"
    sudo rm -f "$DESKTOP_DIR/$APP_NAME.desktop"
    sudo rm -f "$AUTOSTART_DIR/$APP_NAME.desktop"
    sudo rm -f /etc/sudoers.d/emulator-manager
    rm -f /tmp/emulator-manager-idle.pid
    sudo gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true
    echo "Preserved: $CONFIG_DIR (user config)"
    echo "Uninstall complete."
    exit 0
fi

# ── Install dependencies ──

echo "Checking dependencies..."
NEEDED=""
dpkg -s python3-evdev &>/dev/null || NEEDED="$NEEDED python3-evdev"
dpkg -s python3-gi &>/dev/null || NEEDED="$NEEDED python3-gi"
dpkg -s gir1.2-ayatanaappindicator3-0.1 &>/dev/null || NEEDED="$NEEDED gir1.2-ayatanaappindicator3-0.1"
command -v xdotool &>/dev/null || NEEDED="$NEEDED xdotool"

if [[ -n "$NEEDED" ]]; then
    echo "Installing:$NEEDED"
    sudo apt-get update -qq
    sudo apt-get install -y $NEEDED
fi

# Ensure user is in input group
if ! groups | grep -qw input; then
    echo "Adding $(whoami) to input group..."
    sudo usermod -aG input "$(whoami)"
    echo "  Note: log out and back in for group change to take effect"
fi

# ── Ensure uinput is accessible (needed for idle-bridge) ──

SUDOERS_DROPIN="/etc/sudoers.d/emulator-manager"
echo "Creating sudoers drop-in for uinput access..."
sudo tee "$SUDOERS_DROPIN" >/dev/null << EOF
# emulator-manager: allow idle-bridge to fix /dev/uinput permissions
$(whoami) ALL=(ALL) NOPASSWD: /usr/bin/chmod 660 /dev/uinput
$(whoami) ALL=(ALL) NOPASSWD: /usr/bin/chgrp input /dev/uinput
EOF
sudo chmod 440 "$SUDOERS_DROPIN"

# ── Install application files ──

echo "Installing to $INSTALL_DIR..."
sudo mkdir -p "$INSTALL_DIR"
sudo cp "$SCRIPT_DIR/touch_shim.py"      "$INSTALL_DIR/"
sudo cp "$SCRIPT_DIR/idle-bridge.py"     "$INSTALL_DIR/"
sudo cp "$SCRIPT_DIR/emulator-manager-tray.py" "$INSTALL_DIR/"
sudo cp "$SCRIPT_DIR/emu_wrapper.sh"     "$INSTALL_DIR/"
sudo cp "$SCRIPT_DIR/VERSION"            "$INSTALL_DIR/"
sudo chmod +x "$INSTALL_DIR/touch_shim.py"
sudo chmod +x "$INSTALL_DIR/idle-bridge.py"
sudo chmod +x "$INSTALL_DIR/emulator-manager-tray.py"
sudo chmod +x "$INSTALL_DIR/emu_wrapper.sh"

# ── Create launcher symlink ──

echo "Creating $WRAPPER_LINK..."
sudo rm -f "$WRAPPER_LINK"
sudo ln -s "$INSTALL_DIR/emu_wrapper.sh" "$WRAPPER_LINK"

# ── Install icon ──

echo "Installing icon..."
sudo mkdir -p "$ICON_DIR"
if [[ -f "$SCRIPT_DIR/emulator-manager.svg" ]]; then
    sudo cp "$SCRIPT_DIR/emulator-manager.svg" "$ICON_DIR/"
else
    echo "  Warning: emulator-manager.svg not found in $SCRIPT_DIR"
fi
sudo gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true

# ── Desktop entry (app menu) ──

echo "Creating desktop entry..."
sudo tee "$DESKTOP_DIR/$APP_NAME.desktop" >/dev/null <<EOF
[Desktop Entry]
Name=Emulator Manager
Comment=Touch shim and launcher for Mac emulators
Exec=/usr/bin/python3 $INSTALL_DIR/emulator-manager-tray.py --show-tray
Icon=$APP_NAME
Type=Application
Categories=Settings;
Actions=Uninstall;

[Desktop Action Uninstall]
Name=Uninstall Emulator Manager
Exec=sh -c "sudo $INSTALL_DIR/install.sh --uninstall"
EOF

# ── Autostart ──

echo "Creating autostart entry..."
sudo tee "$AUTOSTART_DIR/$APP_NAME.desktop" >/dev/null <<EOF
[Desktop Entry]
Name=Emulator Manager
Exec=bash -c "/usr/bin/python3 $INSTALL_DIR/idle-bridge.py & sleep 3 && /usr/bin/python3 $INSTALL_DIR/emulator-manager-tray.py"
Type=Application
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF

# ── Create user config (if not exists) ──

if [[ ! -f "$CONFIG_DIR/config.ini" ]]; then
    echo "Creating default config at $CONFIG_DIR/config.ini..."
    mkdir -p "$CONFIG_DIR"
    cp "$SCRIPT_DIR/config.ini.default" "$CONFIG_DIR/config.ini"
else
    echo "Config exists at $CONFIG_DIR/config.ini — preserved."
fi

# ── Copy install.sh for uninstall support ──

sudo cp "$SCRIPT_DIR/install.sh" "$INSTALL_DIR/"
sudo chmod +x "$INSTALL_DIR/install.sh"

# ── Summary ──

echo ""
echo "═══ $APP_NAME installed ═══"
echo ""
echo "  Application:  $INSTALL_DIR/"
echo "  Config:       $CONFIG_DIR/config.ini"
echo "  Tray icon:    appears in system tray on next login"
echo "  App menu:     Settings → Emulator Manager"
echo ""
echo "  Start now:    python3 $INSTALL_DIR/emulator-manager-tray.py &"
echo "  Kiosk usage:  emu-wrapper --binary /path/to/emu --window-name Name"
