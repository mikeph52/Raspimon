#!/usr/bin/env python3
"""
Raspimon - Single-page Full Dashboard TUI
Features:
 - Full dashboard homepage (single-page)
 - Real-time CPU graph (sparkline + bar)
 - Real-time temperature graph
 - Real-time network throughput graphs (up/down)
 - SD card read/write graph (disk io)
 - GPU usage graph (best-effort for Pi 5 via vcgencmd)
 - Fan / power info (best-effort via vcgencmd)
 - Multiple graph windows laid out on one page
 - Theme presets (light, dark, solarized-like)
 - Simple plugin system (plugins folder; plugins must define register(app))

Notes:
 - Requires psutil (pip install psutil)
 - On Raspberry Pi, vcgencmd is used when available for temperature/gpu/fan info.
 - The program runs entirely in the terminal using curses and updates every 0.4s.

"""

import curses
import time
import os
import math
import importlib.util
import glob
import psutil
import shutil
import json
import traceback
import subprocess
from collections import deque

# ----------------------------- Configuration -----------------------------
REFRESH = 0.4  # seconds
HISTORY_LENGTH = 120  # number of points kept for sparkline
PLUGINS_DIR = "plugins"

# Default themes
THEMES = {
    "dark": {
        "bg": curses.COLOR_BLACK,
        "fg": curses.COLOR_WHITE,
        "accent": curses.COLOR_CYAN,
        "warn": curses.COLOR_YELLOW,
        "danger": curses.COLOR_RED,
    },
    "light": {
        "bg": curses.COLOR_WHITE,
        "fg": curses.COLOR_BLACK,
        "accent": curses.COLOR_BLUE,
        "warn": curses.COLOR_MAGENTA,
        "danger": curses.COLOR_RED,
    },
    "solar": {
        "bg": curses.COLOR_BLACK,
        "fg": curses.COLOR_YELLOW,
        "accent": curses.COLOR_GREEN,
        "warn": curses.COLOR_MAGENTA,
        "danger": curses.COLOR_RED,
    }
}

# ------------------------- Utility functions -----------------------------

def safe_cmd(cmd):
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return ""


def vcgencmd_available():
    return shutil.which("vcgencmd") is not None


def get_pi_temp():
    # try vcgencmd first
    if vcgencmd_available():
        out = safe_cmd(["vcgencmd", "measure_temp"])  # like: temp=45.2'C
        try:
            return float(out.replace("temp=", "").replace("'C", ""))
        except Exception:
            return None
    # fallback to psutil sensors
    try:
        temps = psutil.sensors_temperatures()
        # look for 'cpu-thermal' or 'coretemp' etc
        for k in temps:
            for entry in temps[k]:
                if entry.current:
                    return entry.current
    except Exception:
        pass
    return None


def get_gpu_usage():
    # best-effort: vcgencmd measure_clock core and measure_temp/gpu_mem
    if vcgencmd_available():
        # there is no direct 'usage' metric; we approximate via clock compared to max clock
        base = safe_cmd(["vcgencmd", "measure_clock", "core"])  # core=250000000
        try:
            cur = int(base.split("=")[1])
        except Exception:
            return None
        # try reading max clock via /sys or assume 500MHz
        max_clock = 600_000_000
        return min(100.0, (cur / max_clock) * 100.0)
    return None


def get_fan_power():
    # Use vcgencmd get_throttled or get_config int? best-effort
    if vcgencmd_available():
        throttled = safe_cmd(["vcgencmd", "get_throttled"])  # throttled=0x0
        volts = safe_cmd(["vcgencmd", "measure_volts"])  # volt=1.2000V
        return throttled, volts
    return None, None


def human(n):
    # human-readable bytes
    for unit in ["B","KB","MB","GB","TB"]:
        if abs(n) < 1024.0:
            return f"{n:3.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"


def clamp(v, a=0.0, b=100.0):
    return max(a, min(b, v))

# ----------------------------- Data Store --------------------------------

class DataStore:
    def __init__(self, history_len=HISTORY_LENGTH):
        self.history_len = history_len
        self.cpu = deque([0.0]*history_len, maxlen=history_len)
        self.temp = deque([0.0]*history_len, maxlen=history_len)
        self.net_in = deque([0.0]*history_len, maxlen=history_len)
        self.net_out = deque([0.0]*history_len, maxlen=history_len)
        self.disk_read = deque([0.0]*history_len, maxlen=history_len)
        self.disk_write = deque([0.0]*history_len, maxlen=history_len)
        self.gpu = deque([0.0]*history_len, maxlen=history_len)
        self.time = deque([time.time() - (history_len-i)*REFRESH for i in range(history_len)], maxlen=history_len)
        # previous counters for rates
        self.prev_net = psutil.net_io_counters()
        self.prev_disk = psutil.disk_io_counters()

    def update(self):
        t = time.time()
        # CPU
        try:
            cpuv = psutil.cpu_percent(interval=None)
        except Exception:
            cpuv = 0.0
        self.cpu.append(cpuv)

        # Temp
        tmp = get_pi_temp()
        if tmp is None:
            tmp = 0.0
        self.temp.append(tmp)

        # Network rates
        now_net = psutil.net_io_counters()
        dt = REFRESH
        in_rate = (now_net.bytes_recv - self.prev_net.bytes_recv) / dt
        out_rate = (now_net.bytes_sent - self.prev_net.bytes_sent) / dt
        self.prev_net = now_net
        self.net_in.append(in_rate)
        self.net_out.append(out_rate)

        # Disk IO rates (system-wide)
        now_disk = psutil.disk_io_counters()
        if now_disk is None:
            read_rate = 0.0
            write_rate = 0.0
        else:
            read_rate = (now_disk.read_bytes - self.prev_disk.read_bytes) / dt
            write_rate = (now_disk.write_bytes - self.prev_disk.write_bytes) / dt
            self.prev_disk = now_disk
        self.disk_read.append(read_rate)
        self.disk_write.append(write_rate)

        # GPU
        gpuv = get_gpu_usage()
        if gpuv is None:
            gpuv = 0.0
        self.gpu.append(gpuv)

        # time
        self.time.append(t)

# ----------------------------- Plugins -----------------------------------

class PluginManager:
    def __init__(self, app):
        self.app = app
        self.plugins = []

    def discover_and_load(self):
        if not os.path.isdir(PLUGINS_DIR):
            return
        files = glob.glob(os.path.join(PLUGINS_DIR, "*.py"))
        for f in files:
            try:
                name = os.path.splitext(os.path.basename(f))[0]
                spec = importlib.util.spec_from_file_location(name, f)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "register"):
                    module.register(self.app)
                    self.plugins.append(name)
            except Exception:
                # don't fail dashboard on plugin errors
                traceback.print_exc()

# ----------------------------- Drawing helpers ---------------------------

SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values, width):
    if width <= 0:
        return ""
    # normalize to 0-1
    mn = min(values) if values else 0
    mx = max(values) if values else 1
    rng = mx - mn if mx != mn else 1
    step = max(1, len(values) // width)
    out = []
    for i in range(0, len(values), step):
        v = values[i]
        idx = int(((v - mn) / rng) * (len(SPARK_CHARS)-1))
        out.append(SPARK_CHARS[idx])
    s = "".join(out)
    if len(s) > width:
        s = s[-width:]
    return s.rjust(width)


def draw_bar(stdscr, y, x, width, pct):
    filled = int((pct/100.0) * width)
    stdscr.addstr(y, x, "█"*filled + " "*(width-filled))

# ----------------------------- App ---------------------------------------

class App:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.data = DataStore()
        self.running = True
        self.theme_name = "dark"
        self.theme = THEMES[self.theme_name]
        self.width = 0
        self.height = 0
        self.plugins = PluginManager(self)
        self.selected_widget = 0
        self.widgets = [
            "CPU",
            "Temp",
            "Net",
            "SD IO",
            "GPU",
            "Fan/Power",
            "Plugins",
        ]
        # initial psutil call to populate counters
        psutil.cpu_percent(interval=None)
        self.plugins.discover_and_load()

    def init_curses(self):
        curses.curs_set(0)
        curses.use_default_colors()
        curses.start_color()
        # map colors from theme
        curses.init_pair(1, self.theme["fg"], self.theme["bg"])      # main
        curses.init_pair(2, self.theme["accent"], self.theme["bg"])  # accent
        curses.init_pair(3, self.theme["warn"], self.theme["bg"])    # warn
        curses.init_pair(4, self.theme["danger"], self.theme["bg"])  # danger
        curses.init_pair(5, curses.COLOR_GREEN, self.theme["bg"])     # good

    def switch_theme(self, name):
        if name in THEMES:
            self.theme_name = name
            self.theme = THEMES[name]
            self.init_curses()

    def draw_header(self):
        title = f" Raspimon 0.29 by mikeph_ 2021-2025 • Theme: {self.theme_name} "
        self.stdscr.attron(curses.color_pair(2))
        self.stdscr.addstr(0, 0, title)
        self.stdscr.attroff(curses.color_pair(2))
        self.stdscr.hline(1, 0, "-", self.width)

    def draw_footer(self):
        hint = " ←/→ switch widget  |  t toggle theme  |  q quit  |  r reload plugins "
        self.stdscr.hline(self.height-2, 0, "-", self.width)
        self.stdscr.addstr(self.height-1, 0, hint)

    def draw_sidebar(self):
        w = max(28, int(self.width * 0.22))
        x = self.width - w
        # box
        for row in range(2, self.height-2):
            self.stdscr.addstr(row, x-1, "|")
        self.stdscr.attron(curses.color_pair(2))
        self.stdscr.addstr(2, x+1, " System ")
        self.stdscr.attroff(curses.color_pair(2))

        # stats
        cpu = self.data.cpu[-1]
        temp = self.data.temp[-1]
        mem = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
        net = psutil.net_io_counters()
        ip = safe_cmd(["hostname","-I"]).split()
        ip_str = ip[0] if ip else "-"

        stats = [
            ("CPU", f"{cpu:.1f}%"),
            ("Temp", f"{temp:.1f}°C"),
            ("Mem", f"{mem:.1f}%"),
            ("Disk", f"{disk:.1f}%"),
            ("IP", ip_str),
            ("Sent", human(net.bytes_sent)),
            ("Recv", human(net.bytes_recv)),
        ]
        y = 4
        for k,v in stats:
            self.stdscr.addstr(y, x+1, f"{k:6} {v}")
            y += 1

        # widgets list
        self.stdscr.addstr(y+1, x+1, " Widgets:")
        for i, wname in enumerate(self.widgets):
            marker = "▶" if i == self.selected_widget else "  "
            self.stdscr.addstr(y+2+i, x+1, f"{marker} {wname}")

    def draw_main(self):
        # Layout: grid of panels in the left area
        main_w = self.width - max(28, int(self.width * 0.22)) - 2
        main_h = self.height - 6
        # top row: CPU sparkline + CPU bar and GPU small graph
        # second row: Temp sparkline + SD IO
        # third row: Network throughput (two smaller graphs)

        # CPU panel
        cpu_title = "CPU"
        self.stdscr.addstr(3, 2, cpu_title, curses.color_pair(2))
        # sparkline
        sl = sparkline(list(self.data.cpu), min(60, main_w-6))
        self.stdscr.addstr(4, 2, sl)
        # bar
        draw_bar(self.stdscr, 5, 2, min(60, main_w-6), self.data.cpu[-1])
        self.stdscr.addstr(5, min(64, main_w-2), f" {self.data.cpu[-1]:5.1f}%")

        # GPU small
        self.stdscr.addstr(3, 70 if main_w>80 else min(40, main_w-20), "GPU")
        gpu_bar_x = 70 if main_w>80 else min(40, main_w-20)
        draw_bar(self.stdscr, 4, gpu_bar_x, 20, self.data.gpu[-1])
        self.stdscr.addstr(4, gpu_bar_x+22, f"{self.data.gpu[-1]:4.0f}%")

        # Temp panel
        self.stdscr.addstr(7, 2, "Temperature", curses.color_pair(2))
        tsl = sparkline(list(self.data.temp), min(60, main_w-6))
        self.stdscr.addstr(8, 2, tsl)
        draw_bar(self.stdscr, 9, 2, min(60, main_w-6), clamp(self.data.temp[-1],0,100))
        self.stdscr.addstr(9, min(64, main_w-2), f" {self.data.temp[-1]:5.1f}°C")

        # SD IO panel
        self.stdscr.addstr(11, 2, "SD Card R/W", curses.color_pair(2))
        rsl = sparkline(list(self.data.disk_read), min(60, main_w-6))
        wsl = sparkline(list(self.data.disk_write), min(60, main_w-6))
        self.stdscr.addstr(12, 2, "R:"+rsl)
        self.stdscr.addstr(13, 2, "W:"+wsl)
        self.stdscr.addstr(12, min(64, main_w-2), f"{human(self.data.disk_read[-1])}/s")
        self.stdscr.addstr(13, min(64, main_w-2), f"{human(self.data.disk_write[-1])}/s")

        # Network panel
        self.stdscr.addstr(15, 2, "Network (rates)", curses.color_pair(2))
        netw = min(60, main_w-6)
        in_sp = sparkline(list(self.data.net_in), netw)
        out_sp = sparkline(list(self.data.net_out), netw)
        self.stdscr.addstr(16, 2, "↓:"+in_sp)
        self.stdscr.addstr(17, 2, "↑:"+out_sp)
        self.stdscr.addstr(16, min(64, main_w-2), f"{human(self.data.net_in[-1])}/s")
        self.stdscr.addstr(17, min(64, main_w-2), f"{human(self.data.net_out[-1])}/s")

        # Fan / Power small
        self.stdscr.addstr(19, 2, "Fan / Power", curses.color_pair(2))
        throttled, volts = get_fan_power()
        self.stdscr.addstr(20, 2, f"Throttled: {throttled or '-'}")
        self.stdscr.addstr(21, 2, f"Volts: {volts or '-'}")

        # Plugins area
        self.stdscr.addstr(23, 2, "Plugins Loaded:", curses.color_pair(2))
        pl = self.plugins.plugins
        self.stdscr.addstr(24, 2, ", ".join(pl) if pl else "(none)")

        # highlight selected widget area with a border or marker
        sel = self.selected_widget
        self.stdscr.addstr(3 + sel*2, self.width-5, "◀")

    def handle_input(self):
        ch = self.stdscr.getch()
        if ch == -1:
            return
        if ch in (ord('q'), ord('Q')):
            self.running = False
        elif ch in (curses.KEY_RIGHT, ord('l')):
            self.selected_widget = (self.selected_widget + 1) % len(self.widgets)
        elif ch in (curses.KEY_LEFT, ord('h')):
            self.selected_widget = (self.selected_widget - 1) % len(self.widgets)
        elif ch in (ord('t'),):
            # cycle theme
            keys = list(THEMES.keys())
            idx = keys.index(self.theme_name)
            self.switch_theme(keys[(idx+1) % len(keys)])
        elif ch in (ord('r'),):
            self.plugins = PluginManager(self)
            self.plugins.discover_and_load()
        elif ch == ord('p'):
            # placeholder to open plugin menu
            pass

    def run(self):
        self.init_curses()
        self.stdscr.nodelay(True)
        last = time.time()
        while self.running:
            try:
                self.height, self.width = self.stdscr.getmaxyx()
                now = time.time()
                if now - last >= REFRESH:
                    self.data.update()
                    last = now
                self.stdscr.erase()
                self.draw_header()
                self.draw_sidebar()
                self.draw_main()
                self.draw_footer()
                self.stdscr.refresh()
                self.handle_input()
                time.sleep(0.05)
            except KeyboardInterrupt:
                self.running = False
            except Exception:
                # draw the exception in the screen for debugging
                self.stdscr.erase()
                lines = traceback.format_exc().splitlines()
                for i,l in enumerate(lines[:self.height-1]):
                    try:
                        self.stdscr.addstr(i,0,l[:self.width-1])
                    except Exception:
                        pass
                self.stdscr.refresh()
                time.sleep(2.0)
                self.running = False

# ----------------------------- Entry point --------------------------------

def main(stdscr):
    app = App(stdscr)
    app.run()

if __name__ == '__main__':
    curses.wrapper(main)
