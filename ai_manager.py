"""
AI Manager - AI CLI Process Monitor
Detects Claude Code, Codex CLI, GitHub Copilot CLI processes
and shows their status (processing / waiting for input).
"""

import ctypes
import ctypes.wintypes
import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk
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

# Windows API constants
SW_RESTORE = 9
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

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
    if IsIconic(hwnd):
        ShowWindow(hwnd, SW_RESTORE)

    # Strategy: simulate an Alt key press to unlock SetForegroundWindow,
    # then call SetForegroundWindow.  This reliably bypasses the restriction
    # that only the foreground process may change the foreground window.
    VK_MENU = 0x12
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    keybd_event = user32.keybd_event

    keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)       # Alt down
    SetForegroundWindow(hwnd)
    keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)  # Alt up
    BringWindowToTop(hwnd)


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
        if len(p.hwnds) <= 1:
            continue

        # Check cache first
        if p.pid in _hwnd_cache:
            p.hwnds = [_hwnd_cache[p.pid]]
            continue

        # Resolve via AttachConsole
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
        else:
            p.hwnds = p.hwnds[:1]


def _find_wsl_tab_hwnds() -> list[tuple[int, float]]:
    """Find interactive WSL tab host processes and their console HWNDs.

    Returns a list of (hwnd, create_time) sorted by creation time (oldest first).
    Each entry corresponds to one WSL terminal tab.
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
            try:
                parent = wp.parent()
                if parent and (parent.name() or "").lower() == "cmd.exe":
                    target_pid = parent.pid
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

            hwnd = _get_console_hwnd_for_pid(target_pid)
            if hwnd:
                ctime = wp.create_time()
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

    # WSL CLI detection patterns: (display_name, keywords)
    wsl_cli_patterns = [
        ("Claude Code", ["claude-code", "@anthropic-ai/claude-code", "/bin/claude"]),
        ("Codex CLI", ["@openai/codex", "/bin/codex", "codex/codex"]),
        ("GitHub Copilot CLI", ["github-copilot-cli", "@githubnext/github-copilot-cli"]),
    ]

    # Get console HWNDs for WSL tabs, sorted by creation time
    tab_hwnds = _find_wsl_tab_hwnds()

    for distro in distros:
        try:
            ps_out = subprocess.check_output(
                ["wsl", "-d", distro, "--", "ps", "aux"],
                timeout=5, stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="replace")
        except Exception:
            continue

        # First pass: collect matching PIDs and tty info
        seen_tty: set[tuple[str, str]] = set()
        tty_procs: list[tuple[str, CLIProcess]] = []
        pids_to_resolve: list[int] = []

        for line in ps_out.splitlines():
            line_lower = line.lower()
            for display_name, keywords in wsl_cli_patterns:
                if any(kw in line_lower for kw in keywords):
                    parts = line.split(None, 10)
                    if len(parts) < 11:
                        continue
                    try:
                        wsl_pid = int(parts[1])
                        cpu = float(parts[2])
                        tty = parts[6]
                    except (ValueError, IndexError):
                        continue

                    key = (display_name, tty)
                    if key in seen_tty:
                        continue
                    seen_tty.add(key)

                    cmdline_str = parts[10] if len(parts) > 10 else ""
                    status = "Processing" if cpu > CPU_BUSY_THRESHOLD else "Waiting for input"
                    pids_to_resolve.append(wsl_pid)

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
                    tty_procs.append((tty, proc_entry))
                    break

        # Batch-resolve cwd for all detected PIDs in one WSL call
        cwd_map: dict[int, str] = {}
        if pids_to_resolve:
            try:
                # Build a single shell command: readlink -f /proc/PID1/cwd; readlink ...
                readlink_cmds = "; ".join(
                    f"echo {pid}=$(readlink -f /proc/{pid}/cwd 2>/dev/null)"
                    for pid in pids_to_resolve
                )
                cwd_out = subprocess.check_output(
                    ["wsl", "-d", distro, "--", "bash", "-c", readlink_cmds],
                    timeout=5, stderr=subprocess.DEVNULL,
                ).decode("utf-8", errors="replace")
                for line in cwd_out.splitlines():
                    if "=" in line:
                        pid_str, path = line.split("=", 1)
                        try:
                            cwd_map[int(pid_str.strip())] = path.strip()
                        except ValueError:
                            pass
            except Exception:
                pass

        # Assign cwd from batch results
        for _tty, proc_entry in tty_procs:
            proc_entry.cwd = cwd_map.get(proc_entry.pid, "")

        # Sort by tty (pts/2 < pts/3 < ...) to align with tab_hwnds order
        tty_procs.sort(key=lambda x: x[0])

        # Assign HWNDs: tab_hwnds are sorted by creation time (oldest first),
        # and tty_procs are sorted by pts number (lowest first).
        for i, (tty, proc_entry) in enumerate(tty_procs):
            if i < len(tab_hwnds):
                proc_entry.hwnds = [tab_hwnds[i][0]]
            results.append(proc_entry)

    return results


def scan_processes() -> list[CLIProcess]:
    """Scan all running processes and return detected AI CLI processes."""
    results: list[CLIProcess] = []
    seen_pids: set[int] = set()

    # --- Windows processes ---
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
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

            cmdline_str = " ".join(info.get("cmdline") or [])

            # Get working directory
            try:
                cwd = proc.cwd()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                cwd = ""

            # Find terminal window
            terminal = _find_terminal_ancestor(proc)
            terminal_pid = terminal.pid if terminal else None

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
                all_hwnds = find_windows_for_pid(pid)
            if not all_hwnds:
                try:
                    ppid = proc.ppid()
                    if ppid:
                        all_hwnds = find_windows_for_pid(ppid)
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass

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
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AI Manager – AI CLI Process Monitor")
        self.root.geometry("1200x420")
        self.root.minsize(900, 300)
        self.root.configure(bg="#1e1e2e")

        self._processes: list[CLIProcess] = []
        self._scanning = False

        # Always-on-top state (restored from settings)
        self._topmost_var = tk.BooleanVar(value=self._load_topmost_setting())

        self._build_ui()
        self._apply_topmost()
        self._schedule_refresh()

    # ---- UI construction ----

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Dark theme colors
        bg = "#1e1e2e"
        fg = "#cdd6f4"
        select_bg = "#45475a"
        heading_bg = "#313244"

        style.configure("Treeview",
                        background=bg, foreground=fg, fieldbackground=bg,
                        rowheight=32, font=("Segoe UI", 10))
        style.configure("Treeview.Heading",
                        background=heading_bg, foreground=fg,
                        font=("Segoe UI", 10, "bold"))
        style.map("Treeview",
                  background=[("selected", select_bg)],
                  foreground=[("selected", "#ffffff")])

        # Header
        header = tk.Frame(self.root, bg="#313244", height=48)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(header, text="AI Manager", font=("Segoe UI", 14, "bold"),
                 bg="#313244", fg="#cba6f7").pack(side=tk.LEFT, padx=16)

        self.status_label = tk.Label(header, text="",
                                     font=("Segoe UI", 9), bg="#313244", fg="#a6adc8")
        self.status_label.pack(side=tk.RIGHT, padx=16)

        btn_frame = tk.Frame(header, bg="#313244")
        btn_frame.pack(side=tk.RIGHT, padx=8)

        style.configure("Accent.TButton", font=("Segoe UI", 9))
        refresh_btn = ttk.Button(btn_frame, text="Refresh", style="Accent.TButton",
                                 command=self._manual_refresh)
        refresh_btn.pack(side=tk.LEFT, padx=4)

        topmost_cb = tk.Checkbutton(
            btn_frame, text="Always on Top",
            variable=self._topmost_var, command=self._on_topmost_toggle,
            bg="#313244", fg="#cdd6f4", selectcolor="#45475a",
            activebackground="#313244", activeforeground="#cdd6f4",
            font=("Segoe UI", 9),
        )
        topmost_cb.pack(side=tk.LEFT, padx=(12, 4))

        # Treeview
        columns = ("cli", "pid", "status", "cpu", "cwd", "terminal")
        tree_frame = tk.Frame(self.root, bg=bg)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                                 selectmode="browse")
        self.tree.heading("cli", text="AI CLI")
        self.tree.heading("pid", text="PID")
        self.tree.heading("status", text="Status")
        self.tree.heading("cpu", text="CPU %")
        self.tree.heading("cwd", text="Working Directory")
        self.tree.heading("terminal", text="Terminal")

        self.tree.column("cli", width=160, minwidth=120)
        self.tree.column("pid", width=80, minwidth=60, anchor=tk.CENTER)
        self.tree.column("status", width=160, minwidth=120)
        self.tree.column("cpu", width=80, minwidth=60, anchor=tk.CENTER)
        self.tree.column("cwd", width=300, minwidth=150)
        self.tree.column("terminal", width=300, minwidth=150)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)

        # Row colors by status
        self.tree.tag_configure("waiting",
                                background="#1a3a2a", foreground="#a6e3a1")
        self.tree.tag_configure("processing",
                                background="#3a1a1a", foreground="#f38ba8")

        # Double-click / Enter to activate window
        self.tree.bind("<Double-1>", self._on_activate)
        self.tree.bind("<Return>", self._on_activate)

        # Footer hint
        hint = tk.Label(self.root,
                        text="Double-click or press Enter to activate the selected process window",
                        font=("Segoe UI", 8), bg=bg, fg="#6c7086")
        hint.pack(side=tk.BOTTOM, pady=(0, 6))

    # ---- Always-on-top ----

    @staticmethod
    def _load_topmost_setting() -> bool:
        try:
            data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            return bool(data.get("always_on_top", False))
        except Exception:
            return False

    @staticmethod
    def _save_topmost_setting(value: bool) -> None:
        try:
            data: dict = {}
            if _SETTINGS_FILE.exists():
                data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            data["always_on_top"] = value
            _SETTINGS_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    def _apply_topmost(self) -> None:
        self.root.attributes("-topmost", self._topmost_var.get())

    def _on_topmost_toggle(self) -> None:
        self._apply_topmost()
        self._save_topmost_setting(self._topmost_var.get())

    # ---- Refresh logic ----

    def _schedule_refresh(self):
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
        self.root.after(0, self._update_tree, procs)

    @staticmethod
    def _status_tag(p: CLIProcess) -> str:
        return "processing" if p.status == "Processing" else "waiting"

    def _row_values(self, p: CLIProcess) -> tuple:
        status_icon = "\u25b6" if p.status == "Processing" else "\u23f8"
        return (
            p.name,
            p.pid,
            f"{status_icon}  {p.status}",
            f"{p.cpu_percent:.1f}",
            p.cwd or "(unknown)",
            p.terminal_type or "(unknown)",
        )

    def _update_tree(self, procs: list[CLIProcess]):
        self._processes = procs

        # Build a map of new data keyed by PID
        new_pids = {p.pid: p for p in procs}
        existing_iids = self.tree.get_children()
        existing_pids: dict[int, str] = {}  # pid -> iid

        # Remove rows whose PID is gone, update rows that still exist
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
                # Update values and tag in-place (no flicker)
                if (tuple(str(v) for v in values) != tuple(str(v) for v in new_vals)
                        or self.tree.item(iid, "tags") != (new_tag,)):
                    self.tree.item(iid, values=new_vals, tags=(new_tag,))

        # Insert new rows that didn't exist before
        for p in procs:
            if p.pid not in existing_pids:
                self.tree.insert("", tk.END, values=self._row_values(p),
                                 tags=(self._status_tag(p),))

        count = len(procs)
        ts = time.strftime("%H:%M:%S")
        self.status_label.config(
            text=f"{count} process{'es' if count != 1 else ''} found  |  {ts}"
        )
        self._scanning = False

    # ---- Window activation ----

    def _on_activate(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        if not values:
            return
        pid_str = values[1]
        try:
            pid = int(pid_str)
        except ValueError:
            return

        # Find the CLIProcess by pid
        proc = next((p for p in self._processes if p.pid == pid), None)
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
