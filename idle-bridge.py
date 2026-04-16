#!/usr/bin/env python3
"""Hold a virtual input device open for the session. On SIGUSR1,
inject a no-op event so brightness-control resets its idle timer.
Started at login before other autostart apps."""
import os
import signal
import sys
import time

PIDFILE = "/tmp/emulator-manager-idle.pid"
_poke = False


def cleanup(*_):
    try:
        os.unlink(PIDFILE)
    except OSError:
        pass
    sys.exit(0)


def on_poke(*_):
    global _poke
    _poke = True


signal.signal(signal.SIGINT, signal.SIG_DFL)
signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGUSR1, on_poke)

try:
    import subprocess
    from evdev import UInput, ecodes

    # Ensure /dev/uinput is accessible (sudoers drop-in allows this)
    subprocess.run(
        ["sudo", "chmod", "660", "/dev/uinput"],
        capture_output=True, timeout=5,
    )
    subprocess.run(
        ["sudo", "chgrp", "input", "/dev/uinput"],
        capture_output=True, timeout=5,
    )

    dev = UInput(
        {ecodes.EV_REL: [ecodes.REL_MISC]},
        name="emulator-manager-idle",
    )

    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))

    while True:
        time.sleep(0.5)
        if _poke:
            _poke = False
            dev.write(ecodes.EV_REL, ecodes.REL_MISC, 1)
            dev.syn()

except Exception as exc:
    print(f"[idle-bridge] {exc}", file=sys.stderr, flush=True)
    while True:
        time.sleep(3600)
