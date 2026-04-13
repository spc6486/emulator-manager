#!/usr/bin/env python3
"""
Emulator Manager — tray app for launching SheepShaver and BasiliskII
with touch-fix shim on Raspberry Pi touchscreen devices.
"""

import os
import sys
import signal
import fcntl
import subprocess
import configparser
import re
import glob
import shlex

import gi
gi.require_version("Gtk", "3.0")
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
except (ValueError, ImportError):
    AppIndicator = None

from gi.repository import Gtk, GLib, Gdk

APP_ID = "emulator-manager"
INSTALL_DIR = "/opt/emulator-manager"
WRAPPER_PATH = os.path.join(INSTALL_DIR, "emu_wrapper.sh")
ICON_NAME = "emulator-manager"
CONFIG_DIR = os.path.expanduser("~/.config/emulator-manager")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.ini")
LOCK_PATH = os.path.join(CONFIG_DIR, "tray.lock")
UDEV_RULE_DEFAULT = "/etc/udev/rules.d/99-touchscreen-calibration.rules"
VERSION_FILE = os.path.join(INSTALL_DIR, "VERSION")

EMU_PREFS_PATHS = {
    "sheepshaver": os.path.expanduser("~/.config/SheepShaver/prefs"),
    "basilisk": os.path.expanduser("~/.config/BasiliskII/prefs"),
}

EMULATORS = [
    {"key": "sheepshaver", "name": "SheepShaver",
     "window": "SheepShaver",
     "search": ["~/SheepShaver/SheepShaver.bin",
                 "/usr/local/bin/SheepShaver",
                 "/opt/SheepShaver/SheepShaver.bin"]},
    {"key": "basilisk", "name": "BasiliskII",
     "window": "Basilisk II",
     "search": ["~/BasiliskII/BasiliskII.bin",
                 "/usr/local/bin/BasiliskII",
                 "/opt/BasiliskII/BasiliskII.bin"]},
]

SCREEN_RESOLUTIONS = [
    "640x480", "800x600", "832x624", "1024x768",
    "1152x870", "1280x1024", "1600x1200",
]


def get_version():
    try:
        return open(VERSION_FILE).read().strip()
    except FileNotFoundError:
        return "?"


# ── Read emulator prefs ────────────────────────────────────────────

def read_emu_screen(key):
    """Parse the emulator's own prefs file for screen setting.
    Returns (resolution, fullscreen) or (None, None) if not found."""
    path = EMU_PREFS_PATHS.get(key)
    if not path or not os.path.isfile(path):
        return None, None
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("screen "):
                    parts = line.split()
                    if len(parts) >= 2:
                        segs = parts[1].split("/")
                        if len(segs) == 3:
                            mode = segs[0]
                            res = f"{segs[1]}x{segs[2]}"
                            return res, mode.lower() == "dga"
    except OSError:
        pass
    return None, None


# ── Singleton ───────────────────────────────────────────────────────

def acquire_lock():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    lock_fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"[{APP_ID}] Already running.", file=sys.stderr)
        sys.exit(0)
    return lock_fd


# ── Auto-discover emulators ────────────────────────────────────────

def discover_emulator(emu):
    """Search common paths for an emulator binary. Return path or ''."""
    for pattern in emu["search"]:
        expanded = os.path.expanduser(pattern)
        for path in glob.glob(expanded):
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return ""


# ── Configuration ───────────────────────────────────────────────────

class Config:
    def __init__(self):
        self.cp = configparser.ConfigParser()
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if os.path.exists(CONFIG_PATH):
            self.cp.read(CONFIG_PATH)
        self._ensure_sections()
        self._auto_discover()
        self.save()

    def _ensure_sections(self):
        for section in ("emulators", "behavior", "audio", "ui"):
            if not self.cp.has_section(section):
                self.cp.add_section(section)
        for emu in EMULATORS:
            sect = f"launch.{emu['key']}"
            if not self.cp.has_section(sect):
                self.cp.add_section(sect)
                # Seed from emulator prefs if available
                res, fs = read_emu_screen(emu["key"])
                self.cp.set(sect, "nogui", "true")
                self.cp.set(sect, "screen_res", res or "1024x768")
                self.cp.set(sect, "fullscreen",
                            str(fs).lower() if fs is not None else "false")
        defaults = {
            ("behavior", "click_delay_ms", "100"),
            ("behavior", "long_press_time", "2.0"),
            ("behavior", "hold_tolerance", "30"),
            ("behavior", "focus_check_interval", "0.25"),
            ("audio", "prevent_dropout", "false"),
            ("ui", "show_tray", "true"),
        }
        for section, key, val in defaults:
            if not self.cp.has_option(section, key):
                self.cp.set(section, key, val)

    def _auto_discover(self):
        for emu in EMULATORS:
            key = f"{emu['key']}_path"
            current = self.cp.get("emulators", key, fallback="")
            if not current or not os.path.isfile(current):
                found = discover_emulator(emu)
                if found:
                    self.cp.set("emulators", key, found)
                    print(f"[{APP_ID}] Found {emu['name']}: {found}")
                elif not current:
                    self.cp.set("emulators", key, "")

    def load(self):
        self.cp.read(CONFIG_PATH)

    def save(self):
        with open(CONFIG_PATH, "w") as fh:
            self.cp.write(fh)

    def get(self, section, key, fallback=None):
        return self.cp.get(section, key, fallback=fallback)

    def getfloat(self, section, key, fallback=0.0):
        return self.cp.getfloat(section, key, fallback=fallback)

    def getint(self, section, key, fallback=0):
        return self.cp.getint(section, key, fallback=fallback)

    def getbool(self, section, key, fallback=True):
        return self.cp.getboolean(section, key, fallback=fallback)

    def set(self, section, key, value):
        if not self.cp.has_section(section):
            self.cp.add_section(section)
        self.cp.set(section, key, str(value))

    def emu_path(self, key):
        return self.get("emulators", f"{key}_path", fallback="")

    def set_emu_path(self, key, path):
        self.set("emulators", f"{key}_path", path)

    @property
    def show_tray(self):
        return self.getbool("ui", "show_tray", fallback=True)

    @property
    def prevent_dropout(self):
        return self.getbool("audio", "prevent_dropout", fallback=False)

    def launch_section(self, key):
        return f"launch.{key}"

    def get_screen_string(self, key):
        sect = self.launch_section(key)
        res = self.get(sect, "screen_res", fallback="1024x768")
        fs = self.getbool(sect, "fullscreen", fallback=False)
        prefix = "dga" if fs else "win"
        return f"{prefix}/{res.replace('x', '/')}"


# ── Calibration helper ──────────────────────────────────────────────

def read_calibration_info():
    try:
        content = open(UDEV_RULE_DEFAULT).read()
        match = re.search(
            r'LIBINPUT_CALIBRATION_MATRIX[^"]*"([^"]+)"', content
        )
        if match:
            vals = match.group(1).split()
            short = [f"{float(v):.3f}" for v in vals[:6]]
            row1 = f"{short[0]}  {short[1]}  {short[2]}"
            row2 = f"{short[3]}  {short[4]}  {short[5]}"
            return row1, row2, True
    except (FileNotFoundError, ValueError, PermissionError):
        pass
    return "1.000  0.000  0.000", "0.000  1.000  0.000", False


# ── Build launch command ────────────────────────────────────────────

def build_launch_cmd(config, emu):
    key = emu["key"]
    binary = config.emu_path(key)
    parts = ["emu-wrapper", "--binary", binary, "--window-name", emu["window"]]
    sect = config.launch_section(key)
    if config.getbool(sect, "nogui", fallback=True):
        parts.append("--nogui")
    screen = config.get_screen_string(key)
    if screen:
        parts.extend(["--screen", screen])
    return " ".join(parts)


# ── Emulator process ────────────────────────────────────────────────

class EmulatorProcess:
    def __init__(self):
        self.proc = None
        self.running_key = None

    @property
    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def launch(self, config, emu):
        if self.is_running:
            return False
        key = emu["key"]
        binary = config.emu_path(key)
        cmd = [WRAPPER_PATH, "--binary", binary,
               "--window-name", emu["window"]]
        sect = config.launch_section(key)
        if config.getbool(sect, "nogui", fallback=True):
            cmd.append("--nogui")
        screen = config.get_screen_string(key)
        if screen:
            cmd.extend(["--screen", screen])
        try:
            self.proc = subprocess.Popen(cmd, start_new_session=False)
            self.running_key = key
            return True
        except Exception as exc:
            print(f"[{APP_ID}] Launch failed: {exc}", file=sys.stderr)
            return False

    def check(self):
        if self.proc is None:
            return False
        if self.proc.poll() is not None:
            self.proc = None
            self.running_key = None
            return False
        return True


# ── Settings window (Gtk.Window, matches display-calibrator) ────────

class SettingsWindow:
    def __init__(self, config, on_apply_cb):
        self.config = config
        self.on_apply_cb = on_apply_cb
        self.win = Gtk.Window(
            title="Emulator Manager", default_width=440
        )
        self.win.set_position(Gtk.WindowPosition.CENTER)
        self.win.set_type_hint(Gdk.WindowTypeHint.DIALOG)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        outer.set_margin_top(6)
        outer.set_margin_bottom(6)
        self.win.add(outer)

        header = Gtk.Label()
        header.set_markup(
            f"<big><b>Emulator Manager</b></big>  "
            f"<small>v{get_version()}</small>"
        )
        header.set_xalign(0)
        outer.pack_start(header, False, False, 0)

        notebook = Gtk.Notebook()
        outer.pack_start(notebook, True, True, 4)

        # ═══ Tab 1: Emulators ═══
        emu_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        emu_page.set_margin_start(12)
        emu_page.set_margin_end(12)
        emu_page.set_margin_top(8)
        emu_page.set_margin_bottom(8)

        self.path_entries = {}
        self.nogui_checks = {}
        self.fullscreen_checks = {}
        self.res_combos = {}

        for emu in EMULATORS:
            frame = Gtk.Frame()
            frame.set_shadow_type(Gtk.ShadowType.NONE)
            fbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            fbox.set_margin_start(8)
            fbox.set_margin_end(8)
            fbox.set_margin_top(4)
            fbox.set_margin_bottom(4)

            lbl = Gtk.Label()
            lbl.set_markup(f"<b>{emu['name']}</b>")
            lbl.set_xalign(0)
            fbox.pack_start(lbl, False, False, 0)

            # Binary path row
            hbox = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=6
            )
            entry = Gtk.Entry()
            entry.set_text(config.emu_path(emu["key"]))
            entry.set_hexpand(True)
            self.path_entries[emu["key"]] = entry
            hbox.pack_start(entry, True, True, 0)

            browse_btn = Gtk.Button(label="Browse\u2026")
            browse_btn.connect("clicked", self._on_browse, entry)
            hbox.pack_start(browse_btn, False, False, 0)
            fbox.pack_start(hbox, False, False, 0)

            # Open folder + Copy launch command
            action_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=6
            )
            open_btn = Gtk.Button(label="Open folder")
            open_btn.set_tooltip_text(
                "Open the emulator's directory in the file manager"
            )
            open_btn.connect("clicked", self._on_open_folder, entry)
            action_box.pack_start(open_btn, False, False, 0)

            copy_btn = Gtk.Button(label="Copy launch command")
            copy_btn.set_tooltip_text(
                "Copy CLI command for kiosk-manager or scripts"
            )
            copy_btn.connect(
                "clicked", self._on_copy_command, emu
            )
            action_box.pack_start(copy_btn, False, False, 0)
            fbox.pack_start(action_box, False, False, 0)

            # Launch options — single compact row
            sect = config.launch_section(emu["key"])

            opt_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )

            nogui_chk = Gtk.CheckButton(label="No GUI")
            nogui_chk.set_active(
                config.getbool(sect, "nogui", fallback=True)
            )
            self.nogui_checks[emu["key"]] = nogui_chk
            opt_box.pack_start(nogui_chk, False, False, 0)

            fs_chk = Gtk.CheckButton(label="Fullscreen")
            fs_chk.set_active(
                config.getbool(sect, "fullscreen", fallback=False)
            )
            self.fullscreen_checks[emu["key"]] = fs_chk
            opt_box.pack_start(fs_chk, False, False, 0)

            res_combo = Gtk.ComboBoxText()
            current_res = config.get(
                sect, "screen_res", fallback="1024x768"
            )
            active_idx = 0
            for i, res in enumerate(SCREEN_RESOLUTIONS):
                res_combo.append_text(res)
                if res == current_res:
                    active_idx = i
            res_combo.set_active(active_idx)
            self.res_combos[emu["key"]] = res_combo
            opt_box.pack_end(res_combo, False, False, 0)

            res_lbl = Gtk.Label(label="Res:")
            opt_box.pack_end(res_lbl, False, False, 0)

            fbox.pack_start(opt_box, False, False, 0)

            frame.add(fbox)
            emu_page.pack_start(frame, False, False, 0)

        notebook.append_page(emu_page, Gtk.Label(label="Emulators"))

        # ═══ Tab 2: Touch ═══
        touch_page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        touch_page.set_margin_start(12)
        touch_page.set_margin_end(12)
        touch_page.set_margin_top(8)
        touch_page.set_margin_bottom(8)

        self.click_delay = self._setting_row(
            touch_page,
            "Tap accuracy delay",
            "Pauses briefly after fast finger moves\n"
            "to prevent clicking at the wrong spot",
            config.getint("behavior", "click_delay_ms", fallback=100),
            10, 500, 10, 0, "ms",
        )
        self.long_press = self._setting_row(
            touch_page,
            "Hold to double-click",
            "How long to hold your finger in one\n"
            "spot to open a file or folder",
            config.getfloat("behavior", "long_press_time", fallback=2.0),
            0.5, 5.0, 0.1, 1, "sec",
        )
        self.hold_tol = self._setting_row(
            touch_page,
            "Finger wobble allowance",
            "How much your finger can drift while\n"
            "holding and still count as a double-click",
            config.getint("behavior", "hold_tolerance", fallback=30),
            5, 100, 5, 0, "px",
        )
        self.focus_int = self._setting_row(
            touch_page,
            "Responsiveness",
            "How quickly the shim reacts when you\n"
            "switch to or from the emulator window",
            config.getfloat(
                "behavior", "focus_check_interval", fallback=0.25
            ),
            0.05, 1.0, 0.05, 2, "sec",
        )

        # Calibration status
        touch_page.pack_start(Gtk.Separator(), False, False, 4)

        cal_label = Gtk.Label()
        cal_label.set_markup(
            '<span size="small" weight="bold" foreground="gray">'
            "CALIBRATION</span>"
        )
        cal_label.set_xalign(0)
        touch_page.pack_start(cal_label, False, False, 4)

        row1, row2, found = read_calibration_info()

        cal_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        matrix_label = Gtk.Label()
        matrix_label.set_markup(
            f'<span font_family="monospace" size="small">'
            f"{row1}\n{row2}</span>"
        )
        matrix_label.set_xalign(0)
        matrix_label.set_selectable(True)
        cal_box.pack_start(matrix_label, True, True, 0)

        status = Gtk.Label()
        if found:
            status.set_markup(
                '<span foreground="#639922" size="small">'
                "\u25cf Linked</span>"
            )
        else:
            status.set_markup(
                '<span foreground="#BA7517" size="small">'
                "\u25cf Not found</span>"
            )
        cal_box.pack_end(status, False, False, 0)
        touch_page.pack_start(cal_box, False, False, 0)

        hint = Gtk.Label()
        if found:
            hint.set_markup(
                '<span size="x-small" foreground="gray">'
                "Managed by Display Calibrator.</span>"
            )
        else:
            hint.set_markup(
                '<span size="x-small" foreground="gray">'
                "No calibration rule found. Install Display\n"
                "Calibrator or create a udev rule manually.</span>"
            )
        hint.set_xalign(0)
        hint.set_line_wrap(True)
        touch_page.pack_start(hint, False, False, 0)

        notebook.append_page(touch_page, Gtk.Label(label="Touch"))

        # ═══ Tab 3: Audio ═══
        audio_page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        audio_page.set_margin_start(12)
        audio_page.set_margin_end(12)
        audio_page.set_margin_top(8)
        audio_page.set_margin_bottom(8)

        audio_status = Gtk.Label()
        audio_status.set_markup(
            '<span foreground="#639922">\u25cf Active</span>'
        )
        audio_status.set_xalign(0)
        audio_page.pack_start(audio_status, False, False, 0)

        audio_hint = Gtk.Label()
        audio_hint.set_markup(
            '<span size="x-small" foreground="gray">'
            "A silent audio keepalive stream runs alongside the\n"
            "emulator to prevent the vc4-hdmi driver from\n"
            "deadlocking. SDL uses the native PipeWire backend.\n\n"
            "This is always active — no configuration needed.</span>"
        )
        audio_hint.set_xalign(0)
        audio_page.pack_start(audio_hint, False, False, 0)

        notebook.append_page(audio_page, Gtk.Label(label="Audio"))

        # ── Bottom bar (matches display-calibrator) ──
        bottom = Gtk.Box(spacing=8)
        bottom.set_margin_top(4)

        self.tray_check = Gtk.CheckButton(label="Tray icon")
        self.tray_check.set_active(config.show_tray)
        self.tray_check.set_tooltip_text("Show system tray icon on login")
        bottom.pack_start(self.tray_check, False, False, 0)

        self._status_label = Gtk.Label(label="")
        self._status_label.get_style_context().add_class("dim-label")
        bottom.pack_start(self._status_label, False, False, 4)

        btn_apply = Gtk.Button(label="Apply")
        btn_apply.get_style_context().add_class("suggested-action")
        btn_apply.connect("clicked", self._on_apply)
        bottom.pack_end(btn_apply, False, False, 0)

        btn_close = Gtk.Button(label="Close")
        btn_close.connect("clicked", lambda _: self.win.destroy())
        bottom.pack_end(btn_close, False, False, 0)

        outer.pack_end(bottom, False, False, 0)

        self.win.show_all()

    def _setting_row(self, parent, title, hint, value,
                     lo, hi, step, digits, unit):
        vbox = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        vbox.set_margin_top(4)
        vbox.set_margin_bottom(4)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t = Gtk.Label(label=title)
        t.set_xalign(0)
        left.pack_start(t, False, False, 0)
        h = Gtk.Label()
        h.set_markup(
            f'<span size="small" foreground="gray">{hint}</span>'
        )
        h.set_xalign(0)
        left.pack_start(h, False, False, 0)
        vbox.pack_start(left, True, True, 0)

        adj = Gtk.Adjustment(
            value=value, lower=lo, upper=hi,
            step_increment=step, page_increment=step * 10,
        )
        spin = Gtk.SpinButton(adjustment=adj, digits=digits)
        spin.set_width_chars(5)
        vbox.pack_end(spin, False, False, 0)

        unit_lbl = Gtk.Label()
        unit_lbl.set_markup(
            f'<span size="small" foreground="gray">{unit}</span>'
        )
        unit_lbl.set_width_chars(3)
        vbox.pack_end(unit_lbl, False, False, 0)

        parent.pack_start(vbox, False, False, 0)
        parent.pack_start(Gtk.Separator(), False, False, 0)
        return spin

    def _show_status(self, text, timeout_ms=2000):
        self._status_label.set_text(text)
        GLib.timeout_add(timeout_ms, lambda: self._status_label.set_text(""))

    def _on_browse(self, button, entry):
        dialog = Gtk.FileChooserDialog(
            title="Select emulator binary",
            parent=self.win,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Open", Gtk.ResponseType.OK,
        )
        current = entry.get_text()
        if current and os.path.isdir(os.path.dirname(current)):
            dialog.set_current_folder(os.path.dirname(current))
        if dialog.run() == Gtk.ResponseType.OK:
            entry.set_text(dialog.get_filename())
        dialog.destroy()

    def _on_open_folder(self, button, entry):
        path = entry.get_text().strip()
        if path:
            folder = os.path.dirname(os.path.expanduser(path))
        else:
            folder = os.path.expanduser("~")
        if not os.path.isdir(folder):
            folder = os.path.expanduser("~")
        try:
            subprocess.Popen(
                ["xdg-open", folder],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            print(f"[{APP_ID}] Open folder: {e}", file=sys.stderr)

    def _on_copy_command(self, button, emu):
        # Collect current values first
        self._collect_values()
        cmd = build_launch_cmd(self.config, emu)
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(cmd, -1)
        clipboard.store()
        orig = button.get_label()
        button.set_label("Copied!")
        GLib.timeout_add(1500, lambda: button.set_label(orig))

    def _collect_values(self):
        """Read all widget values into config without saving."""
        for emu in EMULATORS:
            key = emu["key"]
            self.config.set_emu_path(
                key, self.path_entries[key].get_text()
            )
            sect = self.config.launch_section(key)
            self.config.set(
                sect, "nogui",
                str(self.nogui_checks[key].get_active()).lower()
            )
            self.config.set(
                sect, "fullscreen",
                str(self.fullscreen_checks[key].get_active()).lower()
            )
            self.config.set(
                sect, "screen_res",
                self.res_combos[key].get_active_text() or "1024x768"
            )
        self.config.set(
            "behavior", "click_delay_ms",
            int(self.click_delay.get_value())
        )
        self.config.set(
            "behavior", "long_press_time",
            round(self.long_press.get_value(), 1)
        )
        self.config.set(
            "behavior", "hold_tolerance",
            int(self.hold_tol.get_value())
        )
        self.config.set(
            "behavior", "focus_check_interval",
            round(self.focus_int.get_value(), 2)
        )
        self.config.set(
            "ui", "show_tray", self.tray_check.get_active()
        )

    def _on_apply(self, _):
        self._collect_values()
        self.on_apply_cb()
        self._show_status("Saved")


# ── Tray application ───────────────────────────────────────────────

class EmulatorManagerApp:
    def __init__(self):
        self.config = Config()
        self.emu = EmulatorProcess()
        self.indicator = None
        self.menu = None
        self.menu_items = {}
        self._poll_tag = None
        self._settings_win = None

        self._build_indicator()
        self._build_menu()
        self._start_poll()

    def _build_indicator(self):
        if AppIndicator is None:
            return
        self.indicator = AppIndicator.Indicator.new(
            APP_ID, ICON_NAME,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        # Fallback theme path for hicolor cache miss
        self.indicator.set_icon_theme_path(INSTALL_DIR)
        if self.config.show_tray:
            self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        else:
            self.indicator.set_status(AppIndicator.IndicatorStatus.PASSIVE)

    def _build_menu(self):
        self.menu = Gtk.Menu()
        self.menu_items = {}

        for emu in EMULATORS:
            item = Gtk.MenuItem()
            hbox = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            name_label = Gtk.Label(label=emu["name"])
            name_label.set_xalign(0)
            hbox.pack_start(name_label, True, True, 0)
            status_label = Gtk.Label()
            status_label.set_xalign(1)
            hbox.pack_end(status_label, False, False, 0)
            item.add(hbox)
            item.connect("activate", self._on_launch, emu)
            self.menu.append(item)
            self.menu_items[emu["key"]] = {
                "item": item,
                "name_label": name_label,
                "status_label": status_label,
            }

        self.menu.append(Gtk.SeparatorMenuItem())

        self.status_item = Gtk.MenuItem(label="Shim: Idle")
        self.status_item.set_sensitive(False)
        self.menu.append(self.status_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        settings_item = Gtk.MenuItem(label="Settings\u2026")
        settings_item.connect("activate", self._on_settings)
        self.menu.append(settings_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_quit)
        self.menu.append(quit_item)

        self.menu.show_all()
        if self.indicator:
            self.indicator.set_menu(self.menu)
        self._update_menu_state()

    def _update_menu_state(self):
        running = self.emu.is_running
        for emu in EMULATORS:
            widgets = self.menu_items[emu["key"]]
            item = widgets["item"]
            status_label = widgets["status_label"]
            path = self.config.emu_path(emu["key"])
            path_exists = path and os.path.isfile(path)

            if running and self.emu.running_key == emu["key"]:
                status_label.set_markup(
                    '<span size="small" foreground="#639922">'
                    "\u25cf Running</span>"
                )
                item.set_sensitive(False)
            elif running:
                status_label.set_text("")
                item.set_sensitive(False)
            elif not path_exists:
                status_label.set_markup(
                    '<span size="small" foreground="#BA7517">'
                    "Not found</span>"
                )
                item.set_sensitive(False)
            else:
                status_label.set_text("")
                item.set_sensitive(True)

        if running:
            name = next(
                (e["name"] for e in EMULATORS
                 if e["key"] == self.emu.running_key), "?"
            )
            self.status_item.set_label(f"Shim: Active ({name})")
        else:
            self.status_item.set_label("Shim: Idle")

    def _start_poll(self):
        if self._poll_tag:
            GLib.source_remove(self._poll_tag)
        self._poll_tag = GLib.timeout_add_seconds(2, self._poll_tick)

    def _poll_tick(self):
        if self.emu.running_key is not None:
            if not self.emu.check():
                self._update_menu_state()
        return True

    def _on_launch(self, menu_item, emu):
        path = self.config.emu_path(emu["key"])
        if not path or not os.path.isfile(path):
            dialog = Gtk.MessageDialog(
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=f"{emu['name']} not found",
                secondary_text=(
                    f"Binary not found at:\n{path}\n\n"
                    "Use Settings to set the correct path."
                ),
            )
            dialog.run()
            dialog.destroy()
            return
        if self.emu.launch(self.config, emu):
            self._update_menu_state()
            GLib.timeout_add(500, self._poll_tick)

    def _on_settings(self, _):
        if self._settings_win:
            self._settings_win.win.present()
            return
        sw = SettingsWindow(self.config, self._apply_settings)
        sw.win.connect("destroy", lambda _: self._on_settings_closed())
        self._settings_win = sw

    def _on_settings_closed(self):
        self._settings_win = None

    def _apply_settings(self):
        self.config.save()
        if self.indicator:
            status = (
                AppIndicator.IndicatorStatus.ACTIVE
                if self.config.show_tray
                else AppIndicator.IndicatorStatus.PASSIVE
            )
            self.indicator.set_status(status)
        self._update_menu_state()
        subprocess.run(
            ["killall", "-HUP", "touch_shim.py"],
            capture_output=True,
        )

    def _on_quit(self, menu_item):
        Gtk.main_quit()

    def run(self):
        Gtk.main()


# ── Entry point ─────────────────────────────────────────────────────

def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    lock_fd = acquire_lock()  # noqa: F841

    if "--show-tray" in sys.argv:
        cfg = Config()
        cfg.set("ui", "show_tray", True)
        cfg.save()

    app = EmulatorManagerApp()
    app.run()


if __name__ == "__main__":
    main()
