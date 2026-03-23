<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="Japanese"></a>
</p>

# AI CLI Watcher - AI CLI Process Monitor

AI CLI Watcher is a Windows desktop application for monitoring the runtime status of AI CLI tools (Claude Code / Codex CLI / GitHub Copilot CLI) in real time.

- You can view the status of each process in a single list: processing or waiting for user input.
- You can double-click a process to bring its terminal window to the foreground.
- It also supports bringing Windows Terminal to the foreground even when it is open on another virtual desktop.

> [!IMPORTANT]
> Even if you use Windows virtual desktops, you can activate a Windows Terminal window from the list or cards view when it is open on a different desktop.

## Features

| Feature | Description |
| ------- | ----------- |
| Automatic process detection | Automatically detects AI CLI processes running on both Windows and WSL |
| Status display | Shows each process state as `Processing` or `Waiting for input` |
| Color-coded display | Waiting processes are shown in green tones, and processing ones in red tones, so they are easy to distinguish at a glance |
| View mode switching | Switch between `Table` (list), `Cards` (vertical cards), and `Minimize` views. Window size and position are stored separately for each mode |
| Label management | Save a label name and color for each working directory. Labels can be edited from the Label column in `Table` view or from the `+ Label` button in `Cards` view |
| Virtual desktop support | Windows Terminal opened on another virtual desktop can also be activated from the list or cards view |
| Window activation | Double-click a row or card to activate the corresponding CLI terminal window. The app also attempts to restore minimized windows |
| Working directory display | Shows the working directory of each CLI so that multiple instances can be distinguished |
| Terminal type display | Shows the terminal type, such as Windows Terminal, PowerShell, or Command Prompt |
| Always on Top | The `Always on Top` checkbox keeps the window above others, and the setting is saved automatically |
| Auto refresh | In `Table` and `Cards` views, the list refreshes every 2 seconds by default and can be changed in `settings.json`. In `Minimize` view, refresh is paused and runs immediately when `Restore` is pressed |

## Supported CLIs

| CLI | Windows | WSL |
| --- | ------- | --- |
| Claude Code (Anthropic) | ✅ | ✅ |
| Codex CLI (OpenAI) | ✅ | ✅ |
| GitHub Copilot CLI | ✅ | ✅ |

- Supports simultaneous detection of multiple instances of each CLI
- Can also detect processes launched via node/npm/npx
- Excludes false positives such as VS Code extension background processes and the Windows Copilot app

## Requirements

- **OS**: Windows 10 / 11
- **Runtime for distribution builds**: No additional runtime required
- **Build environment from source**: `.NET 10 SDK`
- **If you want WSL monitoring**: WSL, with `python3` recommended inside each distro

Even without `python3`, Windows-side monitoring and basic WSL process detection still work.
However, for WSL monitoring, working directory retrieval, I/O collection, and more accurate CPU/I/O-based status detection will be limited.

## Launch

Download the distributed `app` folder from Releases, extract it, and run `AI-CLI-Watcher.exe` inside it.
No additional `.NET Runtime` installation is required.

---

If you want to build from `src`, run the PowerShell script as follows.

```powershell
.\publish.ps1 -CleanOutput
```

`publish.ps1` uses self-contained single-file publish, so the root of `app/` is normally just `AI-CLI-Watcher.exe` and `settings.json`.
If you are switching from the older publish layout, run it once with `-CleanOutput` to remove stale files.

## Usage

### Screen Layout

Displayed content changes depending on the current view mode. The following images show examples of each mode.

| View Mode | Sample Image |
| --------- | ------------ |
| `Table` | ![](./images/00001.jpg) |
| `Cards` | ![](./images/00002.jpg) |
| `Minimize` | ![](./images/00003.jpg) |

### Field Descriptions

| Field | Description |
| ----- | ----------- |
| AI CLI | The CLI name. For WSL processes, `(WSL:<distribution name>)` is appended |
| PID | Process ID |
| Status | `▶ Processing` or `⏸ Waiting for input` |
| CPU % | CPU usage of the entire process tree |
| Label | Label name. In `Table` view, this appears in the Label column. If unset, `+ Label` is shown. If the directory is unavailable, `No Label` is shown. In `Cards` view, it appears as the `+ Label` button or as the saved label on the card |
| Directory | The CLI working directory. Long paths are shortened while preserving the end of the path |
| Terminal | The terminal type, such as Windows Terminal or PowerShell |

### Controls

| Action | Behavior |
| ------ | -------- |
| `Cards` / `Table` button | Switches between `Table` and `Cards` views. Size and position are saved independently for each view |
| `Minimize` button | Switches to a compact screen that only shows the `Restore` button |
| `Restore` button | Returns from the minimized screen to the previous view and position, then refreshes immediately |
| `+ Label` button | Click `+ Label` to add or edit a label |
| Double-click / Enter | Activates the selected CLI terminal window and also attempts to restore minimized windows, including Windows Terminal on another virtual desktop |
| Status bar display | In addition to the current time, the status bar shows either `Auto refresh` or the latest `Scan` duration |
| `Always on Top` checkbox | When enabled, the AI CLI Watcher window stays above all other windows |

Labels cannot be saved for processes whose working directory cannot be determined.

### Status Determination Logic

On both Windows and WSL, status is determined using the following two signals. If either one exceeds its threshold, the status becomes `Processing`. If both are below their thresholds, the status becomes `Waiting for input`.

| Signal | Threshold | Description |
| ------ | --------- | ----------- |
| Tree CPU | 2.0% | Total CPU usage of the process and all child processes |
| I/O Delta | 1,000 activity score | Increase in I/O activity since the previous scan. On Windows, this includes I/O operation counts in addition to bytes |

- On Windows, the app uses a native C# / Win32-based implementation to gather process-tree information
- On WSL, the app uses `ps` and `/proc`, and when `python3` is available it also retrieves CPU ticks, I/O information, and working directories

### Settings Persistence

The following settings are saved in `settings.json` and preserved after the application exits.
If `settings.json` does not exist at startup, cannot be parsed as JSON, or has an invalid configuration, the application automatically recreates it by filling and normalizing only the managed settings with system default values.

| Setting | Storage |
| ------- | ------- |
| `Always on Top` checkbox state | `settings.json` (`always_on_top`) |
| Last normal view mode used (`Table` or `Cards`) | `settings.json` (`layout_mode`) |
| Auto refresh interval | `settings.json` (`refresh_interval_ms`) |
| Detailed status bar display mode | `settings.json` (`status_detail_mode`) |
| Window size and position for each view mode (`Table` / `Cards` / `Minimize`) | `settings.json` (`window_geometries.landscape` / `portrait` / `minimized`) |
| Label name and color for each working directory | `settings.json` (`process_labels`) |

The main settings are as follows.

| Key | Type | Default | Allowed values / Description |
| --- | ---- | ------- | ---------------------------- |
| `always_on_top` | boolean | `false` | `true` / `false` |
| `layout_mode` | string | `"landscape"` | `"landscape"` / `"portrait"` |
| `refresh_interval_ms` | number | `2000` | `1000`, `2000`, `3000`, `5000`. Invalid values are normalized to `2000` |
| `status_detail_mode` | string | `"refresh_interval"` | `"refresh_interval"` / `"refresh_interval_ms"` show the refresh interval, and `"scan_duration"` / `"scan_duration_ms"` show the latest scan duration |

Example:

```json
{
  "always_on_top": false,
  "layout_mode": "landscape",
  "refresh_interval_ms": 2000,
  "status_detail_mode": "refresh_interval"
}
```

## File Structure

```text
AI-CLI-Watcher/
├── .gitignore                   # Root ignore settings
├── LICENSE                      # License
├── README.md                    # Japanese README
├── README_en.md                 # English README
├── publish.ps1                  # Build script for distribution
├── images/                      # Screenshot samples used in the README
│   ├── 00001.jpg
│   ├── 00002.jpg
│   └── 00003.jpg
└── src/
    ├── .gitignore               # Ignore settings for build artifacts
    ├── AI-CLI-Watcher.sln       # Solution file
    ├── AI-CLI-Watcher.csproj    # WPF project definition
    ├── App.xaml                 # Application definition
    ├── App.xaml.cs              # Application initialization
    ├── MainWindow.xaml          # Main window UI
    ├── MainWindow.xaml.cs       # Main window logic
    ├── AssemblyInfo.cs          # Assembly information
    ├── app_icon.ico             # Application icon
    ├── Helpers/
    │   └── ColorHelper.cs       # Color helper
    ├── Models/
    │   ├── AppSettings.cs       # Settings model
    │   ├── CliDefinition.cs     # Monitored CLI definitions
    │   └── CliProcess.cs        # Detected process model
    ├── Services/
    │   ├── ProcessScanner.cs    # Windows-side process detection
    │   ├── SettingsService.cs   # Settings load/save logic
    │   ├── Win32Api.cs          # Win32 API integration
    │   └── WslScanner.cs        # WSL-side process detection
    ├── Themes/
    │   └── DarkTheme.xaml       # Theme definition
    └── Views/
        ├── LabelEditorDialog.xaml    # Label editor dialog UI
        └── LabelEditorDialog.cs      # Label editor dialog logic
```

`app/`, `src/bin/`, `src/obj/`, and `settings.json` are omitted here because they are generated during build or runtime.

## Verification Status

| Environment | CLI | Verification |
| ----------- | --- | ------------ |
| Windows | Claude Code | ✅ Verified |
| Windows | Codex CLI | ✅ Verified |
| Windows | GitHub Copilot CLI | ✅ Verified |
| WSL | Codex CLI | ✅ Verified |
| WSL | Claude Code | ✅ Verified |
| WSL | GitHub Copilot CLI | ✅ Verified |

## ❗This project is provided under the MIT License. See the LICENSE file for details.
