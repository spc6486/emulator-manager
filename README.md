# Emulator Manager

A system tray launcher for SheepShaver and BasiliskII on Raspberry Pi touchscreen devices. Launch classic Mac emulators with one tap, configure display settings, and automatically work around the input and audio issues that affect these emulators under Wayland.

## What it does

**Launches emulators from the system tray** — tap the tray icon, pick SheepShaver or BasiliskII, and the emulator starts immediately in no-GUI mode. No setup dialogs, no terminal commands. The tray shows which emulator is running and prevents launching a second one simultaneously.

**Configures display settings** — set resolution and fullscreen/windowed mode per emulator from the Settings window. Initial values are read from each emulator's own prefs file (`~/.config/SheepShaver/prefs`, `~/.config/BasiliskII/prefs`) so what you see matches what's already configured.

**Fixes ghost clicks on touchscreens** — when using a touchscreen with SheepShaver or BasiliskII, tapping at a new location causes an unwanted click at the *previous* touch position. This happens because the emulator receives the pointer jump and button press as a single event, interpreting it as a drag from the old position rather than a tap at the new one. The included touch shim intercepts raw touchscreen events and re-injects them with correct sequencing (move first, then click), eliminating these ghost clicks entirely. The shim only activates when the emulator window is focused — normal desktop touch input is unaffected.

**Prevents audio dropout** — SheepShaver and BasiliskII audio commonly stops after extended use due to PipeWire's stream management pausing underrunning clients. Enabling "Prevent audio dropout" in Settings writes drop-in configs for WirePlumber and PipeWire-Pulse that disable node suspension, increase buffer sizes, and route emulator audio through SDL's native PipeWire backend, bypassing the failure-prone pipewire-pulse translation layer.

**Protects against input lockout** — if the touch shim crashes while the emulator is running fullscreen, all input would be lost. A watchdog monitors the shim and automatically kills the emulator within two seconds if the shim dies, preventing a situation that would otherwise require SSH or a reboot to recover from.

## Install

```bash
git clone https://github.com/spc6486/emulator-manager.git
cd emulator-manager
chmod +x install.sh
./install.sh
```

The installer handles dependencies, installs to `/opt/emulator-manager/`, and creates autostart and application menu entries. The tray icon appears on next login, or start it now:

```bash
python3 /opt/emulator-manager/emulator-manager-tray.py &
```

## Uninstall

```bash
sudo /opt/emulator-manager/install.sh --uninstall
```

## Settings

Open from the tray icon or from the application menu under Preferences.

### Emulators tab

Per emulator: binary path (auto-discovered on first run), Open folder button to access the emulator's directory for ROM and prefs editing, Copy launch command for pasting into kiosk-manager or scripts, fullscreen toggle, and resolution dropdown.

### Touch tab

Adjustable behavior for the touch shim: tap accuracy delay, hold-to-double-click timing, finger wobble allowance, and focus check responsiveness. Also displays the current calibration matrix from [display-calibrator](https://github.com/spc6486/display-calibrator) if installed.

### Audio tab

Single toggle to enable audio dropout prevention. When enabled, the emulator wrapper creates WirePlumber and PipeWire-Pulse drop-in configs that only affect emulator processes.

## CLI usage

Launch emulators directly without the tray app:

```bash
emu-wrapper --binary /path/to/SheepShaver.bin --window-name SheepShaver --nogui --screen win/1024/768
emu-wrapper --binary /path/to/BasiliskII.bin --window-name "Basilisk II" --nogui --audio-fix
```

## Configuration

Settings are stored at `~/.config/emulator-manager/config.ini`. Changes made in the Settings window take effect on Apply. Touch shim settings can also be reloaded without restarting: `killall -HUP touch_shim.py`.

## Files

| Path | Purpose |
|------|---------|
| `/opt/emulator-manager/` | Application files |
| `/usr/local/bin/emu-wrapper` | Launcher symlink |
| `~/.config/emulator-manager/config.ini` | User settings |
| `/etc/xdg/autostart/emulator-manager.desktop` | Tray autostart |
| `/usr/share/applications/emulator-manager.desktop` | App menu entry |

## Requirements

- Raspberry Pi with a USB touchscreen
- Wayland compositor (labwc, Wayfire, or Sway)
- SheepShaver and/or BasiliskII
- Python 3, python3-gi, python3-evdev, xdotool

## License

MIT
