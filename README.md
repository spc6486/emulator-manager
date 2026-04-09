# Touch Shim

A system tray application and touch input shim for running SheepShaver and BasiliskII classic Mac emulators on Raspberry Pi touchscreen devices under Wayland compositors.

## The problem

When using a touchscreen with SheepShaver or BasiliskII, tapping a new location causes a ghost click at the previous touch position. This happens because the emulator receives a "button down + pointer jump" sequence — interpreting it as a drag from the old position rather than a tap at the new one.

## How it works

The shim grabs the touchscreen device (via evdev) while the emulator window is focused, preventing raw touch events from reaching the compositor. It re-injects mouse events via xdotool with correct sequencing — move to new position first, then press — eliminating the ghost-click artifact.

When the emulator loses focus, the shim releases the touchscreen so normal desktop touch input works without interruption.

### Calibration

Touch coordinate calibration is read automatically from the system's libinput calibration matrix at `/etc/udev/rules.d/99-touchscreen-calibration.rules`. If [display-calibrator](https://github.com/spc6486/display-calibrator) is installed, the shim uses its calibration with no additional setup. Recalibrating in display-calibrator takes effect on the next emulator launch, or immediately via `killall -HUP touch_shim.py`.

If no calibration rule exists, the shim falls back to an identity matrix (no correction).

## Features

- **System tray launcher** — one-click emulator launch, no setup dialog
- **Auto-discovery** — finds SheepShaver and BasiliskII in common install locations
- **Focus-aware grab** — touchscreen works normally when emulator is not focused
- **Long-press double-click** — hold 2 seconds to open files and folders
- **Configurable behavior** — tap delay, hold timing, wobble tolerance
- **SIGHUP hot-reload** — change settings without restarting the emulator
- **Copy launch command** — paste directly into kiosk-manager or shell scripts
- **Calibration integration** — reads display-calibrator's udev rule automatically

## Requirements

- Raspberry Pi with a USB touchscreen
- Wayland compositor (labwc, Wayfire, or Sway)
- SheepShaver and/or BasiliskII installed
- Python 3, python3-gi, python3-evdev, xdotool

## Install

```bash
git clone https://github.com/spc6486/touch-shim.git
cd touch-shim
chmod +x install.sh
./install.sh
```

The installer handles dependencies (`python3-evdev`, `python3-gi`, `gir1.2-ayatanaappindicator3-0.1`, `xdotool`), installs to `/opt/touch-shim/`, creates autostart and menu entries, and sets up a default config.

The tray icon appears on next login, or start it now:

```bash
python3 /opt/touch-shim/touch-shim-tray.py &
```

## Uninstall

```bash
sudo /opt/touch-shim/install.sh --uninstall
```

User configuration at `~/.config/touch-shim/` is preserved.

## Usage

### From the tray

Click the tray icon to see available emulators. Tap one to launch it immediately with the touch shim active. The emulator starts in no-GUI mode (skipping the setup dialog).

The tray shows running state and disables the other emulator while one is active.

### From the command line

Launch any emulator directly without the tray app:

```bash
emu-wrapper --binary /path/to/SheepShaver.bin --window-name SheepShaver
emu-wrapper --binary /path/to/BasiliskII.bin --window-name "Basilisk II"
```

### From kiosk-manager

Use the "Copy launch command" button in Settings to get the full command string, then paste it into kiosk-manager's application path field.

## Configuration

Edit `~/.config/touch-shim/config.ini` or open Settings from the tray icon.

### Emulator paths

The tray app searches these locations on startup and fills in any empty paths:

| Emulator | Search order |
|----------|-------------|
| SheepShaver | `~/SheepShaver/SheepShaver.bin`, `/usr/local/bin/SheepShaver`, `/opt/SheepShaver/SheepShaver.bin` |
| BasiliskII | `~/BasiliskII/BasiliskII.bin`, `/usr/local/bin/BasiliskII`, `/opt/BasiliskII/BasiliskII.bin` |

Manually configured paths are never overridden by auto-discovery.

### Touch behavior

| Setting | Default | Description |
|---------|---------|-------------|
| Tap accuracy delay | 100 ms | Pause after fast finger moves to prevent misclicks |
| Hold to double-click | 2.0 sec | Hold duration to trigger a double-click |
| Finger wobble allowance | 30 px | Maximum drift during hold that still counts |
| Responsiveness | 0.25 sec | How quickly the shim reacts to window focus changes |

Reload settings without restarting: `killall -HUP touch_shim.py`

## Files

| Path | Purpose |
|------|---------|
| `/opt/touch-shim/touch_shim.py` | Touch event shim |
| `/opt/touch-shim/touch-shim-tray.py` | System tray application |
| `/opt/touch-shim/emu_wrapper.sh` | Emulator launcher with compositor detection |
| `/usr/local/bin/emu-wrapper` | Symlink to emu_wrapper.sh |
| `~/.config/touch-shim/config.ini` | User settings |
| `/etc/xdg/autostart/touch-shim.desktop` | Tray autostart on login |
| `/usr/share/applications/touch-shim.desktop` | Application menu entry |

## Known limitations

- The touch shim addresses a ghost-click issue specific to SheepShaver and BasiliskII. Other emulators may not exhibit this problem.
- Mouse injection uses xdotool (X11) via Xwayland. A native Wayland solution (ydotool) could replace this in the future.
- The 180° display rotation compensation is currently hardcoded. Displays without rotation would need the rotation fix removed.

## License

MIT
