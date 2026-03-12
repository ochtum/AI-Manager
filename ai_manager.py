"""
AI Manager - AI CLI Process Monitor
Detects Claude Code, Codex CLI, GitHub Copilot CLI processes
and shows their status (processing / waiting for input).
"""

import ctypes
import ctypes.wintypes
import json
import math
import os
import re
import shlex
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import psutil

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REFRESH_INTERVAL_MS = 1000  # auto-refresh every 1 second
CPU_BUSY_THRESHOLD = 2.0    # percent – tree CPU above this = "processing"
IO_BUSY_THRESHOLD = 1000    # bytes – I/O delta above this = "processing"
LANDSCAPE_GEOMETRY = "1200x420"
PORTRAIT_GEOMETRY = "320x760"
MINIMIZED_GEOMETRY = "220x90"
LANDSCAPE_MIN_SIZE = (900, 320)
PORTRAIT_MIN_SIZE = (280, 460)
MINIMIZED_MIN_SIZE = (220, 90)
GEOMETRY_SAVE_DELAY_MS = 450

# Settings file – stored next to the script
_SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"

# Process identification patterns
# Each entry: (display_name, exe_patterns, cmdline_keywords, cmdline_exclude, path_keywords)
# exe_patterns:     match by process name (case-insensitive)
# cmdline_keywords: match by command-line substring (for node/npm/npx wrappers)
# cmdline_exclude:  skip if any of these appear in command line
# path_keywords:    if exe_patterns match, at least one of these must appear in
#                   the full exe path (empty list = no path restriction)
CLI_DEFINITIONS = [
    (
        "Claude Code",
        ["claude.exe", "claude"],
        ["@anthropic-ai/claude-code", "claude-code"],
        [],
        [],  # no path restriction – claude.exe is always the CLI
    ),
    (
        "Codex CLI",
        ["codex.exe", "codex"],
        ["@openai/codex"],
        ["app-server"],  # exclude VS Code extension background process
        ["@openai", "codex"],  # must be under @openai/codex path
    ),
    (
        "GitHub Copilot CLI",
        ["copilot.exe", "copilot", "github-copilot-cli.exe", "github-copilot-cli"],
        ["@github/copilot", "@githubnext/github-copilot-cli", "github-copilot-cli"],
        ["microsoft.copilot", "m365copilot"],  # exclude Windows Copilot app
        ["@github", "npm"],  # must be under @github/copilot path, not WindowsApps
    ),
]

WSL_LAUNCHER_EXE_PATTERNS = {
    "node",
    "npm",
    "npx",
    "pnpm",
    "bun",
    "bash",
    "sh",
    "env",
}

WSL_CLI_DEFINITIONS = [
    (
        "Claude Code",
        ["claude"],
        [
            "@anthropic-ai/claude-code",
            "/bin/claude",
            "/.claude/local/claude",
        ],
        [],
    ),
    (
        "Codex CLI",
        ["codex"],
        ["@openai/codex", "/bin/codex", "codex/codex"],
        ["app-server"],
    ),
    (
        "GitHub Copilot CLI",
        ["copilot", "github-copilot-cli"],
        [
            "@github/copilot",
            "@githubnext/github-copilot-cli",
            "github-copilot-cli",
            "/bin/copilot",
        ],
        ["microsoft.copilot", "m365copilot"],
    ),
]

NON_INTERACTIVE_CMDLINE_PATTERNS = (
    " mcp-server",
    " --mcp-server",
)

# Windows API constants
SW_RESTORE = 9
SW_SHOW = 5
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
GA_ROOT = 2
GA_ROOTOWNER = 3

# ---------------------------------------------------------------------------
# Win32 helpers (pure ctypes – no PowerShell)
# ---------------------------------------------------------------------------

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(
    ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
)
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
IsWindowVisible = user32.IsWindowVisible
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
SetForegroundWindow = user32.SetForegroundWindow
ShowWindow = user32.ShowWindow
IsIconic = user32.IsIconic
BringWindowToTop = user32.BringWindowToTop
GetWindow = user32.GetWindow
GetWindowLongW = user32.GetWindowLongW
GetAncestor = user32.GetAncestor
SetActiveWindow = user32.SetActiveWindow

# For AllowSetForegroundWindow workaround
AttachThreadInput = user32.AttachThreadInput
GetForegroundWindow = user32.GetForegroundWindow
GetCurrentThreadId = kernel32.GetCurrentThreadId
GetWindowThreadProcessId_full = user32.GetWindowThreadProcessId


def get_window_title(hwnd: int) -> str:
    length = GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def is_main_window(hwnd: int) -> bool:
    """Check if hwnd is a main (non-tool) window."""
    if not IsWindowVisible(hwnd):
        return False
    # Skip tool windows unless they also have WS_EX_APPWINDOW
    ex_style = GetWindowLongW(hwnd, GWL_EXSTYLE)
    if (ex_style & WS_EX_TOOLWINDOW) and not (ex_style & WS_EX_APPWINDOW):
        return False
    # Must not be owned
    if GetWindow(hwnd, GW_OWNER):
        return False
    return True


def find_windows_for_pid(pid: int) -> list[int]:
    """Return list of HWNDs belonging to *pid* that are main windows."""
    result = []

    def callback(hwnd, _lparam):
        if is_main_window(hwnd):
            proc_id = ctypes.wintypes.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value == pid:
                result.append(hwnd)
        return True

    EnumWindows(EnumWindowsProc(callback), 0)
    return result


def activate_window(hwnd: int) -> None:
    """Bring a window to the foreground.

    Uses multiple strategies to bypass Windows' foreground-lock restrictions.
    """
    targets: list[int] = []
    for flag in (GA_ROOTOWNER, GA_ROOT):
        try:
            target = int(GetAncestor(hwnd, flag))
        except Exception:
            target = 0
        if target and target not in targets:
            targets.append(target)
    if hwnd not in targets:
        targets.append(hwnd)

    # Restore the outermost window first so minimized terminal windows reappear.
    for target in targets:
        if IsIconic(target):
            ShowWindow(target, SW_RESTORE)
        else:
            ShowWindow(target, SW_SHOW)

    # Strategy: simulate an Alt key press to unlock SetForegroundWindow,
    # then call SetForegroundWindow.  This reliably bypasses the restriction
    # that only the foreground process may change the foreground window.
    VK_MENU = 0x12
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    keybd_event = user32.keybd_event

    keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)       # Alt down
    try:
        for target in targets:
            SetForegroundWindow(target)
            BringWindowToTop(target)
            try:
                SetActiveWindow(target)
            except Exception:
                pass
    finally:
        keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)  # Alt up


# ---------------------------------------------------------------------------
# Process scanning
# ---------------------------------------------------------------------------

@dataclass
class CLIProcess:
    """Represents a detected AI CLI process."""
    name: str           # e.g. "Claude Code"
    pid: int
    cpu_percent: float
    status: str         # "Processing" or "Waiting for input"
    cmdline: str
    cwd: str = ""         # working directory
    terminal_pid: Optional[int] = None
    terminal_type: str = ""   # stable label: e.g. "Windows Terminal", "WSL:Ubuntu"
    hwnds: list[int] = field(default_factory=list)


def _match_cli(proc_info: dict) -> Optional[str]:
    """Return the CLI display name if *proc_info* matches, else None."""
    exe_name = (proc_info.get("name") or "").lower()
    try:
        cmdline_parts = proc_info.get("cmdline") or []
        cmdline_lower = " ".join(cmdline_parts).lower()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        cmdline_lower = ""

    # Try to get the full exe path for path-based filtering
    exe_path_lower = ""
    if cmdline_parts:
        exe_path_lower = cmdline_parts[0].lower()

    for display_name, exe_patterns, kw_list, exclude_list, path_kw in CLI_DEFINITIONS:
        # Check exclusion patterns first
        if any(ex.lower() in cmdline_lower for ex in exclude_list):
            continue
        # Direct exe match
        if exe_name in [p.lower() for p in exe_patterns]:
            # If path_keywords specified, the exe path must contain at least one
            if path_kw and not any(pk.lower() in exe_path_lower for pk in path_kw):
                continue
            return display_name
        # node/npm/npx running one of the CLIs (check command line)
        if exe_name in ("node.exe", "node", "npm.exe", "npm", "npx.exe", "npx"):
            for kw in kw_list:
                if kw.lower() in cmdline_lower:
                    return display_name
    return None


def _match_wsl_cli(exe_name: str, cmdline: str) -> tuple[Optional[str], int]:
    """Return the WSL CLI display name and confidence score if matched."""
    exe_candidates: set[str] = set()
    exe_name_lower = (exe_name or "").lower()
    if exe_name_lower:
        exe_candidates.add(exe_name_lower)

    try:
        argv0 = shlex.split(cmdline)[0]
    except (ValueError, IndexError):
        argv0 = (cmdline or "").split(None, 1)[0] if cmdline else ""
    argv0_base = os.path.basename(argv0).lower()
    if argv0_base:
        exe_candidates.add(argv0_base)

    cmdline_lower = (cmdline or "").lower()

    for display_name, exe_patterns, kw_list, exclude_list in WSL_CLI_DEFINITIONS:
        if any(ex.lower() in cmdline_lower for ex in exclude_list):
            continue
        if any(candidate in [p.lower() for p in exe_patterns] for candidate in exe_candidates):
            return display_name, 3
        if any(candidate in WSL_LAUNCHER_EXE_PATTERNS for candidate in exe_candidates):
            for kw in kw_list:
                if kw.lower() in cmdline_lower:
                    if any(candidate in {"node", "bun"} for candidate in exe_candidates):
                        return display_name, 2
                    return display_name, 1
    return None, 0


def _is_non_interactive_cli_cmdline(cmdline: str) -> bool:
    cmd = f" {cmdline.lower()} "
    return any(pattern in cmd for pattern in NON_INTERACTIVE_CMDLINE_PATTERNS)


def _tty_sort_key(tty: str) -> tuple[int, int, str]:
    """Sort pts/N numerically before falling back to lexical ordering."""
    tty_lower = (tty or "").lower()
    if tty_lower.startswith("pts/"):
        try:
            return (0, int(tty_lower.split("/", 1)[1]), tty_lower)
        except ValueError:
            pass
    return (1, 0, tty_lower)


def _find_terminal_ancestor(proc: psutil.Process) -> Optional[psutil.Process]:
    """Walk up the process tree to find the nearest ancestor that owns a visible window."""
    try:
        parent = proc.parent()
        visited = set()
        while parent and parent.pid not in visited and parent.pid > 4:
            visited.add(parent.pid)
            # Return the first ancestor that has a visible window
            if find_windows_for_pid(parent.pid):
                return parent
            parent = parent.parent()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    return None


def _has_cli_ancestor(proc: psutil.Process, display_name: str) -> bool:
    """Return True if any ancestor (other than self) is also detected as the same CLI type."""
    try:
        parent = proc.parent()
        visited = set()
        while parent and parent.pid not in visited and parent.pid > 4:
            visited.add(parent.pid)
            try:
                pinfo = parent.as_dict(attrs=["pid", "name", "cmdline"])
                if _match_cli(pinfo) == display_name:
                    return True
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
            parent = parent.parent()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    return False


# ---------------------------------------------------------------------------
# Status detection – tree CPU + I/O delta
# ---------------------------------------------------------------------------

# Track I/O counters from previous scan: pid -> total_bytes
_prev_io: dict[int, int] = {}
_wsl_prev_cpu: dict[tuple[str, int], tuple[float, int]] = {}
_wsl_prev_io: dict[tuple[str, int], int] = {}
_wsl_clk_tck: dict[str, int] = {}


def _get_tree_io(proc: psutil.Process) -> int:
    """Return the total I/O bytes (read+write) for a process and its children."""
    total = 0
    try:
        io = proc.io_counters()
        total += io.read_bytes + io.write_bytes
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    try:
        for child in proc.children(recursive=True):
            try:
                cio = child.io_counters()
                total += cio.read_bytes + cio.write_bytes
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    return total


def _get_tree_cpu(proc: psutil.Process) -> float:
    """Return the sum of cpu_percent for a process and its children."""
    total = 0.0
    try:
        total += proc.cpu_percent(interval=None)
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    try:
        for child in proc.children(recursive=True):
            try:
                total += child.cpu_percent(interval=None)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    return total


def _detect_status(proc: psutil.Process) -> tuple[float, str]:
    """Detect whether a process is busy or waiting for input.

    Uses two signals:
      1. Tree CPU (process + children) > CPU_BUSY_THRESHOLD
      2. I/O delta since last scan > IO_BUSY_THRESHOLD

    Returns (tree_cpu, status_string).
    """
    pid = proc.pid
    tree_cpu = _get_tree_cpu(proc)
    current_io = _get_tree_io(proc)

    # Calculate I/O delta
    prev = _prev_io.get(pid)
    _prev_io[pid] = current_io
    io_delta = (current_io - prev) if prev is not None else 0

    is_busy = tree_cpu > CPU_BUSY_THRESHOLD or io_delta > IO_BUSY_THRESHOLD
    status = "Processing" if is_busy else "Waiting for input"
    return tree_cpu, status


def _get_wsl_clk_tck(distro: str) -> int:
    cached = _wsl_clk_tck.get(distro)
    if cached:
        return cached

    import subprocess

    clk_tck = 100
    try:
        out = subprocess.check_output(
            ["wsl", "-d", distro, "--", "getconf", "CLK_TCK"],
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace").strip()
        clk_tck = max(int(out), 1)
    except Exception:
        pass

    _wsl_clk_tck[distro] = clk_tck
    return clk_tck


def _detect_wsl_status(
    distro: str,
    pid: int,
    fallback_cpu: float,
    proc_ticks: Optional[int],
    io_total: Optional[int],
) -> tuple[float, str]:
    key = (distro, pid)
    cpu_percent = fallback_cpu

    if proc_ticks is not None:
        prev = _wsl_prev_cpu.get(key)
        now = time.monotonic()
        _wsl_prev_cpu[key] = (now, proc_ticks)
        if prev is not None:
            prev_ts, prev_ticks = prev
            elapsed = now - prev_ts
            delta_ticks = proc_ticks - prev_ticks
            if elapsed > 0 and delta_ticks >= 0:
                clk_tck = _get_wsl_clk_tck(distro)
                cpu_percent = max(0.0, (delta_ticks / clk_tck) / elapsed * 100.0)

    io_delta = 0
    if io_total is not None:
        prev_io = _wsl_prev_io.get(key)
        _wsl_prev_io[key] = io_total
        if prev_io is not None and io_total >= prev_io:
            io_delta = io_total - prev_io

    status = (
        "Processing"
        if cpu_percent > CPU_BUSY_THRESHOLD or io_delta > IO_BUSY_THRESHOLD
        else "Waiting for input"
    )
    return cpu_percent, status


def _get_wsl_proc_details(
    distro: str,
    pids: list[int],
) -> dict[int, tuple[str, Optional[int], Optional[int]]]:
    import subprocess

    if not pids:
        return {}

    py_code = (
        "import os, sys\n"
        "for raw_pid in sys.argv[1:]:\n"
        "    pid = int(raw_pid)\n"
        "    cwd = ''\n"
        "    try:\n"
        "        cwd = os.readlink(f'/proc/{pid}/cwd')\n"
        "    except Exception:\n"
        "        pass\n"
        "    ticks = ''\n"
        "    try:\n"
        "        with open(f'/proc/{pid}/stat', 'r', encoding='utf-8', errors='replace') as fh:\n"
        "            stat_line = fh.read().strip()\n"
        "        after_comm = stat_line[stat_line.rfind(')') + 2:].split()\n"
        "        ticks = str(int(after_comm[11]) + int(after_comm[12]))\n"
        "    except Exception:\n"
        "        pass\n"
        "    io_total = ''\n"
        "    try:\n"
        "        values = {}\n"
        "        with open(f'/proc/{pid}/io', 'r', encoding='utf-8', errors='replace') as fh:\n"
        "            for line in fh:\n"
        "                key, value = line.split(':', 1)\n"
        "                values[key.strip()] = int(value.strip() or 0)\n"
        "        io_total = str(\n"
        "            values.get('rchar', 0)\n"
        "            + values.get('wchar', 0)\n"
        "            + values.get('read_bytes', 0)\n"
        "            + values.get('write_bytes', 0)\n"
        "        )\n"
        "    except Exception:\n"
        "        pass\n"
        "    print(f'{pid}\\t{cwd}\\t{ticks}\\t{io_total}')\n"
    )

    details: dict[int, tuple[str, Optional[int], Optional[int]]] = {}
    try:
        out = subprocess.check_output(
            ["wsl", "-d", distro, "--", "python3", "-c", py_code, *[str(pid) for pid in pids]],
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace")
    except Exception:
        return details

    for line in out.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        pid_str, cwd, ticks_str, io_str = parts
        try:
            pid = int(pid_str.strip())
        except ValueError:
            continue
        try:
            ticks = int(ticks_str) if ticks_str.strip() else None
        except ValueError:
            ticks = None
        try:
            io_total = int(io_str) if io_str.strip() else None
        except ValueError:
            io_total = None
        details[pid] = (cwd.strip(), ticks, io_total)
    return details


def _get_console_hwnd_for_pid(pid: int) -> Optional[int]:
    """Get the console window HWND for a process using AttachConsole.

    Temporarily attaches to the target process's console to retrieve
    its console window handle.  This reliably maps a process to its
    specific terminal tab, even when multiple tabs share the same
    terminal emulator.
    """
    try:
        kernel32.FreeConsole()
        if kernel32.AttachConsole(pid):
            hwnd = kernel32.GetConsoleWindow()
            kernel32.FreeConsole()
            # Re-attach to our own parent console
            kernel32.AttachConsole(-1)  # ATTACH_PARENT_PROCESS
            if hwnd:
                return hwnd
        else:
            # Re-attach to our own parent console
            kernel32.AttachConsole(-1)
    except Exception:
        try:
            kernel32.AttachConsole(-1)
        except Exception:
            pass
    return None


# Cache: PID -> console HWND (cleared when PID no longer exists)
_hwnd_cache: dict[int, int] = {}


def _resolve_hwnds(procs: list[CLIProcess]) -> None:
    """Resolve the correct console HWND for each Windows process in-place.

    Uses AttachConsole/GetConsoleWindow to get the exact HWND for each
    process's terminal tab, replacing the broad list of all terminal HWNDs.
    Results are cached by PID for subsequent scans.
    """
    # Clean stale cache entries
    live_pids = {p.pid for p in procs}
    for pid in list(_hwnd_cache):
        if pid not in live_pids:
            del _hwnd_cache[pid]

    for p in procs:
        # WSL entries already have tab HWNDs resolved separately.
        if "(WSL:" in p.name:
            if p.hwnds:
                p.hwnds = p.hwnds[:1]
            continue

        if p.pid in _hwnd_cache:
            p.hwnds = [_hwnd_cache[p.pid]]
            continue

        hwnd = _get_console_hwnd_for_pid(p.pid)
        if not hwnd and p.terminal_pid:
            hwnd = _get_console_hwnd_for_pid(p.terminal_pid)
        if not hwnd:
            try:
                ppid = psutil.Process(p.pid).ppid()
                if ppid:
                    hwnd = _get_console_hwnd_for_pid(ppid)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

        if hwnd:
            _hwnd_cache[p.pid] = hwnd
            p.hwnds = [hwnd]
        elif p.hwnds:
            p.hwnds = p.hwnds[:1]


def _find_wsl_tab_hwnds() -> list[tuple[int, float]]:
    """Find interactive WSL tab host processes and their console HWNDs.

    Returns a list of (hwnd, create_time) sorted by creation time (oldest first).
    Each entry corresponds to one WSL terminal tab/window host.
    """
    import subprocess

    # Interactive WSL tabs are typically: cmd.exe -> wsl.exe -> wsl.exe
    # or directly: wsl.exe (interactive, no --exec).
    # We look for wsl.exe processes whose cmdline is just "wsl.exe -d <distro>"
    # (no --exec) and find the topmost Windows host process with a console.
    wsl_procs: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (proc.info["name"] or "").lower() != "wsl.exe":
                continue
            cmdline = proc.info.get("cmdline") or []
            cmd_lower = " ".join(cmdline).lower()
            # Skip non-interactive (--exec) WSL processes
            if "--exec" in cmd_lower or "--cd" in cmd_lower:
                continue
            # Only consider wsl.exe whose parent is cmd.exe or a terminal
            # (not another wsl.exe – those are children)
            try:
                parent = proc.parent()
                if parent and (parent.name() or "").lower() == "wsl.exe":
                    continue  # child wsl.exe, skip
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
            wsl_procs.append(proc)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue

    # Get console HWND for each via their parent cmd.exe (or themselves)
    tab_hwnds: list[tuple[int, float]] = []
    for wp in wsl_procs:
        try:
            # Try parent first (cmd.exe that hosts the WSL session)
            target_pid = wp.pid
            host_proc = wp
            try:
                parent = wp.parent()
                if parent and (parent.name() or "").lower() == "cmd.exe":
                    target_pid = parent.pid
                    host_proc = parent
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

            hwnd = _get_console_hwnd_for_pid(target_pid)
            if hwnd:
                # Use the window host process creation time (typically cmd.exe).
                # Using child wsl.exe creation time can reorder tabs incorrectly
                # when child processes are recreated.
                ctime = host_proc.create_time()
                tab_hwnds.append((hwnd, ctime))
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue

    # Sort by creation time (oldest first -> lowest pts number)
    tab_hwnds.sort(key=lambda x: x[1])
    return tab_hwnds


def _scan_wsl_processes() -> list[CLIProcess]:
    """Scan WSL distributions for AI CLI processes."""
    import subprocess

    results: list[CLIProcess] = []
    live_keys: set[tuple[str, int]] = set()

    # Identify running WSL distributions
    try:
        out = subprocess.check_output(
            ["wsl", "--list", "--running", "--quiet"],
            timeout=5, stderr=subprocess.DEVNULL,
        )
        try:
            distros = out.decode("utf-16-le").strip().split("\n")
        except UnicodeDecodeError:
            distros = out.decode("utf-8", errors="replace").strip().split("\n")
        distros = [d.strip().strip("\x00") for d in distros if d.strip().strip("\x00")]
    except Exception:
        return results

    # Get console HWNDs for WSL tabs, sorted by creation time
    tab_hwnds = _find_wsl_tab_hwnds()

    for distro in distros:
        try:
            ps_out = subprocess.check_output(
                [
                    "wsl",
                    "-d",
                    distro,
                    "--",
                    "ps",
                    "-eo",
                    "pid=,ppid=,pcpu=,etimes=,tty=,comm=,args=",
                    "-ww",
                ],
                timeout=5, stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="replace")
        except Exception:
            continue

        # Per-tty "age" proxy (seconds): larger means the tty has older processes.
        # This is more reliable than raw pts numbering when pts values were reused.
        tty_age_seconds: dict[str, int] = {}

        # First pass: pick the best-matching process for each CLI/TTY pair.
        best_by_tty: dict[tuple[str, str], tuple[tuple[int, float, int], CLIProcess]] = {}

        for line in ps_out.splitlines():
            parts = line.split(None, 6)
            if len(parts) < 7:
                continue

            try:
                wsl_pid = int(parts[0])
                cpu = float(parts[2])
                elapsed = int(parts[3])
            except ValueError:
                continue

            tty = parts[4]
            exe_name = parts[5]
            cmdline_str = parts[6]

            if tty and tty != "?":
                previous = tty_age_seconds.get(tty, -1)
                if elapsed > previous:
                    tty_age_seconds[tty] = elapsed

            if _is_non_interactive_cli_cmdline(cmdline_str):
                continue

            display_name, match_score = _match_wsl_cli(exe_name, cmdline_str)
            if display_name is None:
                continue

            status = "Processing" if cpu > CPU_BUSY_THRESHOLD else "Waiting for input"
            proc_entry = CLIProcess(
                name=f"{display_name} (WSL:{distro})",
                pid=wsl_pid,
                cpu_percent=cpu,
                status=status,
                cmdline=cmdline_str,
                cwd="",  # resolved later in batch
                terminal_pid=None,
                terminal_type=f"WSL:{distro} ({tty})",
                hwnds=[],
            )

            key = (display_name, tty)
            rank = (match_score, cpu, wsl_pid)
            current = best_by_tty.get(key)
            if current is None or rank > current[0]:
                best_by_tty[key] = (rank, proc_entry)

        tty_procs = [
            (tty, proc_entry)
            for (_display_name, tty), (_rank, proc_entry) in best_by_tty.items()
        ]
        pids_to_resolve = [proc_entry.pid for _tty, proc_entry in tty_procs]

        proc_details = _get_wsl_proc_details(distro, pids_to_resolve)

        for _tty, proc_entry in tty_procs:
            cwd, ticks, io_total = proc_details.get(proc_entry.pid, ("", None, None))
            proc_entry.cwd = cwd
            proc_entry.cpu_percent, proc_entry.status = _detect_wsl_status(
                distro,
                proc_entry.pid,
                proc_entry.cpu_percent,
                ticks,
                io_total,
            )

        # Sort by inferred tty age (oldest first), then tty as a stable fallback.
        # tab_hwnds are also sorted oldest first by host-process creation time.
        tty_procs.sort(
            key=lambda x: (-tty_age_seconds.get(x[0], -1), _tty_sort_key(x[0]))
        )

        # Assign HWNDs: both sides are sorted oldest first.
        for i, (tty, proc_entry) in enumerate(tty_procs):
            live_keys.add((distro, proc_entry.pid))
            if i < len(tab_hwnds):
                proc_entry.hwnds = [tab_hwnds[i][0]]
            results.append(proc_entry)

    for key in list(_wsl_prev_cpu):
        if key not in live_keys:
            del _wsl_prev_cpu[key]
    for key in list(_wsl_prev_io):
        if key not in live_keys:
            del _wsl_prev_io[key]

    return results


def scan_processes() -> list[CLIProcess]:
    """Scan all running processes and return detected AI CLI processes."""
    results: list[CLIProcess] = []
    seen_pids: set[int] = set()

    # --- Windows processes ---
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
            cmdline_str = " ".join(info.get("cmdline") or [])
            if _is_non_interactive_cli_cmdline(cmdline_str):
                continue
            display_name = _match_cli(info)
            if display_name is None:
                continue
            pid = info["pid"]
            if pid in seen_pids:
                continue
            seen_pids.add(pid)

            # Skip child processes whose parent is already the same CLI type
            if _has_cli_ancestor(proc, display_name):
                continue

            # Determine status using tree CPU + I/O delta
            cpu, status = _detect_status(proc)

            # Get working directory
            try:
                cwd = proc.cwd()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                cwd = ""

            # Find terminal window
            terminal = _find_terminal_ancestor(proc)
            terminal_pid = terminal.pid if terminal else None
            terminal_name = (terminal.name() or "").lower() if terminal else ""
            own_hwnds = find_windows_for_pid(pid)

            # Skip the Claude desktop app: it is also claude.exe, but owns its
            # own top-level window and is typically launched by explorer.exe,
            # not a terminal host.
            if (
                display_name == "Claude Code"
                and (info.get("name") or "").lower() == "claude.exe"
                and terminal_name == "explorer.exe"
            ):
                continue

            # Determine stable terminal type label
            terminal_type = ""
            if terminal:
                tname = (terminal.name() or "").lower()
                _terminal_labels = {
                    "windowsterminal.exe": "Windows Terminal",
                    "wt.exe": "Windows Terminal",
                    "cmd.exe": "Command Prompt",
                    "powershell.exe": "PowerShell",
                    "pwsh.exe": "PowerShell",
                    "mintty.exe": "MinTTY",
                    "alacritty.exe": "Alacritty",
                    "wezterm-gui.exe": "WezTerm",
                    "conhost.exe": "Console",
                }
                terminal_type = _terminal_labels.get(tname, terminal.name())

            # Collect HWNDs – prefer terminal window, fall back to own window
            all_hwnds: list[int] = []
            if terminal_pid:
                all_hwnds = find_windows_for_pid(terminal_pid)
            if not all_hwnds:
                all_hwnds = own_hwnds
            if not all_hwnds:
                try:
                    ppid = proc.ppid()
                    if ppid:
                        all_hwnds = find_windows_for_pid(ppid)
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass

            # Hide non-interactive/background CLI processes (e.g. MCP servers)
            # when there is no activatable window.
            if not all_hwnds:
                continue

            results.append(CLIProcess(
                name=display_name,
                pid=pid,
                cpu_percent=cpu,
                status=status,
                cmdline=cmdline_str,
                cwd=cwd,
                terminal_pid=terminal_pid,
                terminal_type=terminal_type,
                hwnds=all_hwnds,  # will be narrowed by _assign_best_hwnds
            ))
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue

    # --- WSL processes ---
    try:
        results.extend(_scan_wsl_processes())
    except Exception:
        pass

    # Resolve exact console HWNDs for each process
    _resolve_hwnds(results)

    # Clean stale _prev_io entries for PIDs that no longer exist
    live_pids = {p.pid for p in results}
    for stale_pid in list(_prev_io):
        if stale_pid not in live_pids:
            del _prev_io[stale_pid]

    return results


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class AIManagerApp:
    BG = "#1e1e2e"
    FG = "#cdd6f4"
    HEADING_BG = "#313244"
    MUTED_FG = "#a6adc8"
    SUBTLE_FG = "#6c7086"
    SELECT_BG = "#45475a"
    CARD_BG = "#24273a"
    CHIP_BG = "#181825"
    UNLABELED_CHIP_BG = "#25283d"
    UNLABELED_CHIP_ACTIVE_BG = "#353a57"
    LABEL_PRESETS = [
        ("Red", "#f38ba8"),
        ("Blue", "#89b4fa"),
        ("Green", "#a6e3a1"),
        ("Yellow", "#f9e2af"),
        ("Purple", "#cba6f7"),
    ]
    LABEL_FONT_CANDIDATES = (
        "Yu Gothic UI",
        "Meiryo UI",
        "Meiryo",
        "MS UI Gothic",
        "Segoe UI",
    )
    CLI_VISUALS = {
        "Claude Code": {
            "id": "claude",
            "icon": "●",
            "accent": "#89b4fa",
            "header_bg": "#25344f",
        },
        "Codex CLI": {
            "id": "codex",
            "icon": "◆",
            "accent": "#f9e2af",
            "header_bg": "#4b4030",
        },
        "GitHub Copilot CLI": {
            "id": "copilot",
            "icon": "▲",
            "accent": "#a6e3a1",
            "header_bg": "#2a4131",
        },
    }

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AI Manager – AI CLI Process Monitor")
        self.root.geometry(LANDSCAPE_GEOMETRY)
        self.root.minsize(*LANDSCAPE_MIN_SIZE)
        self.root.configure(bg=self.BG)

        self._processes: list[CLIProcess] = []
        self._process_lookup: dict[int, CLIProcess] = {}
        self._scanning = False
        self._settings = self._load_settings()
        self._window_geometries = self._load_window_geometries(
            self._settings.get("window_geometries")
        )
        self._process_labels = self._load_process_labels(
            self._settings.get("process_labels")
        )
        self._card_widgets: dict[int, dict[str, object]] = {}
        self._tree_cli_widgets: dict[int, tk.Label] = {}
        self._tree_label_widgets: dict[int, tk.Button] = {}
        self._portrait_canvas_window: Optional[int] = None
        self._geometry_save_job: Optional[str] = None
        self._tree_label_layout_job: Optional[str] = None
        self._suspend_geometry_tracking = False
        self._window_mode = "normal"
        self._restore_layout_after_minimize: Optional[str] = None

        # Always-on-top state (restored from settings)
        self._topmost_var = tk.BooleanVar(
            value=bool(self._settings.get("always_on_top", False))
        )
        layout_mode = str(self._settings.get("layout_mode", "landscape")).lower()
        if layout_mode not in {"landscape", "portrait"}:
            layout_mode = "landscape"
        self._layout_var = tk.StringVar(value=layout_mode)
        self._current_layout = layout_mode
        self._label_font_family = self._select_font_family(self.LABEL_FONT_CANDIDATES)

        self._build_ui()
        self._apply_layout(initial=True)
        self._apply_topmost()
        self._schedule_refresh()

    # ---- UI construction ----

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Treeview",
                        background=self.BG, foreground=self.FG, fieldbackground=self.BG,
                        rowheight=32, font=("Segoe UI", 10))
        style.configure("Treeview.Heading",
                        background=self.HEADING_BG, foreground=self.FG,
                        font=("Segoe UI", 10, "bold"))
        style.map("Treeview",
                  background=[("selected", self.SELECT_BG)],
                  foreground=[("selected", "#ffffff")])

        style.configure("Accent.TButton", font=("Segoe UI", 8), padding=(7, 2))
        self.header = tk.Frame(self.root, bg=self.HEADING_BG, padx=16, pady=10)
        self.header.pack(fill=tk.X)
        self.header.grid_columnconfigure(0, weight=1)
        self.header.grid_columnconfigure(1, weight=0)
        self.header.grid_columnconfigure(2, weight=0)

        self.title_label = tk.Label(
            self.header,
            text="AI Manager",
            font=("Segoe UI", 14, "bold"),
            bg=self.HEADING_BG,
            fg="#cba6f7",
        )

        self.status_label = tk.Label(
            self.header,
            text="",
            font=("Segoe UI", 8),
            bg=self.HEADING_BG,
            fg=self.MUTED_FG,
        )

        self.controls_frame = tk.Frame(self.header, bg=self.HEADING_BG)
        self.actions_frame = tk.Frame(self.controls_frame, bg=self.HEADING_BG)
        self.actions_frame.pack(side=tk.LEFT)

        refresh_btn = ttk.Button(
            self.actions_frame,
            text="Refresh",
            style="Accent.TButton",
            command=self._manual_refresh,
        )
        refresh_btn.pack(side=tk.LEFT)

        self.layout_btn = ttk.Button(
            self.actions_frame,
            text="Portrait",
            style="Accent.TButton",
            command=self._toggle_layout,
        )
        self.layout_btn.pack(side=tk.LEFT, padx=(4, 0))

        self.minimize_btn = ttk.Button(
            self.actions_frame,
            text="Minimize",
            style="Accent.TButton",
            command=self._enter_minimized_mode,
        )
        self.minimize_btn.pack(side=tk.LEFT, padx=(4, 0))

        topmost_cb = tk.Checkbutton(
            self.actions_frame,
            text="Top",
            variable=self._topmost_var, command=self._on_topmost_toggle,
            bg=self.HEADING_BG, fg=self.FG, selectcolor=self.SELECT_BG,
            activebackground=self.HEADING_BG, activeforeground=self.FG,
            font=("Segoe UI", 8),
            padx=0,
            pady=0,
            bd=0,
            highlightthickness=0,
        )
        topmost_cb.pack(side=tk.LEFT, padx=(4, 0))

        # Treeview
        columns = ("cli", "pid", "status", "cpu", "label", "cwd", "terminal")
        self.content_frame = tk.Frame(self.root, bg=self.BG)
        self.content_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tree_frame = tk.Frame(self.content_frame, bg=self.BG)

        self.tree = ttk.Treeview(self.tree_frame, columns=columns, show="headings",
                                 selectmode="browse")
        self.tree.heading("cli", text="AI CLI")
        self.tree.heading("pid", text="PID")
        self.tree.heading("status", text="Status")
        self.tree.heading("cpu", text="CPU %")
        self.tree.heading("label", text="Label")
        self.tree.heading("cwd", text="Working Directory")
        self.tree.heading("terminal", text="Terminal")

        self.tree.column("cli", width=190, minwidth=150)
        self.tree.column("pid", width=80, minwidth=60, anchor=tk.CENTER)
        self.tree.column("status", width=160, minwidth=120)
        self.tree.column("cpu", width=80, minwidth=60, anchor=tk.CENTER)
        self.tree.column("label", width=140, minwidth=110)
        self.tree.column("cwd", width=300, minwidth=150)
        self.tree.column("terminal", width=300, minwidth=150)

        self.tree_scrollbar = ttk.Scrollbar(
            self.tree_frame,
            orient=tk.VERTICAL,
            command=self._on_tree_scroll,
        )
        self.tree.configure(yscrollcommand=self._on_tree_yview)
        self.tree_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self._tree_cli_column_id = f"#{columns.index('cli') + 1}"
        self._tree_label_column_id = f"#{columns.index('label') + 1}"

        # Row colors by status
        self.tree.tag_configure("waiting", background="#1a3a2a", foreground=self.FG)
        self.tree.tag_configure("processing", background="#3a1a1a", foreground=self.FG)

        # Double-click / Enter to activate window
        self.tree.bind("<Double-1>", self._on_tree_activate)
        self.tree.bind("<Return>", self._on_tree_activate)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_click, add="+")
        self.tree.bind("<Configure>", self._on_tree_geometry_change, add="+")
        self.tree.bind("<MouseWheel>", self._on_tree_geometry_change, add="+")
        self.tree.bind("<Button-4>", self._on_tree_geometry_change, add="+")
        self.tree.bind("<Button-5>", self._on_tree_geometry_change, add="+")

        self.portrait_frame = tk.Frame(self.content_frame, bg=self.BG)
        self.portrait_canvas = tk.Canvas(
            self.portrait_frame,
            bg=self.BG,
            highlightthickness=0,
            bd=0,
        )
        self.portrait_scrollbar = ttk.Scrollbar(
            self.portrait_frame,
            orient=tk.VERTICAL,
            command=self.portrait_canvas.yview,
        )
        self.portrait_canvas.configure(yscrollcommand=self.portrait_scrollbar.set)
        self.portrait_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.portrait_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.portrait_cards_frame = tk.Frame(self.portrait_canvas, bg=self.BG)
        self._portrait_canvas_window = self.portrait_canvas.create_window(
            (0, 0),
            window=self.portrait_cards_frame,
            anchor="nw",
        )
        self.portrait_cards_frame.bind(
            "<Configure>",
            self._on_portrait_content_configure,
        )
        self.portrait_canvas.bind(
            "<Configure>",
            self._on_portrait_canvas_configure,
        )
        self.portrait_frame.bind("<Enter>", self._on_portrait_hover, add="+")
        self.portrait_canvas.bind("<Enter>", self._on_portrait_hover, add="+")
        self.portrait_cards_frame.bind("<Enter>", self._on_portrait_hover, add="+")

        # Footer hint
        self.hint_label = tk.Label(
            self.root,
            text="Double-click a process row or card, or press Enter on a selected row, to activate the process window",
            font=("Segoe UI", 7),
            bg=self.BG,
            fg=self.SUBTLE_FG,
        )
        self.hint_label.pack(side=tk.BOTTOM, pady=(0, 6))

        self.minimized_frame = tk.Frame(self.root, bg=self.BG)
        self.restore_btn = ttk.Button(
            self.minimized_frame,
            text="Restore",
            style="Accent.TButton",
            command=self._restore_from_minimized,
        )
        self.restore_btn.pack(expand=True, ipadx=6, ipady=4)

        self.root.bind("<Configure>", self._on_root_configure)
        self.root.bind_all("<MouseWheel>", self._on_global_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_global_mousewheel_linux, add="+")
        self.root.bind_all("<Button-5>", self._on_global_mousewheel_linux, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- Always-on-top ----

    @staticmethod
    def _load_settings() -> dict:
        try:
            data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _write_settings(data: dict) -> None:
        try:
            _SETTINGS_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    @staticmethod
    def _load_window_geometries(data) -> dict[str, str]:
        if not isinstance(data, dict):
            return {}
        result: dict[str, str] = {}
        for layout in ("landscape", "portrait", "minimized"):
            geometry = data.get(layout)
            if isinstance(geometry, str) and AIManagerApp._is_valid_geometry(geometry):
                result[layout] = geometry
        return result

    @staticmethod
    def _is_valid_geometry(geometry: str) -> bool:
        return bool(re.match(r"^\d+x\d+[+-]\d+[+-]\d+$", geometry))

    @staticmethod
    def _geometry_size(geometry: str) -> tuple[int, int]:
        size = geometry.split("+", 1)[0].split("-", 1)[0]
        width_str, height_str = size.split("x", 1)
        return int(width_str), int(height_str)

    @staticmethod
    def _normalize_directory_key(directory: str) -> str:
        return directory.strip()

    @classmethod
    def _load_process_labels(cls, data) -> dict[str, dict[str, str]]:
        if not isinstance(data, dict):
            return {}
        result: dict[str, dict[str, str]] = {}
        for raw_directory, raw_label in data.items():
            if not isinstance(raw_directory, str) or not isinstance(raw_label, dict):
                continue
            directory = cls._normalize_directory_key(raw_directory)
            name = raw_label.get("name")
            color = raw_label.get("color")
            if not directory or not isinstance(name, str) or not isinstance(color, str):
                continue
            name = name.strip()
            color = color.strip()
            if not name or not color:
                continue
            try:
                cls._color_to_hex(color)
            except ValueError:
                continue
            result[directory] = {"name": name, "color": color}
        return result

    def _persist_process_labels(self) -> None:
        if self._process_labels:
            self._settings["process_labels"] = dict(self._process_labels)
        else:
            self._settings.pop("process_labels", None)
        self._write_settings(self._settings)

    def _select_font_family(self, candidates: tuple[str, ...]) -> str:
        available = {family.lower() for family in tkfont.families(self.root)}
        for family in candidates:
            if family.lower() in available:
                return family
        return "TkDefaultFont"

    def _retint_card_background(self, widget: tk.Widget, target_bg: str) -> None:
        try:
            current_bg = str(widget.cget("bg")).lower()
            if current_bg in {
                self.CARD_BG.lower(),
                "#1a3a2a",  # waiting (current)
                "#3a1a1a",  # processing (current)
                "#1f2a24",  # waiting (legacy)
                "#2b2029",  # processing (legacy)
            }:
                widget.config(bg=target_bg)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._retint_card_background(child, target_bg)

    @staticmethod
    def _hex_to_rgb(color: str) -> tuple[int, int, int]:
        return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))

    @staticmethod
    def _rgb_to_hex(r: int, g: int, b: int) -> str:
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _parse_hex_color(color_text: str) -> str:
        hex_value = color_text.strip().lstrip("#")
        if len(hex_value) in {3, 4}:
            hex_value = "".join(ch * 2 for ch in hex_value[:3])
        elif len(hex_value) in {6, 8}:
            hex_value = hex_value[:6]
        else:
            raise ValueError("Hex colors must use #RGB or #RRGGBB.")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", hex_value):
            raise ValueError("Hex colors must only contain 0-9 or A-F.")
        return f"#{hex_value.lower()}"

    @staticmethod
    def _parse_rgb_component(component: str) -> int:
        value = component.strip()
        if not value:
            raise ValueError("RGB colors must include three components.")
        if value.endswith("%"):
            percent = float(value[:-1])
            return max(0, min(255, round(percent * 255 / 100)))
        number = float(value)
        if not 0 <= number <= 255:
            raise ValueError("RGB components must be between 0 and 255.")
        return round(number)

    @classmethod
    def _parse_rgb_color(cls, color_text: str) -> str:
        body = color_text[color_text.find("(") + 1:color_text.rfind(")")]
        body = body.split("/", 1)[0].replace(",", " ")
        parts = [part for part in body.split() if part]
        if len(parts) != 3:
            raise ValueError("rgb() must include three components.")
        rgb = [cls._parse_rgb_component(part) for part in parts]
        return cls._rgb_to_hex(*rgb)

    @staticmethod
    def _parse_angle(value: str) -> float:
        angle = value.strip().lower()
        for suffix, factor in (
            ("deg", 1.0),
            ("grad", 0.9),
            ("rad", 180.0 / math.pi),
            ("turn", 360.0),
        ):
            if angle.endswith(suffix):
                return float(angle[:-len(suffix)]) * factor
        return float(angle)

    @staticmethod
    def _linear_to_srgb(value: float) -> float:
        value = max(0.0, min(1.0, value))
        if value <= 0.0031308:
            return 12.92 * value
        return 1.055 * (value ** (1 / 2.4)) - 0.055

    @classmethod
    def _oklch_to_hex(cls, lightness: float, chroma: float, hue: float) -> str:
        hue_rad = math.radians(hue)
        a = chroma * math.cos(hue_rad)
        b = chroma * math.sin(hue_rad)

        l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
        m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
        s_ = lightness - 0.0894841775 * a - 1.2914855480 * b

        l = l_ ** 3
        m = m_ ** 3
        s = s_ ** 3

        red_linear = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
        green_linear = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
        blue_linear = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

        rgb = [
            round(cls._linear_to_srgb(channel) * 255)
            for channel in (red_linear, green_linear, blue_linear)
        ]
        return cls._rgb_to_hex(*rgb)

    @classmethod
    def _parse_okclh_color(cls, color_text: str) -> str:
        body = color_text[color_text.find("(") + 1:color_text.rfind(")")]
        body = body.split("/", 1)[0].replace(",", " ")
        parts = [part for part in body.split() if part]
        if len(parts) != 3:
            raise ValueError("okclh() must include lightness, chroma, and hue.")

        lightness_raw = parts[0].strip().lower()
        if lightness_raw.endswith("%"):
            lightness = float(lightness_raw[:-1]) / 100
        else:
            lightness = float(lightness_raw)
            if 1 < lightness <= 100:
                lightness /= 100
        if not 0 <= lightness <= 1:
            raise ValueError("okclh lightness must be between 0 and 1.")

        chroma_raw = parts[1].strip().lower()
        chroma = float(chroma_raw[:-1]) / 100 if chroma_raw.endswith("%") else float(chroma_raw)
        if chroma < 0:
            raise ValueError("okclh chroma must be 0 or greater.")

        hue = cls._parse_angle(parts[2]) % 360
        return cls._oklch_to_hex(lightness, chroma, hue)

    @classmethod
    def _color_to_hex(cls, color_text: str) -> str:
        color = color_text.strip()
        if not color:
            raise ValueError("Enter a color.")
        lowered = color.lower()
        if lowered.startswith("#"):
            return cls._parse_hex_color(color)
        if lowered.startswith("rgb(") and lowered.endswith(")"):
            return cls._parse_rgb_color(color)
        if lowered.startswith("okclh(") and lowered.endswith(")"):
            return cls._parse_okclh_color(color)
        raise ValueError("Use #hex, rgb(), or okclh().")

    @classmethod
    def _label_text_color(cls, color_text: str) -> str:
        red, green, blue = cls._hex_to_rgb(color_text)
        luma = (0.299 * red) + (0.587 * green) + (0.114 * blue)
        return "#11111b" if luma > 170 else "#f9fafb"

    def _label_for_directory(self, directory: str) -> Optional[dict[str, str]]:
        key = self._normalize_directory_key(directory)
        if not key:
            return None
        return self._process_labels.get(key)

    def _set_process_label(self, directory: str, name: str, color: str) -> None:
        key = self._normalize_directory_key(directory)
        if not key:
            return
        self._process_labels[key] = {"name": name.strip(), "color": color.strip()}
        self._persist_process_labels()

    def _remove_process_label(self, directory: str) -> None:
        key = self._normalize_directory_key(directory)
        if not key:
            return
        if key in self._process_labels:
            del self._process_labels[key]
            self._persist_process_labels()

    def _save_setting(self, key: str, value) -> None:
        if self._settings.get(key) == value:
            return
        self._settings[key] = value
        self._write_settings(self._settings)

    def _window_is_normal(self) -> bool:
        try:
            return self.root.state() == "normal"
        except tk.TclError:
            return False

    def _current_geometry_string(self) -> Optional[str]:
        if not self._window_is_normal():
            return None
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        if width <= 1 or height <= 1:
            return None
        if x <= -32000 or y <= -32000:
            return None
        return f"{width}x{height}{x:+d}{y:+d}"

    def _geometry_key_for_current_view(self) -> str:
        return "minimized" if self._window_mode == "minimized" else self._current_layout

    def _default_window_geometry(
        self,
        layout: str,
        preserve_position: bool = False,
    ) -> str:
        if layout == "landscape":
            geometry = LANDSCAPE_GEOMETRY
        elif layout == "portrait":
            geometry = PORTRAIT_GEOMETRY
        elif layout == "minimized":
            geometry = MINIMIZED_GEOMETRY
        else:
            raise ValueError(f"Unknown layout: {layout}")
        if not preserve_position:
            return geometry
        try:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
        except tk.TclError:
            return geometry
        if x <= -32000 or y <= -32000:
            return geometry
        width, height = self._geometry_size(geometry)
        return f"{width}x{height}{x:+d}{y:+d}"

    def _restore_window_geometry(self, layout: str, preserve_position: bool = False) -> None:
        geometry = self._window_geometries.get(layout)
        if geometry is None:
            geometry = self._default_window_geometry(
                layout,
                preserve_position=preserve_position,
            )
        self.root.geometry(geometry)

    def _save_window_geometry(self, layout: Optional[str] = None) -> None:
        target_layout = layout or self._geometry_key_for_current_view()
        geometry = self._current_geometry_string()
        if geometry is None:
            return
        if self._window_geometries.get(target_layout) == geometry:
            return
        self._window_geometries[target_layout] = geometry
        self._settings["window_geometries"] = dict(self._window_geometries)
        self._write_settings(self._settings)

    def _schedule_geometry_save(self) -> None:
        if self._suspend_geometry_tracking or not self._window_is_normal():
            return
        if self._geometry_save_job is not None:
            self.root.after_cancel(self._geometry_save_job)
        self._geometry_save_job = self.root.after(
            GEOMETRY_SAVE_DELAY_MS,
            self._flush_geometry_save,
        )

    def _flush_geometry_save(self) -> None:
        self._geometry_save_job = None
        self._save_window_geometry()

    def _apply_topmost(self) -> None:
        self.root.attributes("-topmost", self._topmost_var.get())

    def _on_topmost_toggle(self) -> None:
        self._apply_topmost()
        self._save_setting("always_on_top", self._topmost_var.get())

    # ---- Layout ----

    def _on_layout_change(self) -> None:
        target_layout = self._layout_var.get()
        if target_layout == self._current_layout:
            return
        if self._geometry_save_job is not None:
            self.root.after_cancel(self._geometry_save_job)
            self._geometry_save_job = None
        self._save_window_geometry(self._current_layout)
        self._apply_layout()
        self._save_setting("layout_mode", target_layout)

    def _toggle_layout(self) -> None:
        self._layout_var.set(
            "landscape" if self._layout_var.get() == "portrait" else "portrait"
        )
        self._on_layout_change()

    def _apply_layout(self, initial: bool = False) -> None:
        layout = self._layout_var.get()
        for widget in (self.title_label, self.controls_frame, self.status_label):
            widget.grid_forget()

        self._suspend_geometry_tracking = True
        if layout == "portrait":
            self.root.minsize(*PORTRAIT_MIN_SIZE)
            self._restore_window_geometry(
                layout,
                preserve_position=not initial,
            )
            self.header.configure(padx=8, pady=6)
            self.content_frame.pack_configure(padx=6, pady=6)
            self.title_label.grid(row=0, column=0, sticky="w")
            self.controls_frame.grid(
                row=1,
                column=0,
                columnspan=3,
                sticky="w",
                pady=(4, 0),
            )
            self.status_label.grid(row=0, column=1, sticky="e", padx=(8, 0))
            self.status_label.config(anchor="e", justify="right")
            self.tree_frame.pack_forget()
            self.portrait_frame.pack(fill=tk.BOTH, expand=True)
            self.root.after_idle(self._refresh_portrait_wraplengths)
        else:
            self.root.minsize(*LANDSCAPE_MIN_SIZE)
            self._restore_window_geometry(
                layout,
                preserve_position=not initial,
            )
            self.header.configure(padx=16, pady=10)
            self.content_frame.pack_configure(padx=8, pady=8)
            self.title_label.grid(row=0, column=0, sticky="w")
            self.controls_frame.grid(row=0, column=1, sticky="e", padx=(16, 0))
            self.status_label.grid(row=0, column=2, sticky="e", padx=(16, 0))
            self.status_label.config(anchor="e", justify="right", wraplength=0)
            self.portrait_frame.pack_forget()
            self.tree_frame.pack(fill=tk.BOTH, expand=True)

        self._current_layout = layout
        self.layout_btn.config(text="Wide" if layout == "portrait" else "Portrait")
        self.root.update_idletasks()
        self._suspend_geometry_tracking = False
        self._refresh_status_wraplength()
        self._schedule_geometry_save()
        self._schedule_tree_label_layout()

    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        self._refresh_status_wraplength()
        self._schedule_geometry_save()

    def _on_close(self) -> None:
        if self._geometry_save_job is not None:
            self.root.after_cancel(self._geometry_save_job)
            self._geometry_save_job = None
        self._save_window_geometry()
        self.root.destroy()

    def _show_normal_view(self) -> None:
        self.minimized_frame.pack_forget()
        if self.header.winfo_manager() != "pack":
            self.header.pack(fill=tk.X)
        if self.content_frame.winfo_manager() != "pack":
            self.content_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        if self.hint_label.winfo_manager() != "pack":
            self.hint_label.pack(side=tk.BOTTOM, pady=(0, 6))

    def _show_minimized_view(self) -> None:
        self.header.pack_forget()
        self.content_frame.pack_forget()
        self.hint_label.pack_forget()
        if self.minimized_frame.winfo_manager() != "pack":
            self.minimized_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

    def _enter_minimized_mode(self) -> None:
        if self._window_mode == "minimized":
            return
        if self._geometry_save_job is not None:
            self.root.after_cancel(self._geometry_save_job)
            self._geometry_save_job = None
        self._save_window_geometry(self._current_layout)
        self._restore_layout_after_minimize = self._current_layout

        self._window_mode = "minimized"
        self._suspend_geometry_tracking = True
        self.root.minsize(*MINIMIZED_MIN_SIZE)
        self._show_minimized_view()
        self._restore_window_geometry("minimized", preserve_position=True)
        self.root.update_idletasks()
        self._suspend_geometry_tracking = False
        self._schedule_geometry_save()

    def _restore_from_minimized(self) -> None:
        if self._window_mode != "minimized":
            return
        if self._geometry_save_job is not None:
            self.root.after_cancel(self._geometry_save_job)
            self._geometry_save_job = None
        self._save_window_geometry("minimized")

        restore_layout = self._restore_layout_after_minimize or self._current_layout
        self._window_mode = "normal"
        self._restore_layout_after_minimize = None
        self._layout_var.set(restore_layout)
        self._show_normal_view()
        self._apply_layout()
        self._do_refresh()

    def _refresh_status_wraplength(self) -> None:
        if self._layout_var.get() == "portrait":
            width = max(self.root.winfo_width() - 168, 120)
            self.status_label.config(wraplength=width)
        else:
            self.status_label.config(wraplength=0)

    def _on_portrait_content_configure(self, _event=None) -> None:
        self.portrait_canvas.configure(
            scrollregion=self.portrait_canvas.bbox("all")
        )

    def _on_portrait_canvas_configure(self, event) -> None:
        if self._portrait_canvas_window is not None:
            self.portrait_canvas.itemconfigure(
                self._portrait_canvas_window,
                width=event.width,
            )
        self._refresh_portrait_wraplengths(event.width)

    def _on_portrait_hover(self, _event=None) -> None:
        if self._layout_var.get() == "portrait":
            self.portrait_canvas.focus_set()

    def _pointer_over_portrait(self) -> bool:
        try:
            hovered = self.root.winfo_containing(
                self.root.winfo_pointerx(),
                self.root.winfo_pointery(),
            )
        except tk.TclError:
            return False
        return self._widget_is_descendant(hovered, self.portrait_frame)

    @staticmethod
    def _widget_is_descendant(widget: Optional[tk.Widget], ancestor: tk.Widget) -> bool:
        current = widget
        while current is not None:
            if current == ancestor:
                return True
            current = current.master
        return False

    def _portrait_can_scroll(self) -> bool:
        first, last = self.portrait_canvas.yview()
        return first > 0.0 or last < 1.0

    def _on_global_mousewheel(self, event) -> Optional[str]:
        if self._layout_var.get() != "portrait" or not self._pointer_over_portrait():
            return None
        if not self._portrait_can_scroll():
            return "break"

        delta = int(event.delta)
        if delta == 0:
            return "break"

        units = -int(delta / 120) if delta % 120 == 0 else (-1 if delta > 0 else 1)
        self.portrait_canvas.focus_set()
        self.portrait_canvas.yview_scroll(units, "units")
        return "break"

    def _on_global_mousewheel_linux(self, event) -> Optional[str]:
        if self._layout_var.get() != "portrait" or not self._pointer_over_portrait():
            return None
        if not self._portrait_can_scroll():
            return "break"

        units = -1 if getattr(event, "num", 0) == 4 else 1
        self.portrait_canvas.focus_set()
        self.portrait_canvas.yview_scroll(units, "units")
        return "break"

    def _refresh_portrait_wraplengths(self, canvas_width: Optional[int] = None) -> None:
        if canvas_width is None:
            canvas_width = self.portrait_canvas.winfo_width()
        detail_wrap = max(canvas_width - 44, 160)
        title_wrap = max(canvas_width - 140, 120)
        for bundle in self._card_widgets.values():
            bundle["title_label"].config(wraplength=title_wrap)
            for label in bundle["wrap_labels"]:
                label.config(wraplength=detail_wrap)

    def _refresh_process_label_views(self, directory: str = "") -> None:
        target = self._normalize_directory_key(directory) if directory else ""
        for process in self._processes:
            process_directory = self._normalize_directory_key(process.cwd)
            if target and process_directory != target:
                continue
            bundle = self._card_widgets.get(process.pid)
            if bundle is not None:
                self._update_label_controls(bundle, process)
        self._refresh_tree_rows(directory)

    def _open_label_editor(self, pid: int) -> None:
        process = self._process_lookup.get(pid)
        if process is None:
            return

        directory = self._normalize_directory_key(process.cwd)
        if not directory:
            messagebox.showinfo(
                "Process Label",
                "This process does not have a directory yet, so the label cannot be saved.",
                parent=self.root,
            )
            return

        existing = self._label_for_directory(directory)
        dialog = tk.Toplevel(self.root)
        dialog.title("Process Label")
        dialog.configure(bg=self.HEADING_BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        frame = tk.Frame(dialog, bg=self.HEADING_BG, padx=14, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="Directory",
            font=(self._label_font_family, 8, "bold"),
            bg=self.HEADING_BG,
            fg=self.MUTED_FG,
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            frame,
            text=directory,
            font=("Segoe UI", 8),
            bg=self.HEADING_BG,
            fg=self.FG,
            justify="left",
            wraplength=340,
            anchor="w",
        ).pack(fill=tk.X, pady=(2, 10))

        name_var = tk.StringVar(value=existing["name"] if existing else "")
        color_var = tk.StringVar(
            value=existing["color"] if existing else self.LABEL_PRESETS[0][1]
        )
        preview_text = tk.StringVar(value="")

        tk.Label(
            frame,
            text="Label name",
            font=("Segoe UI", 8, "bold"),
            bg=self.HEADING_BG,
            fg=self.MUTED_FG,
            anchor="w",
        ).pack(anchor="w")
        name_entry = tk.Entry(
            frame,
            textvariable=name_var,
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground="#585b70",
            highlightcolor="#89b4fa",
            bg=self.CARD_BG,
            fg=self.FG,
            insertbackground=self.FG,
        )
        name_entry.pack(fill=tk.X, pady=(2, 10))

        tk.Label(
            frame,
            text="Color code",
            font=("Segoe UI", 8, "bold"),
            bg=self.HEADING_BG,
            fg=self.MUTED_FG,
            anchor="w",
        ).pack(anchor="w")
        color_entry = tk.Entry(
            frame,
            textvariable=color_var,
            font=("Consolas", 9),
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground="#585b70",
            highlightcolor="#89b4fa",
            bg=self.CARD_BG,
            fg=self.FG,
            insertbackground=self.FG,
        )
        color_entry.pack(fill=tk.X, pady=(2, 8))

        tk.Label(
            frame,
            text="Presets",
            font=("Segoe UI", 8, "bold"),
            bg=self.HEADING_BG,
            fg=self.MUTED_FG,
            anchor="w",
        ).pack(anchor="w")
        preset_frame = tk.Frame(frame, bg=self.HEADING_BG)
        preset_frame.pack(fill=tk.X, pady=(4, 10))

        preview_button = tk.Button(
            frame,
            text="Preview",
            font=(self._label_font_family, 8, "bold"),
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=lambda: None,
        )
        preview_button.pack(anchor="w")

        preview_label = tk.Label(
            frame,
            textvariable=preview_text,
            font=("Segoe UI", 8),
            bg=self.HEADING_BG,
            fg=self.MUTED_FG,
            anchor="w",
            justify="left",
        )
        preview_label.pack(fill=tk.X, pady=(6, 10))

        actions = tk.Frame(frame, bg=self.HEADING_BG)
        actions.pack(fill=tk.X)

        def update_preview(*_args) -> None:
            preview_name = name_var.get().strip() or "Preview"
            try:
                parsed_color = self._color_to_hex(color_var.get())
            except ValueError as exc:
                preview_button.config(
                    text=preview_name,
                    bg=self.CARD_BG,
                    fg=self.FG,
                    activebackground=self.CARD_BG,
                    activeforeground=self.FG,
                )
                preview_text.set(str(exc))
                preview_label.config(fg="#f38ba8")
                return

            preview_button.config(
                text=preview_name,
                bg=parsed_color,
                fg=self._label_text_color(parsed_color),
                activebackground=parsed_color,
                activeforeground=self._label_text_color(parsed_color),
            )
            preview_text.set("Use #hex, rgb(), or okclh().")
            preview_label.config(fg=self.MUTED_FG)

        def select_preset(color_value: str) -> None:
            color_var.set(color_value)
            update_preview()

        for preset_name, preset_color in self.LABEL_PRESETS:
            tk.Button(
                preset_frame,
                text=preset_name,
                font=("Segoe UI", 8, "bold"),
                relief=tk.FLAT,
                bd=0,
                padx=8,
                pady=3,
                bg=preset_color,
                fg=self._label_text_color(preset_color),
                activebackground=preset_color,
                activeforeground=self._label_text_color(preset_color),
                cursor="hand2",
                command=lambda value=preset_color: select_preset(value),
            ).pack(side=tk.LEFT, padx=(0, 6))

        def save_label() -> None:
            name = name_var.get().strip()
            if not name:
                messagebox.showerror(
                    "Process Label",
                    "Enter a label name.",
                    parent=dialog,
                )
                name_entry.focus_set()
                return
            try:
                self._color_to_hex(color_var.get())
            except ValueError as exc:
                messagebox.showerror("Process Label", str(exc), parent=dialog)
                color_entry.focus_set()
                return

            self._set_process_label(directory, name, color_var.get().strip())
            self._refresh_process_label_views(directory)
            dialog.destroy()

        def remove_label() -> None:
            self._remove_process_label(directory)
            self._refresh_process_label_views(directory)
            dialog.destroy()

        ttk.Button(actions, text="Save", command=save_label).pack(side=tk.RIGHT)
        ttk.Button(actions, text="Cancel", command=dialog.destroy).pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        if existing is not None:
            ttk.Button(actions, text="Delete", command=remove_label).pack(side=tk.LEFT)

        name_var.trace_add("write", update_preview)
        color_var.trace_add("write", update_preview)
        dialog.bind("<Return>", lambda _event: save_label())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + max((self.root.winfo_width() - dialog.winfo_width()) // 2, 20)
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - dialog.winfo_height()) // 2, 20)
        dialog.geometry(f"+{x}+{y}")
        update_preview()
        name_entry.focus_set()
        dialog.wait_window()

    def _remove_label_via_button(self, pid: int) -> None:
        process = self._process_lookup.get(pid)
        if process is None:
            return
        directory = self._normalize_directory_key(process.cwd)
        if not directory:
            return
        self._remove_process_label(directory)
        self._refresh_process_label_views(directory)

    # ---- Refresh logic ----

    def _schedule_refresh(self):
        if self._window_mode != "minimized":
            self._do_refresh()
        self.root.after(REFRESH_INTERVAL_MS, self._schedule_refresh)

    def _manual_refresh(self):
        self._do_refresh()

    def _do_refresh(self):
        if self._scanning:
            return
        self._scanning = True
        # Don't show "Scanning..." to avoid flickering
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        try:
            procs = scan_processes()
        except Exception:
            procs = []
        self.root.after(0, self._update_views, procs)

    @staticmethod
    def _status_tag(p: CLIProcess) -> str:
        return "processing" if p.status == "Processing" else "waiting"

    def _status_text(self, p: CLIProcess) -> str:
        return p.status

    @classmethod
    def _cli_visual(cls, cli_name: str) -> dict[str, str]:
        for base_name, visual in cls.CLI_VISUALS.items():
            if cli_name == base_name or cli_name.startswith(f"{base_name} "):
                return visual
        return {"id": "unknown", "icon": "■", "accent": "#94e2d5", "header_bg": "#2b3d3d"}

    def _cli_display_name(self, p: CLIProcess) -> str:
        visual = self._cli_visual(p.name)
        return f"{visual['icon']} {p.name}"

    def _row_values(self, p: CLIProcess) -> tuple:
        return (
            "",
            p.pid,
            self._status_text(p),
            f"{p.cpu_percent:.1f}",
            "",
            p.cwd or "(unknown)",
            p.terminal_type or "(unknown)",
        )

    @staticmethod
    def _status_palette(tag: str) -> tuple[str, str, str, str]:
        if tag == "processing":
            return ("#f38ba8", "#3a1a1a", "#6c2742", "#ffffff")
        return ("#a6e3a1", "#1a3a2a", "#2f6549", "#ffffff")

    def _update_views(self, procs: list[CLIProcess]):
        self._processes = procs
        self._process_lookup = {p.pid: p for p in procs}

        try:
            self._sync_tree_rows(procs)
            self._sync_tree_cli_labels(procs)
            self._sync_tree_label_buttons(procs)
            self._sync_portrait_cards(procs)

            count = len(procs)
            ts = time.strftime("%H:%M:%S")
            self.status_label.config(
                text=f"{count} found | {ts}"
            )
        finally:
            self._scanning = False

    def _sync_tree_rows(self, procs: list[CLIProcess]) -> None:
        new_pids = {p.pid: p for p in procs}
        existing_iids = self.tree.get_children()
        existing_pids: dict[int, str] = {}

        for iid in existing_iids:
            values = self.tree.item(iid, "values")
            try:
                pid = int(values[1])
            except (ValueError, IndexError):
                self.tree.delete(iid)
                continue
            if pid not in new_pids:
                self.tree.delete(iid)
            else:
                existing_pids[pid] = iid
                p = new_pids[pid]
                new_vals = self._row_values(p)
                new_tag = self._status_tag(p)
                if (tuple(str(v) for v in values) != tuple(str(v) for v in new_vals)
                        or self.tree.item(iid, "tags") != (new_tag,)):
                    self.tree.item(iid, values=new_vals, tags=(new_tag,))

        for p in procs:
            if p.pid not in existing_pids:
                iid = self.tree.insert(
                    "",
                    tk.END,
                    values=self._row_values(p),
                    tags=(self._status_tag(p),),
                )
                existing_pids[p.pid] = iid

        for index, p in enumerate(procs):
            iid = existing_pids.get(p.pid)
            if iid:
                self.tree.move(iid, "", index)

    def _sync_tree_cli_labels(self, procs: list[CLIProcess]) -> None:
        live_pids = {p.pid for p in procs}
        for pid in list(self._tree_cli_widgets):
            if pid not in live_pids:
                self._tree_cli_widgets[pid].destroy()
                del self._tree_cli_widgets[pid]

        for p in procs:
            widget = self._tree_cli_widgets.get(p.pid)
            if widget is None:
                widget = tk.Label(
                    self.tree_frame,
                    font=("Segoe UI", 10, "bold"),
                    padx=6,
                    pady=0,
                    anchor="w",
                    bd=0,
                    cursor="hand2",
                )
                widget.bind(
                    "<Button-1>",
                    lambda _event, target_pid=p.pid: self._on_tree_cli_click(target_pid),
                )
                widget.bind(
                    "<Double-Button-1>",
                    lambda _event, target_pid=p.pid: self._on_tree_cli_double_click(target_pid),
                )
                self._tree_cli_widgets[p.pid] = widget

            visual = self._cli_visual(p.name)
            row_bg = "#3a1a1a" if self._status_tag(p) == "processing" else "#1a3a2a"
            widget.config(
                text=self._cli_display_name(p),
                bg=row_bg,
                fg=visual["accent"],
            )

        self._schedule_tree_label_layout()

    def _on_tree_cli_click(self, pid: int) -> str:
        for iid in self.tree.get_children():
            if self._tree_pid_from_item(iid) == pid:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                break
        return "break"

    def _on_tree_cli_double_click(self, pid: int) -> str:
        self._on_tree_cli_click(pid)
        self._activate_pid(pid)
        return "break"

    def _sync_tree_label_buttons(self, procs: list[CLIProcess]) -> None:
        live_pids = {p.pid for p in procs}
        for pid in list(self._tree_label_widgets):
            if pid not in live_pids:
                self._tree_label_widgets[pid].destroy()
                del self._tree_label_widgets[pid]

        for p in procs:
            button = self._tree_label_widgets.get(p.pid)
            if button is None:
                button = tk.Button(
                    self.tree_frame,
                    font=(self._label_font_family, 8, "bold"),
                    relief=tk.FLAT,
                    bd=0,
                    padx=10,
                    pady=2,
                    cursor="hand2",
                    command=lambda target_pid=p.pid: self._open_label_editor(target_pid),
                )
                self._tree_label_widgets[p.pid] = button

            saved_label = self._label_for_directory(p.cwd)
            if saved_label is None:
                self._set_unlabeled_button_style(button)
                if not self._normalize_directory_key(p.cwd):
                    button.config(
                        text="No Label",
                        state=tk.DISABLED,
                        bg=self.CHIP_BG,
                        fg=self.SUBTLE_FG,
                        activebackground=self.CHIP_BG,
                        activeforeground=self.SUBTLE_FG,
                        disabledforeground=self.SUBTLE_FG,
                        highlightthickness=1,
                        highlightbackground="#45475a",
                        highlightcolor="#45475a",
                    )
            else:
                self._set_saved_label_button_style(
                    button,
                    saved_label["name"],
                    saved_label["color"],
                )

        self._schedule_tree_label_layout()

    def _sync_portrait_cards(self, procs: list[CLIProcess]) -> None:
        live_pids = {p.pid for p in procs}
        for pid in list(self._card_widgets):
            if pid not in live_pids:
                self._card_widgets[pid]["frame"].destroy()
                del self._card_widgets[pid]

        for p in procs:
            bundle = self._card_widgets.get(p.pid)
            if bundle is None:
                bundle = self._create_portrait_card(p)
                self._card_widgets[p.pid] = bundle
            self._update_portrait_card(bundle, p)

        for bundle in self._card_widgets.values():
            bundle["frame"].pack_forget()

        last_index = len(procs) - 1
        for index, p in enumerate(procs):
            pady = (0, 4) if index != last_index else (0, 0)
            self._card_widgets[p.pid]["frame"].pack(fill=tk.X, pady=pady)

        self._refresh_portrait_wraplengths()
        self._on_portrait_content_configure()

    def _create_portrait_card(self, p: CLIProcess) -> dict[str, object]:
        visual = self._cli_visual(p.name)
        cli_accent = visual["accent"]
        accent, status_bg, badge_bg, badge_fg = self._status_palette(self._status_tag(p))

        card = tk.Frame(
            self.portrait_cards_frame,
            bg=status_bg,
            highlightbackground=accent,
            highlightthickness=1,
            bd=0,
            cursor="hand2",
        )
        accent_bar = tk.Frame(card, bg=accent, width=3, cursor="hand2")
        accent_bar.pack(side=tk.LEFT, fill=tk.Y)

        content = tk.Frame(card, bg=status_bg, padx=8, pady=8, cursor="hand2")
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        header = tk.Frame(content, bg=status_bg, cursor="hand2")
        header.pack(fill=tk.X)

        cli_frame = tk.Frame(header, bg=status_bg, cursor="hand2")
        cli_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        title_label = tk.Label(
            cli_frame,
            text="",
            font=("Segoe UI", 10, "bold"),
            bg=status_bg,
            fg=cli_accent,
            anchor="w",
            justify="left",
            cursor="hand2",
        )
        title_label.pack(anchor="w", fill=tk.X)

        status_badge = tk.Label(
            header,
            text="",
            font=("Segoe UI", 9, "bold"),
            bg=badge_bg,
            fg=badge_fg,
            padx=10,
            pady=4,
            highlightthickness=1,
            highlightbackground=accent,
            highlightcolor=accent,
            cursor="hand2",
        )
        status_badge.pack(side=tk.RIGHT, padx=(8, 0))

        label_row = tk.Frame(content, bg=self.CARD_BG)
        label_row.pack(fill=tk.X, pady=(6, 0))

        label_button = tk.Button(
            label_row,
            text="",
            font=(self._label_font_family, 8, "bold"),
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            bg=self.CHIP_BG,
            fg=self.FG,
            activebackground=self.SELECT_BG,
            activeforeground="#ffffff",
            highlightthickness=1,
            highlightbackground="#585b70",
            highlightcolor="#89b4fa",
            command=lambda target_pid=p.pid: self._open_label_editor(target_pid),
        )
        label_button.pack(side=tk.LEFT)
        self._mark_card_control(label_button)

        label_delete_button = tk.Button(
            label_row,
            text="x",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            bd=0,
            width=2,
            padx=0,
            pady=3,
            cursor="hand2",
            bg=self.CHIP_BG,
            fg=self.FG,
            activebackground=self.SELECT_BG,
            activeforeground="#ffffff",
            highlightthickness=1,
            highlightbackground="#585b70",
            highlightcolor="#89b4fa",
            command=lambda target_pid=p.pid: self._remove_label_via_button(target_pid),
        )
        self._mark_card_control(label_delete_button)

        meta = tk.Frame(content, bg=self.CARD_BG, cursor="hand2")
        meta.pack(fill=tk.X, pady=(6, 0))

        pid_card = tk.Frame(meta, bg=self.CHIP_BG, padx=6, pady=4, cursor="hand2")
        pid_card.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            pid_card,
            text="PID",
            font=("Segoe UI", 7, "bold"),
            bg=self.CHIP_BG,
            fg=self.MUTED_FG,
            anchor="w",
            cursor="hand2",
        ).pack(anchor="w")
        pid_value = tk.Label(
            pid_card,
            text="",
            font=("Segoe UI", 8, "bold"),
            bg=self.CHIP_BG,
            fg=self.FG,
            anchor="w",
            cursor="hand2",
        )
        pid_value.pack(anchor="w", pady=(2, 0))

        cpu_card = tk.Frame(meta, bg=self.CHIP_BG, padx=6, pady=4, cursor="hand2")
        cpu_card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        tk.Label(
            cpu_card,
            text="CPU %",
            font=("Segoe UI", 7, "bold"),
            bg=self.CHIP_BG,
            fg=self.MUTED_FG,
            anchor="w",
            cursor="hand2",
        ).pack(anchor="w")
        cpu_value = tk.Label(
            cpu_card,
            text="",
            font=("Segoe UI", 8, "bold"),
            bg=self.CHIP_BG,
            fg=self.FG,
            anchor="w",
            cursor="hand2",
        )
        cpu_value.pack(anchor="w", pady=(2, 0))

        details = tk.Frame(content, bg=self.CARD_BG, cursor="hand2")
        details.pack(fill=tk.X, pady=(6, 0))

        terminal_value = self._create_card_detail(details, "Terminal")
        cwd_value = self._create_card_detail(details, "Directory")

        bundle = {
            "frame": card,
            "accent_bar": accent_bar,
            "header": header,
            "cli_frame": cli_frame,
            "status_badge": status_badge,
            "title_label": title_label,
            "label_button": label_button,
            "label_delete_button": label_delete_button,
            "pid_value": pid_value,
            "cpu_value": cpu_value,
            "cwd_value": cwd_value,
            "terminal_value": terminal_value,
            "wrap_labels": [cwd_value, terminal_value],
        }
        self._bind_card_activation(card, p.pid)
        return bundle

    def _create_card_detail(self, parent: tk.Widget, label_text: str) -> tk.Label:
        row = tk.Frame(parent, bg=self.CARD_BG, cursor="hand2")
        row.pack(fill=tk.X, pady=(0, 5))
        label = tk.Label(
            row,
            text=label_text.upper(),
            font=("Segoe UI", 7, "bold"),
            bg=self.CARD_BG,
            fg=self.MUTED_FG,
            anchor="w",
            cursor="hand2",
        )
        label.pack(anchor="w")
        value = tk.Label(
            row,
            text="",
            font=("Segoe UI", 8),
            bg=self.CARD_BG,
            fg=self.FG,
            anchor="w",
            justify="left",
            cursor="hand2",
        )
        value.pack(fill=tk.X, expand=True, pady=(1, 0))
        return value

    @staticmethod
    def _mark_card_control(widget: tk.Widget) -> None:
        setattr(widget, "_card_control", True)
        widget.bind("<Double-Button-1>", lambda _event: "break")

    def _tree_label_text(self, p: CLIProcess) -> str:
        saved_label = self._label_for_directory(p.cwd)
        if saved_label is not None:
            return saved_label["name"]
        return "+ Label" if self._normalize_directory_key(p.cwd) else "No Label"

    @staticmethod
    def _tree_pid_from_values(values) -> Optional[int]:
        if not values:
            return None
        try:
            return int(values[1])
        except (ValueError, IndexError, TypeError):
            return None

    def _tree_pid_from_item(self, item_id: str) -> Optional[int]:
        return self._tree_pid_from_values(self.tree.item(item_id, "values"))

    def _refresh_tree_rows(self, directory: str = "") -> None:
        target = self._normalize_directory_key(directory) if directory else ""
        for iid in self.tree.get_children():
            pid = self._tree_pid_from_item(iid)
            if pid is None:
                continue
            process = self._process_lookup.get(pid)
            if process is None:
                continue
            process_directory = self._normalize_directory_key(process.cwd)
            if target and process_directory != target:
                continue
            self.tree.item(
                iid,
                values=self._row_values(process),
                tags=(self._status_tag(process),),
            )
        self._schedule_tree_label_layout()

    def _schedule_tree_label_layout(self) -> None:
        if self._tree_label_layout_job is not None:
            self.root.after_cancel(self._tree_label_layout_job)
        self._tree_label_layout_job = self.root.after_idle(self._layout_tree_label_buttons)

    def _layout_tree_label_buttons(self) -> None:
        self._tree_label_layout_job = None
        if self._current_layout != "landscape" or self._window_mode == "minimized":
            for widget in self._tree_cli_widgets.values():
                widget.place_forget()
            for button in self._tree_label_widgets.values():
                button.place_forget()
            return

        self.tree.update_idletasks()
        tree_x = self.tree.winfo_x()
        tree_y = self.tree.winfo_y()
        for iid in self.tree.get_children():
            pid = self._tree_pid_from_item(iid)
            if pid is None:
                continue
            cli_widget = self._tree_cli_widgets.get(pid)
            button = self._tree_label_widgets.get(pid)
            if cli_widget is not None:
                cli_bbox = self.tree.bbox(iid, self._tree_cli_column_id)
                if not cli_bbox:
                    cli_widget.place_forget()
                else:
                    cell_x, cell_y, cell_width, cell_height = cli_bbox
                    cli_widget.place(
                        x=tree_x + cell_x + 4,
                        y=tree_y + cell_y + 1,
                        width=max(cell_width - 8, 48),
                        height=max(cell_height - 2, 20),
                    )
            if button is None:
                continue
            bbox = self.tree.bbox(iid, self._tree_label_column_id)
            if not bbox:
                button.place_forget()
                continue
            cell_x, cell_y, cell_width, cell_height = bbox
            width = max(cell_width - 8, 36)
            height = max(cell_height - 4, 20)
            button.place(
                x=tree_x + cell_x + 4,
                y=tree_y + cell_y + 2,
                width=width,
                height=height,
            )

    def _on_tree_yview(self, first, last) -> None:
        self.tree_scrollbar.set(first, last)
        self._schedule_tree_label_layout()

    def _on_tree_scroll(self, *args) -> None:
        self.tree.yview(*args)
        self._schedule_tree_label_layout()

    def _on_tree_geometry_change(self, _event=None) -> None:
        self._schedule_tree_label_layout()

    def _set_unlabeled_button_style(self, button: tk.Button) -> None:
        button.config(
            text="+ Label",
            state=tk.NORMAL,
            bg=self.UNLABELED_CHIP_BG,
            fg=self.MUTED_FG,
            activebackground=self.UNLABELED_CHIP_ACTIVE_BG,
            activeforeground=self.FG,
            disabledforeground=self.SUBTLE_FG,
            highlightthickness=1,
            highlightbackground="#72789c",
            highlightcolor="#89b4fa",
            font=(self._label_font_family, 8, "normal"),
        )

    def _set_saved_label_button_style(self, button: tk.Button, name: str, color: str) -> None:
        parsed_color = self._color_to_hex(color)
        text_color = self._label_text_color(parsed_color)
        button.config(
            text=name,
            state=tk.NORMAL,
            bg=parsed_color,
            fg=text_color,
            activebackground=parsed_color,
            activeforeground=text_color,
            disabledforeground=text_color,
            highlightthickness=0,
            font=(self._label_font_family, 8, "bold"),
        )

    def _update_label_controls(self, bundle: dict[str, object], p: CLIProcess) -> None:
        label_button = bundle["label_button"]
        label_delete_button = bundle["label_delete_button"]
        saved_label = self._label_for_directory(p.cwd)

        if saved_label is None:
            self._set_unlabeled_button_style(label_button)
            label_delete_button.pack_forget()
            return

        self._set_saved_label_button_style(
            label_button,
            saved_label["name"],
            saved_label["color"],
        )
        label_delete_button.pack(side=tk.LEFT, padx=(6, 0))

    def _bind_card_activation(self, widget: tk.Widget, pid: int) -> None:
        if getattr(widget, "_card_control", False):
            return
        widget.bind(
            "<Double-Button-1>",
            lambda _event, target_pid=pid: self._activate_pid(target_pid),
        )
        widget.bind("<Enter>", self._on_portrait_hover, add="+")
        for child in widget.winfo_children():
            self._bind_card_activation(child, pid)

    def _update_portrait_card(self, bundle: dict[str, object], p: CLIProcess) -> None:
        tag = self._status_tag(p)
        visual = self._cli_visual(p.name)
        cli_accent = visual["accent"]
        accent, status_bg, badge_bg, badge_fg = self._status_palette(tag)
        bundle["frame"].config(bg=status_bg, highlightbackground=accent)
        bundle["accent_bar"].config(bg=accent)
        bundle["header"].config(bg=status_bg)
        bundle["cli_frame"].config(bg=status_bg)
        bundle["status_badge"].config(
            text=self._status_text(p),
            bg=badge_bg,
            fg=badge_fg,
            highlightbackground=accent,
            highlightcolor=accent,
        )
        bundle["title_label"].config(text=self._cli_display_name(p), bg=status_bg, fg=cli_accent)
        self._retint_card_background(bundle["frame"], status_bg)
        self._update_label_controls(bundle, p)
        bundle["pid_value"].config(text=str(p.pid))
        bundle["cpu_value"].config(text=f"{p.cpu_percent:.1f}")
        bundle["cwd_value"].config(text=p.cwd or "(unknown)")
        bundle["terminal_value"].config(text=p.terminal_type or "(unknown)")

    # ---- Window activation ----

    def _on_tree_click(self, event=None):
        if event is None:
            return None
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return None
        column = self.tree.identify_column(event.x)
        if column != self._tree_label_column_id:
            return None

        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return "break"

        self.tree.selection_set(item_id)
        pid = self._tree_pid_from_item(item_id)
        if pid is None:
            return "break"

        self._open_label_editor(pid)
        return "break"

    def _on_tree_activate(self, _event=None):
        if _event is not None and getattr(_event, "x", None) is not None:
            region = self.tree.identify("region", _event.x, _event.y)
            column = self.tree.identify_column(_event.x)
            if region == "cell" and column == self._tree_label_column_id:
                return "break"

        sel = self.tree.selection()
        if not sel:
            return
        pid = self._tree_pid_from_item(sel[0])
        if pid is None:
            return

        self._activate_pid(pid)

    def _activate_pid(self, pid: int) -> None:
        proc = self._process_lookup.get(pid)
        if proc is None or not proc.hwnds:
            self.status_label.config(text=f"No window found for PID {pid}")
            return

        hwnd = proc.hwnds[0]
        try:
            activate_window(hwnd)
            self.status_label.config(text=f"Activated window for PID {pid}")
        except Exception as e:
            self.status_label.config(text=f"Failed to activate: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("=" * 50)
    print("  AI Manager - AI CLI Process Monitor")
    print("=" * 50)
    print()
    print(f"  Started at : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Refresh    : {REFRESH_INTERVAL_MS}ms")
    print()
    print("  Monitoring: Claude Code / Codex CLI / GitHub Copilot CLI")
    print()
    print("  Close the GUI window to exit.")
    print("-" * 50)

    root = tk.Tk()

    # Set DPI awareness for crisp rendering
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = AIManagerApp(root)
    root.mainloop()
    print()
    print("AI Manager stopped.")


if __name__ == "__main__":
    main()
