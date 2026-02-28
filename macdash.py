#!/usr/bin/env python3

from __future__ import annotations

import curses
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional


CPU_RE = re.compile(
    r"CPU usage:\s+([0-9.]+)% user,\s+([0-9.]+)% sys,\s+([0-9.]+)% idle"
)
MEM_RE = re.compile(
    r"PhysMem:\s+([0-9A-Za-z.]+)\s+used\s+\(([^)]+)\),\s+([0-9A-Za-z.]+)\s+unused"
)
DEFAULT_IFACE_RE = re.compile(r"interface:\s+(\S+)")
GPU_NAME_RE = re.compile(r'"spdisplays_device-name"\s*:\s*"([^"]+)"')


@dataclass
class CPUStats:
    user: float
    system: float
    idle: float
    load1: float
    load5: float
    load15: float


@dataclass
class MemoryStats:
    used_bytes: int
    free_bytes: int
    app_bytes: int
    wired_bytes: int
    compressed_bytes: int


@dataclass
class NetworkStats:
    interface: str
    rx_bytes: int
    tx_bytes: int
    rx_rate: float
    tx_rate: float


@dataclass
class Snapshot:
    cpu: CPUStats
    memory: MemoryStats
    network: NetworkStats
    gpu_name: str
    gpu_note: str
    captured_at: float


class Sampler:
    def __init__(self) -> None:
        self.default_interface = self._get_default_interface()
        self.gpu_name = self._get_gpu_name()
        self._last_rx: Optional[int] = None
        self._last_tx: Optional[int] = None
        self._last_net_ts: Optional[float] = None

    def sample(self) -> Snapshot:
        now = time.time()
        cpu_line, mem_line = self._sample_top()
        cpu = self._parse_cpu(cpu_line)
        memory = self._parse_memory(mem_line)
        network = self._sample_network(now)
        return Snapshot(
            cpu=cpu,
            memory=memory,
            network=network,
            gpu_name=self.gpu_name,
            gpu_note="Utilization unavailable via stable public macOS APIs.",
            captured_at=now,
        )

    def _run(self, *args: str) -> str:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return ""
        return result.stdout

    def _sample_top(self) -> tuple[str, str]:
        output = self._run("top", "-l", "1", "-n", "0")
        cpu_line = ""
        mem_line = ""
        for line in output.splitlines():
            if line.startswith("CPU usage:"):
                cpu_line = line.strip()
            elif line.startswith("PhysMem:"):
                mem_line = line.strip()
        return cpu_line, mem_line

    def _parse_cpu(self, line: str) -> CPUStats:
        match = CPU_RE.search(line)
        user = system = idle = 0.0
        if match:
            user = float(match.group(1))
            system = float(match.group(2))
            idle = float(match.group(3))
        load1, load5, load15 = (0.0, 0.0, 0.0)
        try:
            load1, load5, load15 = os.getloadavg()
        except OSError:
            pass
        return CPUStats(
            user=user,
            system=system,
            idle=idle,
            load1=load1,
            load5=load5,
            load15=load15,
        )

    def _parse_memory(self, line: str) -> MemoryStats:
        used_bytes = free_bytes = app_bytes = wired_bytes = compressed_bytes = 0
        match = MEM_RE.search(line)
        if match:
            used_bytes = parse_size(match.group(1))
            free_bytes = parse_size(match.group(3))
            details = [part.strip() for part in match.group(2).split(",")]
            for detail in details:
                if "app" in detail:
                    app_bytes = parse_size(detail.split()[0])
                elif "wired" in detail:
                    wired_bytes = parse_size(detail.split()[0])
                elif "compressed" in detail:
                    compressed_bytes = parse_size(detail.split()[0])
        return MemoryStats(
            used_bytes=used_bytes,
            free_bytes=free_bytes,
            app_bytes=app_bytes,
            wired_bytes=wired_bytes,
            compressed_bytes=compressed_bytes,
        )

    def _sample_network(self, now: float) -> NetworkStats:
        output = self._run("netstat", "-bI", self.default_interface)
        rx_bytes = tx_bytes = 0
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 10 or parts[0] != self.default_interface:
                continue
            try:
                ibytes = int(parts[6])
                obytes = int(parts[9])
            except ValueError:
                continue
            rx_bytes = max(rx_bytes, ibytes)
            tx_bytes = max(tx_bytes, obytes)

        rx_rate = tx_rate = 0.0
        if self._last_net_ts is not None and now > self._last_net_ts:
            elapsed = now - self._last_net_ts
            if self._last_rx is not None and rx_bytes >= self._last_rx:
                rx_rate = (rx_bytes - self._last_rx) / elapsed
            if self._last_tx is not None and tx_bytes >= self._last_tx:
                tx_rate = (tx_bytes - self._last_tx) / elapsed

        self._last_rx = rx_bytes
        self._last_tx = tx_bytes
        self._last_net_ts = now

        return NetworkStats(
            interface=self.default_interface,
            rx_bytes=rx_bytes,
            tx_bytes=tx_bytes,
            rx_rate=rx_rate,
            tx_rate=tx_rate,
        )

    def _get_default_interface(self) -> str:
        output = self._run("route", "-n", "get", "default")
        match = DEFAULT_IFACE_RE.search(output)
        return match.group(1) if match else "en0"

    def _get_gpu_name(self) -> str:
        output = self._run("system_profiler", "SPDisplaysDataType", "-json")
        match = GPU_NAME_RE.search(output)
        if match:
            return match.group(1)
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            return "Unknown GPU"
        for adapter in parsed.get("SPDisplaysDataType", []):
            name = adapter.get("sppci_model") or adapter.get("_name")
            if name:
                return str(name)
        return "Unknown GPU"


def parse_size(value: str) -> int:
    units = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    value = value.strip()
    match = re.fullmatch(r"([0-9.]+)([BKMGT])?", value)
    if not match:
        return 0
    number = float(match.group(1))
    unit = match.group(2) or "B"
    return int(number * units[unit])


def human_bytes(value: float) -> str:
    suffixes = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for suffix in suffixes:
        if size < 1024 or suffix == suffixes[-1]:
            return f"{size:,.1f} {suffix}"
        size /= 1024
    return f"{value:.1f} B"


def percent_bar(value: float, width: int) -> str:
    width = max(width, 4)
    filled = int(round((max(0.0, min(100.0, value)) / 100.0) * width))
    return "[" + ("#" * filled).ljust(width) + "]"


def safe_addstr(stdscr: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    max_y, max_x = stdscr.getmaxyx()
    if y < 0 or y >= max_y or x >= max_x:
        return
    clipped = text[: max(0, max_x - x - 1)]
    if not clipped:
        return
    try:
        stdscr.addstr(y, x, clipped, attr)
    except curses.error:
        pass


def draw_box(stdscr: curses.window, y: int, x: int, h: int, w: int, title: str) -> None:
    if h < 3 or w < 4:
        return
    stdscr.addstr(y, x, "+" + "-" * (w - 2) + "+")
    for row in range(y + 1, y + h - 1):
        stdscr.addstr(row, x, "|")
        stdscr.addstr(row, x + w - 1, "|")
    stdscr.addstr(y + h - 1, x, "+" + "-" * (w - 2) + "+")
    safe_addstr(stdscr, y, x + 2, f" {title} ", curses.color_pair(4) | curses.A_BOLD)


def render(stdscr: curses.window, snapshot: Snapshot) -> None:
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()
    if max_y < 16 or max_x < 60:
        safe_addstr(
            stdscr,
            0,
            0,
            "Window too small. Resize to at least 60x16.",
            curses.color_pair(3) | curses.A_BOLD,
        )
        stdscr.refresh()
        return

    safe_addstr(stdscr, 0, 2, "macdash-tui", curses.color_pair(4) | curses.A_BOLD)
    safe_addstr(
        stdscr,
        0,
        max_x - 26,
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snapshot.captured_at)),
        curses.color_pair(2),
    )

    box_h = max((max_y - 4) // 2, 6)
    box_w = max((max_x - 6) // 2, 28)
    top_y = 2
    left_x = 2
    right_x = left_x + box_w + 2
    bottom_y = top_y + box_h + 1

    draw_box(stdscr, top_y, left_x, box_h, box_w, "CPU")
    draw_box(stdscr, top_y, right_x, box_h, box_w, "Memory")
    draw_box(stdscr, bottom_y, left_x, box_h, box_w, "GPU")
    draw_box(stdscr, bottom_y, right_x, box_h, box_w, "Network")

    render_cpu(stdscr, top_y, left_x, box_w, snapshot.cpu)
    render_memory(stdscr, top_y, right_x, box_w, snapshot.memory)
    render_gpu(stdscr, bottom_y, left_x, box_w, snapshot.gpu_name, snapshot.gpu_note)
    render_network(stdscr, bottom_y, right_x, box_w, snapshot.network)

    safe_addstr(
        stdscr,
        max_y - 1,
        2,
        "Press q or Ctrl-C to exit",
        curses.color_pair(2),
    )
    stdscr.refresh()


def render_cpu(stdscr: curses.window, y: int, x: int, w: int, cpu: CPUStats) -> None:
    inner_x = x + 2
    safe_addstr(
        stdscr,
        y + 2,
        inner_x,
        f"User   {cpu.user:5.1f}% {percent_bar(cpu.user, max(10, w - 20))}",
        curses.color_pair(1),
    )
    safe_addstr(
        stdscr,
        y + 3,
        inner_x,
        f"System {cpu.system:5.1f}% {percent_bar(cpu.system, max(10, w - 20))}",
        curses.color_pair(3),
    )
    safe_addstr(
        stdscr,
        y + 4,
        inner_x,
        f"Idle   {cpu.idle:5.1f}% {percent_bar(cpu.idle, max(10, w - 20))}",
        curses.color_pair(2),
    )
    safe_addstr(
        stdscr,
        y + 6,
        inner_x,
        f"Load avg: {cpu.load1:.2f}  {cpu.load5:.2f}  {cpu.load15:.2f}",
    )


def render_memory(stdscr: curses.window, y: int, x: int, w: int, mem: MemoryStats) -> None:
    total = mem.used_bytes + mem.free_bytes
    used_pct = (mem.used_bytes / total * 100.0) if total else 0.0
    inner_x = x + 2
    safe_addstr(
        stdscr,
        y + 2,
        inner_x,
        f"Used   {human_bytes(mem.used_bytes):>10} {percent_bar(used_pct, max(10, w - 24))}",
        curses.color_pair(3),
    )
    safe_addstr(stdscr, y + 3, inner_x, f"Free   {human_bytes(mem.free_bytes):>10}")
    safe_addstr(stdscr, y + 4, inner_x, f"App    {human_bytes(mem.app_bytes):>10}")
    safe_addstr(stdscr, y + 5, inner_x, f"Wired  {human_bytes(mem.wired_bytes):>10}")
    safe_addstr(
        stdscr,
        y + 6,
        inner_x,
        f"Compr. {human_bytes(mem.compressed_bytes):>10}",
    )


def render_gpu(
    stdscr: curses.window,
    y: int,
    x: int,
    w: int,
    gpu_name: str,
    gpu_note: str,
) -> None:
    inner_x = x + 2
    safe_addstr(stdscr, y + 2, inner_x, f"Adapter: {gpu_name}", curses.color_pair(1))
    safe_addstr(stdscr, y + 4, inner_x, gpu_note)
    safe_addstr(stdscr, y + 5, inner_x, "This keeps the tool dependency-free.")


def render_network(stdscr: curses.window, y: int, x: int, w: int, net: NetworkStats) -> None:
    inner_x = x + 2
    safe_addstr(stdscr, y + 2, inner_x, f"Interface: {net.interface}", curses.color_pair(1))
    safe_addstr(stdscr, y + 3, inner_x, f"RX Total:  {human_bytes(net.rx_bytes)}")
    safe_addstr(stdscr, y + 4, inner_x, f"TX Total:  {human_bytes(net.tx_bytes)}")
    safe_addstr(stdscr, y + 5, inner_x, f"RX Rate:   {human_bytes(net.rx_rate)}/s")
    safe_addstr(stdscr, y + 6, inner_x, f"TX Rate:   {human_bytes(net.tx_rate)}/s")


def init_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_MAGENTA, -1)


def run(stdscr: curses.window) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(250)
    init_colors()
    sampler = Sampler()

    while True:
        snapshot = sampler.sample()
        render(stdscr, snapshot)
        for _ in range(4):
            key = stdscr.getch()
            if key in (ord("q"), ord("Q")):
                return
            time.sleep(0.25)


def main() -> int:
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
