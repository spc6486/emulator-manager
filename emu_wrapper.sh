#!/bin/bash
# ── Shared emulator wrapper ─────────────────────────────────────────
#
# Starts the touch shim alongside any emulator.  Handles compositor
# detection, DISPLAY/XAUTHORITY setup, and clean shutdown.
#
# Usage from per-emulator wrappers:
#   /opt/touch-shim/emu_wrapper.sh \
#       --binary /home/pi/SheepShaver/SheepShaver.bin \
#       --window-name SheepShaver \
#       [-- extra emulator args]
#
# Or directly:
#   emu-wrapper --binary /path/to/emulator --window-name MyApp

set -euo pipefail

SHIM="/opt/touch-shim/touch_shim.py"
SHIM_PID=""
EMU_BINARY=""
WINDOW_NAME=""
EMU_ARGS=()

# ── Parse arguments ──

while [[ $# -gt 0 ]]; do
    case "$1" in
        --binary)    EMU_BINARY="$2"; shift 2 ;;
        --window-name) WINDOW_NAME="$2"; shift 2 ;;
        --)          shift; EMU_ARGS=("$@"); break ;;
        *)           EMU_ARGS+=("$1"); shift ;;
    esac
done

if [[ -z "$EMU_BINARY" || -z "$WINDOW_NAME" ]]; then
    echo "Usage: emu_wrapper.sh --binary /path/to/emu --window-name Name [-- args]"
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

# ── Cleanup on exit ──

cleanup() {
    echo "[wrapper] Cleaning up..."
    if [[ -n "$SHIM_PID" ]]; then
        kill "$SHIM_PID" 2>/dev/null || true
        wait "$SHIM_PID" 2>/dev/null || true
    fi
    # Restore DPMS
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

# ── Launch the emulator ──

echo "[wrapper] Starting $EMU_BINARY"
"$EMU_BINARY" --nogui true "${EMU_ARGS[@]}" || true

echo "[wrapper] Emulator exited."
