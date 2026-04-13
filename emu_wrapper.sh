#!/bin/bash
# ── Shared emulator wrapper ─────────────────────────────────────────
#
# Starts the touch shim alongside any emulator.  Handles compositor
# detection, DISPLAY/XAUTHORITY setup, audio fix, and clean shutdown.
#
# Usage:
#   /opt/emulator-manager/emu_wrapper.sh \
#       --binary /home/pi/SheepShaver/SheepShaver.bin \
#       --window-name SheepShaver \
#       [--nogui] [--screen win/1024/768] \
#       [-- extra emulator args]

set -euo pipefail

SHIM="/opt/emulator-manager/touch_shim.py"
SHIM_PID=""
EMU_PID=""
WATCHDOG_PID=""
KEEPALIVE_PID=""
EMU_BINARY=""
WINDOW_NAME=""
NOGUI=false
SCREEN=""
EMU_ARGS=()

# ── Parse arguments ──

while [[ $# -gt 0 ]]; do
    case "$1" in
        --binary)      EMU_BINARY="$2"; shift 2 ;;
        --window-name) WINDOW_NAME="$2"; shift 2 ;;
        --nogui)       NOGUI=true; shift ;;
        --screen)      SCREEN="$2"; shift 2 ;;
        --)            shift; EMU_ARGS=("$@"); break ;;
        *)             EMU_ARGS+=("$1"); shift ;;
    esac
done

if [[ -z "$EMU_BINARY" || -z "$WINDOW_NAME" ]]; then
    echo "Usage: emu_wrapper.sh --binary /path/to/emu --window-name Name [options]"
    exit 1
fi

if [[ ! -x "$EMU_BINARY" ]]; then
    echo "[wrapper] Error: $EMU_BINARY not found or not executable"
    exit 1
fi

# ── Detect compositor and set DISPLAY/XAUTHORITY ──

detect_display() {
    [[ -n "${DISPLAY:-}" && -n "${XAUTHORITY:-}" ]] && return 0

    local compositor_pid=""
    local compositor_name=""

    for name in labwc wayfire sway; do
        compositor_pid=$(pgrep -u "$(id -u)" -x "$name" 2>/dev/null | head -n1 || true)
        if [[ -n "$compositor_pid" ]]; then
            compositor_name="$name"
            break
        fi
    done

    if [[ -n "$compositor_pid" ]]; then
        local env_file="/proc/$compositor_pid/environ"
        if [[ -r "$env_file" ]]; then
            DISPLAY=$(tr '\0' '\n' < "$env_file" | sed -n 's/^DISPLAY=//p' | head -n1)
            XAUTHORITY=$(tr '\0' '\n' < "$env_file" | sed -n 's/^XAUTHORITY=//p' | head -n1)
            WAYLAND_DISPLAY=$(tr '\0' '\n' < "$env_file" | sed -n 's/^WAYLAND_DISPLAY=//p' | head -n1)
            XDG_RUNTIME_DIR=$(tr '\0' '\n' < "$env_file" | sed -n 's/^XDG_RUNTIME_DIR=//p' | head -n1)
            export DISPLAY="${DISPLAY:-:0}"
            export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
            export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}"
            export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
            echo "[wrapper] Detected $compositor_name (pid $compositor_pid)"
        fi
    fi

    export DISPLAY="${DISPLAY:-:0}"
    export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
}

detect_display
echo "[wrapper] DISPLAY=$DISPLAY"

# ── Audio: keepalive stream ──
# Prevents the vc4-hdmi driver from deadlocking when its PCM stream
# tears down and fails to restart. Without this, audio dropout causes
# SheepShaver to freeze entirely. With it, audio may still drop out
# (a SheepShaver ARM64 bug) but the emulator keeps running.

pacat --playback --format=s16le --rate=44100 --channels=2 --volume=0 < /dev/zero &
KEEPALIVE_PID=$!
sleep 0.3
echo "[wrapper] Audio keepalive active (pid=$KEEPALIVE_PID)"

# ── Cleanup on exit ──

cleanup() {
    echo "[wrapper] Cleaning up..."
    if [[ -n "$WATCHDOG_PID" ]]; then
        kill "$WATCHDOG_PID" 2>/dev/null || true
    fi
    if [[ -n "$KEEPALIVE_PID" ]]; then
        kill "$KEEPALIVE_PID" 2>/dev/null || true
    fi
    if [[ -n "$SHIM_PID" ]]; then
        kill "$SHIM_PID" 2>/dev/null || true
        sleep 0.5
        if kill -0 "$SHIM_PID" 2>/dev/null; then
            echo "[wrapper] Shim didn't exit cleanly — sending SIGKILL"
            kill -9 "$SHIM_PID" 2>/dev/null || true
        fi
        wait "$SHIM_PID" 2>/dev/null || true
    fi
    if [[ -n "$EMU_PID" ]]; then
        kill "$EMU_PID" 2>/dev/null || true
        wait "$EMU_PID" 2>/dev/null || true
    fi
    # Close stale DGA fullscreen windows
    xdotool search --name "$WINDOW_NAME" windowclose 2>/dev/null || true
    xset s on +dpms 2>/dev/null || true
    echo "[wrapper] Done."
}
trap cleanup EXIT

# ── Disable screensaver / DPMS while emulator runs ──

xset s off -dpms 2>/dev/null || true

# ── Start the touch shim ──

if [[ -x "$SHIM" ]]; then
    echo "[wrapper] Starting touch shim (window: $WINDOW_NAME)"
    /usr/bin/python3 "$SHIM" --window-name "$WINDOW_NAME" &
    SHIM_PID=$!
    sleep 0.5
else
    echo "[wrapper] Warning: touch shim not found at $SHIM"
fi

# ── Build emulator command ──

EMU_CMD=("$EMU_BINARY")
[[ "$NOGUI" == "true" ]] && EMU_CMD+=(--nogui true)
[[ -n "$SCREEN" ]] && EMU_CMD+=(--screen "$SCREEN")
EMU_CMD+=("${EMU_ARGS[@]}")

# ── Launch the emulator ──

echo "[wrapper] Starting ${EMU_CMD[*]}"
"${EMU_CMD[@]}" &
EMU_PID=$!

# ── Watchdog: kill emulator if shim dies (prevents input lockout) ──

if [[ -n "$SHIM_PID" ]]; then
    (
        while kill -0 "$SHIM_PID" 2>/dev/null; do
            sleep 2
        done
        if kill -0 "$EMU_PID" 2>/dev/null; then
            echo "[wrapper] Shim died — killing emulator to prevent input lockout"
            kill "$EMU_PID" 2>/dev/null
        fi
    ) &
    WATCHDOG_PID=$!
fi

wait "$EMU_PID" || true

echo "[wrapper] Emulator exited."
