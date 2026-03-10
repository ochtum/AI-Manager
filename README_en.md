<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# AI Manager - AI CLI Process Monitor

A Windows desktop application that monitors AI CLI tools (Claude Code / Codex CLI / GitHub Copilot CLI) in real time and displays their operational status.

## Features

| Feature | Description |
|---------|-------------|
| Automatic process detection | Automatically detects AI CLI processes running on Windows and WSL |
| Status display | Shows each process state as "Processing" or "Waiting for input" |
| Color-coded status | Green background for waiting, red background for processing — status is visible at a glance |
| Window switching | Double-click a process in the list to bring its terminal window to the foreground |
| Working directory display | Shows the directory each CLI is running in, making it easy to distinguish multiple instances |
| Terminal type display | Shows the terminal type such as Windows Terminal, PowerShell, Command Prompt, etc. |
| Always on Top | "Always on Top" checkbox keeps the window above all others (setting is persisted automatically) |
| 1-second auto-refresh | Process information is refreshed automatically every second |

## Supported CLIs

| CLI | Windows | WSL |
|-----|---------|-----|
| Claude Code (Anthropic) | ✅ | ✅ |
| Codex CLI (OpenAI) | ✅ | ✅ |
| GitHub Copilot CLI | ✅ | ✅ |

- Supports simultaneous detection of multiple instances of each CLI
- Detects processes launched via node/npm/npx wrappers
- Filters out false positives such as VS Code extension background processes and the Windows Copilot app

## System Requirements

- **OS**: Windows 10 / 11
- **Python**: 3.10 or later
- **Dependencies**: psutil

## Setup

### 1. Install dependencies

```
pip install -r requirements.txt
```

### 2. Launch

#### Launch from batch file (recommended)

Double-click `scripts\windows\launch_ai_manager.bat`.

#### Launch from command line

```
python ai_manager.py
```

## Usage

### Screen Layout

![](./images/00001.jpg)

### Column Descriptions

| Column | Description |
|--------|-------------|
| AI CLI | CLI name. For WSL processes, `(WSL:<distro name>)` is appended |
| PID | Process ID |
| Status | `▶ Processing` (busy) or `⏸ Waiting for input` (idle) |
| CPU % | Combined CPU usage across the entire process tree |
| Working Directory | The directory where the CLI is running |
| Terminal | Terminal type (Windows Terminal, PowerShell, etc.) |

### Controls

| Action | Behavior |
|--------|----------|
| Double-click / Enter | Brings the selected CLI's terminal window to the foreground |
| Refresh button | Manually refreshes the process list |
| Always on Top checkbox | When checked, the AI Manager window stays above all other windows |

### Status Detection Logic

Status is determined by two signals. If either exceeds its threshold, the process is marked as "Processing".

| Signal | Threshold | Description |
|--------|-----------|-------------|
| Tree CPU | 2.0% | Combined CPU usage of the process and all its child processes |
| I/O Delta | 1,000 bytes | Change in disk I/O since the last scan (detects API communication, etc.) |

### Persisted Settings

The following settings are saved and restored across application restarts.

| Setting | Stored in |
|---------|-----------|
| Always on Top | `settings.json` |

## File Structure

```
AI-Manager/
├── ai_manager.py          # Main application
├── requirements.txt        # Dependencies (psutil)
├── settings.json           # User settings (auto-generated)
├── .gitignore
├── scripts/
│   └── windows/
│       └── launch_ai_manager.bat   # Launch script
└── README.md               # Documentation (Japanese)
```

## Technical Notes

- Built with **pure Python + tkinter + ctypes**. PowerShell is not used at all.
- **Win32 API (ctypes)**: Uses `EnumWindows`, `SetForegroundWindow`, `AttachConsole`, `GetConsoleWindow`, and other APIs to detect and activate windows.
- **WSL support**: Detects processes inside WSL using `wsl --list` and `wsl -d <distro> -- ps aux`. Working directories are resolved via batched `readlink` calls.
- **Window switching**: Even in multi-tab environments like Windows Terminal, the correct tab HWND is resolved via `AttachConsole`/`GetConsoleWindow`, and Alt key simulation ensures reliable foreground activation.

## Verification Status

| Environment | CLI | Status |
|-------------|-----|--------|
| Windows | Claude Code | ✅ Verified |
| Windows | Codex CLI | ✅ Verified |
| Windows | GitHub Copilot CLI | ✅ Verified |
| WSL | Codex CLI | ✅ Verified |
| WSL | Claude Code | Not yet tested |
| WSL | GitHub Copilot CLI | Not yet tested |

## ❗This project is licensed under the MIT License, see the LICENSE file for details
