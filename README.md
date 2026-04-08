# Touch Shim

Fixes ghost-click artifacts when using a touchscreen with SheepShaver
and BasiliskII emulators under Wayland compositors (labwc, Wayfire, Sway).

## The problem

When you lift your finger at position A and touch at position B, the
emulator sees "button still held + pointer jumps to B" and interprets
it as a drag from A to B — clicking at the wrong location.

## How it works

The shim grabs the touchscreen device while the emulator window is
focused, preventing raw touch events from reaching the compositor.
It then re-injects mouse events via xdotool with correct sequencing:
release → move → press. This eliminates the ghost-click artifact.

Calibration is read from the display-calibrator udev rule at
`/etc/udev/rules.d/99-touchscreen-calibration.rules`. No separate
calibration is needed. If display-calibrator is not installed, the
shim uses an identity matrix (no correction).

## Features

- **One-click launch** from system tray — no emulator setup dialog
- **Focus-aware grab** — touchscreen works normally in Pi OS when
  emulator is not focused
- **Long-press double-click** — hold 2 seconds to open files/folders
- **SIGHUP hot-reload** — change settings without restarting emulator
- **Copy launch command** — paste into kiosk-manager or scripts

## Install

```bash
./install.sh
```

## Uninstall

```bash
./install.sh --uninstall
```

## Files

| Path | Purpose |
|------|---------|
| `/opt/touch-shim/` | Application files |
| `/usr/local/bin/emu-wrapper` | Symlink to emu_wrapper.sh |
| `~/.config/touch-shim/config.ini` | User settings |
| `/etc/xdg/autostart/touch-shim.desktop` | Tray app autostart |
| `/usr/share/applications/touch-shim.desktop` | App menu entry |

## CLI usage

Launch an emulator directly without the tray app:

```bash
emu-wrapper --binary /path/to/SheepShaver.bin --window-name SheepShaver
emu-wrapper --binary /path/to/BasiliskII.bin --window-name BasiliskII
```

## Configuration

Edit `~/.config/touch-shim/config.ini` or use Settings from the tray.

Reload without restarting: `killall -HUP touch_shim.py`
