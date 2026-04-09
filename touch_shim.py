#!/usr/bin/env python3
"""
Unified touch shim for emulators running under Wayland compositors.

Grabs the touchscreen to prevent ghost-click artifacts in SheepShaver,
BasiliskII, and similar SDL-based emulators. Reads the calibration matrix
from display-calibrator's udev rule — no separate calibration needed.

Usage:
    touch_shim.py --window-name SheepShaver
    touch_shim.py --window-name BasiliskII
"""

import sys
import os
import re
import time
import signal
import subprocess
import select
import configparser
import argparse
import glob
from evdev import InputDevice, ecodes

# ── Defaults (overridden by ~/.config/touch-shim/config.ini) ────────

DEFAULT_DEVICE = ""
DEFAULT_UDEV_RULE = "/etc/udev/rules.d/99-touchscreen-calibration.rules"
DEFAULT_SCREEN_W = 1024
DEFAULT_SCREEN_H = 768
DEFAULT_CLICK_DELAY_MS = 100
DEFAULT_LONG_PRESS_TIME = 2.0
DEFAULT_HOLD_TOLERANCE = 30
DEFAULT_FOCUS_INTERVAL = 0.25

# Identity matrix (no transformation)
IDENTITY_MATRIX = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]


# ── Configuration ───────────────────────────────────────────────────

class Config:
    """Read-only snapshot of configuration values."""

    def __init__(self):
        self.device_path = DEFAULT_DEVICE
        self.udev_rule_path = DEFAULT_UDEV_RULE
        self.screen_w = DEFAULT_SCREEN_W
        self.screen_h = DEFAULT_SCREEN_H
        self.click_delay_ms = DEFAULT_CLICK_DELAY_MS
        self.long_press_time = DEFAULT_LONG_PRESS_TIME
        self.hold_tolerance = DEFAULT_HOLD_TOLERANCE
        self.focus_interval = DEFAULT_FOCUS_INTERVAL
        self.matrix = list(IDENTITY_MATRIX)

    def load(self, path=None):
        """Load from INI file.  Missing file or keys use defaults."""
        if path is None:
            path = os.path.expanduser("~/.config/touch-shim/config.ini")
        cp = configparser.ConfigParser()
        cp.read(path)  # silently ignores missing file

        dev = "device"
        if cp.has_section(dev):
            self.device_path = cp.get(dev, "path", fallback=self.device_path)
            self.udev_rule_path = cp.get(
                dev, "calibration_rule", fallback=self.udev_rule_path
            )

        scr = "screen"
        if cp.has_section(scr):
            self.screen_w = cp.getint(scr, "width", fallback=self.screen_w)
            self.screen_h = cp.getint(scr, "height", fallback=self.screen_h)

        beh = "behavior"
        if cp.has_section(beh):
            self.click_delay_ms = cp.getint(
                beh, "click_delay_ms", fallback=self.click_delay_ms
            )
            self.long_press_time = cp.getfloat(
                beh, "long_press_time", fallback=self.long_press_time
            )
            self.hold_tolerance = cp.getint(
                beh, "hold_tolerance", fallback=self.hold_tolerance
            )
            self.focus_interval = cp.getfloat(
                beh, "focus_check_interval", fallback=self.focus_interval
            )

        # Read calibration matrix from display-calibrator's udev rule
        self.matrix = read_calibration_matrix(self.udev_rule_path)
        return self


# ── Calibration matrix ──────────────────────────────────────────────

def read_calibration_matrix(rule_path):
    """Parse LIBINPUT_CALIBRATION_MATRIX from the udev rule file.

    Returns six floats [a, b, c, d, e, f] or the identity matrix
    if the file is missing or unparseable.
    """
    try:
        with open(rule_path) as fh:
            content = fh.read()
        match = re.search(
            r'LIBINPUT_CALIBRATION_MATRIX[^"]*"([^"]+)"', content
        )
        if match:
            values = [float(v) for v in match.group(1).split()]
            if len(values) >= 6:
                print(
                    f"[shim] Calibration matrix: "
                    f"{' '.join(f'{v:.5f}' for v in values[:6])}",
                    flush=True,
                )
                return values[:6]
    except (FileNotFoundError, ValueError, PermissionError) as exc:
        print(f"[shim] Cannot read calibration rule: {exc}", flush=True)

    print("[shim] Using identity matrix (no calibration)", flush=True)
    return list(IDENTITY_MATRIX)


# ── Coordinate transform ───────────────────────────────────────────

def transform(raw_x, raw_y, abs_x_range, abs_y_range, matrix, scr_w, scr_h):
    """Apply the calibration matrix to raw evdev coordinates.

    This replicates what libinput does internally:
      1. Normalize raw value into 0‑1 using the device's ABS range
      2. Multiply by the 3×2 calibration matrix
      3. Scale to screen pixel coordinates
    """
    # Normalize
    x_span = abs_x_range[1] - abs_x_range[0]
    y_span = abs_y_range[1] - abs_y_range[0]
    norm_x = (raw_x - abs_x_range[0]) / x_span if x_span else 0.0
    norm_y = (raw_y - abs_y_range[0]) / y_span if y_span else 0.0

    # Matrix multiply  [a b c]   [norm_x]
    #                  [d e f] × [norm_y]
    #                             [  1  ]
    cal_x = matrix[0] * norm_x + matrix[1] * norm_y + matrix[2]
    cal_y = matrix[3] * norm_x + matrix[4] * norm_y + matrix[5]

    # Undo 180° rotation (matrix is pre-rotation, xdotool is post-rotation)
    cal_x = 1.0 - cal_x
    cal_y = 1.0 - cal_y

    # Clamp and scale
    cal_x = max(0.0, min(1.0, cal_x))
    cal_y = max(0.0, min(1.0, cal_y))

    return int(cal_x * scr_w), int(cal_y * scr_h)


# ── Focus detection ─────────────────────────────────────────────────

def is_window_focused(window_name):
    """Return True if the active X window title contains window_name."""
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=1,
        )
        return window_name in result.stdout.strip()
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False


# ── Mouse injection helpers ─────────────────────────────────────────

_DEVNULL = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def mouse_move(x, y):
    subprocess.run(["xdotool", "mousemove", str(x), str(y)], **_DEVNULL)


def mouse_down():
    subprocess.run(["xdotool", "mousedown", "1"], **_DEVNULL)


def mouse_up():
    subprocess.run(["xdotool", "mouseup", "1"], **_DEVNULL)


def mouse_double_click():
    subprocess.run(
        ["xdotool", "click", "--repeat", "2", "--delay", "100", "1"],
        **_DEVNULL,
    )


# ── Main loop ───────────────────────────────────────────────────────

def run(cfg, window_name):
    """Main event loop: grab, transform, inject."""

    # Open the touchscreen device — try configured path, then auto-detect
    ts = None
    if cfg.device_path:
        try:
            ts = InputDevice(cfg.device_path)
        except Exception:
            print(
                f"[shim] Configured device not found: {cfg.device_path}",
                flush=True,
            )

    if ts is None:
        # Auto-detect: search by-id for SingWon or any CTP touch panel
        for pattern in ["/dev/input/by-id/*SingWon*event*",
                        "/dev/input/by-id/*CTP*event*"]:
            for path in sorted(glob.glob(pattern)):
                if "-kbd" in path:
                    continue
                try:
                    ts = InputDevice(path)
                    print(f"[shim] Auto-detected: {path}", flush=True)
                    break
                except Exception:
                    continue
            if ts:
                break

    if ts is None:
        # Last resort: scan all event devices for multitouch capability
        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                dev = InputDevice(path)
                caps = dev.capabilities(absinfo=False)
                if ecodes.EV_ABS in caps:
                    if ecodes.ABS_MT_POSITION_X in caps[ecodes.EV_ABS]:
                        ts = dev
                        print(
                            f"[shim] Found touch device: {path} ({dev.name})",
                            flush=True,
                        )
                        break
            except Exception:
                continue

    if ts is None:
        print("[shim] No touchscreen found", file=sys.stderr)
        sys.exit(1)

    # Read ABS ranges from the device itself
    try:
        abs_x = ts.absinfo(ecodes.ABS_X)
        abs_y = ts.absinfo(ecodes.ABS_Y)
        abs_x_range = (abs_x.min, abs_x.max)
        abs_y_range = (abs_y.min, abs_y.max)
    except Exception:
        # Fallback: try multitouch axes
        try:
            abs_x = ts.absinfo(ecodes.ABS_MT_POSITION_X)
            abs_y = ts.absinfo(ecodes.ABS_MT_POSITION_Y)
            abs_x_range = (abs_x.min, abs_x.max)
            abs_y_range = (abs_y.min, abs_y.max)
        except Exception:
            print("[shim] Cannot read ABS range, using 0-1023/0-767", flush=True)
            abs_x_range = (0, 1023)
            abs_y_range = (0, 767)

    print(
        f"[shim] Device: {ts.name}",
        f"\n[shim] ABS X: {abs_x_range[0]}..{abs_x_range[1]}",
        f"  ABS Y: {abs_y_range[0]}..{abs_y_range[1]}",
        f"\n[shim] Screen: {cfg.screen_w}x{cfg.screen_h}",
        f"\n[shim] Window: {window_name}",
        flush=True,
    )

    # ── SIGHUP handler: reload config + matrix ──
    def handle_sighup(signum, frame):
        nonlocal cfg
        print("[shim] SIGHUP — reloading config", flush=True)
        cfg = Config()
        cfg.load()

    signal.signal(signal.SIGHUP, handle_sighup)

    # ── State ──
    grabbed = False
    touch_down = False
    button_pressed = False
    cur_x = 0
    cur_y = 0
    screen_x = 0
    screen_y = 0
    touch_start_time = None
    touch_start_x = None
    touch_start_y = None
    max_movement = 0
    last_focus_check = 0.0

    def do_grab():
        nonlocal grabbed
        if not grabbed:
            try:
                ts.grab()
                grabbed = True
                print("[shim] Grabbed touchscreen", flush=True)
            except OSError:
                pass

    def do_ungrab():
        nonlocal grabbed
        if grabbed:
            try:
                ts.ungrab()
                grabbed = False
                print("[shim] Released touchscreen", flush=True)
            except OSError:
                pass

    print("[shim] Running — waiting for focus", flush=True)

    try:
        while True:
            # ── Focus check ──
            now = time.monotonic()
            if now - last_focus_check >= cfg.focus_interval:
                last_focus_check = now
                focused = is_window_focused(window_name)
                if focused:
                    do_grab()
                elif not touch_down:
                    # Only ungrab if finger isn't currently down
                    do_ungrab()

            # ── Poll for events (non-blocking with timeout) ──
            r, _, _ = select.select([ts.fd], [], [], cfg.focus_interval)
            if not r:
                continue

            for ev in ts.read():
                # Track absolute position (always, for readiness)
                if ev.type == ecodes.EV_ABS:
                    if ev.code in (ecodes.ABS_MT_POSITION_X, ecodes.ABS_X):
                        cur_x = ev.value
                    elif ev.code in (ecodes.ABS_MT_POSITION_Y, ecodes.ABS_Y):
                        cur_y = ev.value

                    # Live pointer tracking during drag (only when grabbed)
                    if grabbed and touch_down and button_pressed:
                        sx, sy = transform(
                            cur_x, cur_y,
                            abs_x_range, abs_y_range,
                            cfg.matrix, cfg.screen_w, cfg.screen_h,
                        )
                        mouse_move(sx, sy)
                        screen_x, screen_y = sx, sy

                        # Track movement for long-press detection
                        if touch_start_x is not None:
                            dx = abs(screen_x - touch_start_x)
                            dy = abs(screen_y - touch_start_y)
                            max_movement = max(max_movement, dx + dy)

                # Handle touch events (only when grabbed)
                elif (
                    grabbed
                    and ev.type == ecodes.EV_KEY
                    and ev.code == ecodes.BTN_TOUCH
                ):
                    if ev.value == 1:  # ── Touch down ──
                        screen_x, screen_y = transform(
                            cur_x, cur_y,
                            abs_x_range, abs_y_range,
                            cfg.matrix, cfg.screen_w, cfg.screen_h,
                        )

                        # Move pointer first, then press
                        mouse_move(screen_x, screen_y)
                        time.sleep(0.02)
                        mouse_down()

                        touch_down = True
                        button_pressed = True
                        touch_start_time = time.monotonic()
                        touch_start_x = screen_x
                        touch_start_y = screen_y
                        max_movement = 0

                    elif ev.value == 0:  # ── Touch up ──
                        if button_pressed:
                            mouse_up()

                            # Long-press detection → double-click
                            if touch_start_time is not None:
                                held = time.monotonic() - touch_start_time
                                if (
                                    held >= cfg.long_press_time
                                    and max_movement <= cfg.hold_tolerance
                                ):
                                    time.sleep(0.05)
                                    mouse_double_click()
                                    print(
                                        f"[shim] Long press "
                                        f"({held:.1f}s) → double-click",
                                        flush=True,
                                    )

                            button_pressed = False
                        touch_down = False
                        touch_start_time = None
                        touch_start_x = None
                        touch_start_y = None
                        max_movement = 0

    except KeyboardInterrupt:
        print("\n[shim] Interrupted", flush=True)
    finally:
        # Always release the device
        do_ungrab()
        # Release any held button
        if button_pressed:
            mouse_up()
        print("[shim] Exiting", flush=True)


# ── Entry point ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Touch shim for emulators under Wayland"
    )
    parser.add_argument(
        "--window-name", required=True,
        help="Emulator window title to match for focus-aware grab",
    )
    parser.add_argument(
        "--config",
        help="Path to config.ini (default: ~/.config/touch-shim/config.ini)",
    )
    args = parser.parse_args()

    cfg = Config()
    cfg.load(args.config)

    run(cfg, args.window_name)


if __name__ == "__main__":
    main()
