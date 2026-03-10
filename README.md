<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# AI Manager - AI CLI Process Monitor

AI系CLIツール（Claude Code / Codex CLI / GitHub Copilot CLI）の稼働状況をリアルタイムで監視するWindows向けデスクトップアプリケーションです。

## 機能一覧

| 機能 | 説明 |
|------|------|
| プロセス自動検出 | Windows上およびWSL上で動作するAI CLIプロセスを自動的に検出 |
| ステータス表示 | 各プロセスの状態を「Processing（処理中）」「Waiting for input（入力待ち）」で表示 |
| 色分け表示 | 入力待ち＝緑系、処理中＝赤系の背景色で一目で判別可能 |
| ウィンドウ切替 | リスト内のプロセスをダブルクリックすると、そのCLIのターミナルウィンドウをアクティブ化 |
| 作業ディレクトリ表示 | 各CLIが実行されているディレクトリを表示し、複数インスタンスの区別が可能 |
| ターミナル種別表示 | Windows Terminal / PowerShell / Command Prompt 等のターミナル種別を表示 |
| 常に前面表示 | 「Always on Top」チェックで、ウィンドウを常に前面に表示（設定は自動保存） |
| 1秒間隔の自動更新 | 1秒ごとにプロセス情報を自動リフレッシュ |

## 対応CLI

| CLI | Windows | WSL |
|-----|---------|-----|
| Claude Code (Anthropic) | ✅ | ✅ |
| Codex CLI (OpenAI) | ✅ | ✅ |
| GitHub Copilot CLI | ✅ | ✅ |

- 各CLIの複数インスタンスの同時検出に対応
- node/npm/npx 経由で起動されたプロセスも検出可能
- VS Code拡張のバックグラウンドプロセスやWindows Copilotアプリ等の誤検出を除外

## 動作環境

- **OS**: Windows 10 / 11
- **Python**: 3.10 以上
- **依存パッケージ**: psutil

## セットアップ

### 1. 依存パッケージのインストール

```
pip install -r requirements.txt
```

### 2. 起動

#### バッチファイルから起動（推奨）

`scripts\windows\launch_ai_manager.bat` をダブルクリックします。

#### コマンドラインから起動

```
python ai_manager.py
```

## 使い方

### 画面構成

![](./images/00001.jpg)

### 各列の説明

| 列名 | 内容 |
|------|------|
| AI CLI | CLIの名称。WSLの場合は `(WSL:ディストリビューション名)` が付加される |
| PID | プロセスID |
| Status | `▶ Processing`（処理中）または `⏸ Waiting for input`（入力待ち） |
| CPU % | プロセスツリー全体のCPU使用率 |
| Working Directory | CLIの作業ディレクトリ |
| Terminal | ターミナルの種類（Windows Terminal, PowerShell 等） |

### 操作方法

| 操作 | 動作 |
|------|------|
| ダブルクリック / Enter | 選択したCLIのターミナルウィンドウをアクティブ化（前面に表示） |
| Refreshボタン | 手動でプロセス一覧を更新 |
| Always on Topチェック | ONにするとAI Managerウィンドウが常に前面に表示される |

### ステータス判定ロジック

ステータスは以下の2つのシグナルで判定されます（どちらかが閾値を超えると「Processing」）。

| シグナル | 閾値 | 説明 |
|----------|------|------|
| Tree CPU | 2.0% | プロセス＋全子プロセスのCPU使用率合計 |
| I/O Delta | 1,000 bytes | 前回スキャンからのディスクI/O変化量（API通信等を検出） |

### 設定の永続化

以下の設定はアプリケーション終了後も保持されます。

| 設定項目 | 保存先 |
|----------|--------|
| Always on Top | `settings.json` |

## ファイル構成

```
AI-Manager/
├── ai_manager.py          # メインアプリケーション
├── requirements.txt        # 依存パッケージ (psutil)
├── settings.json           # ユーザー設定（自動生成）
├── .gitignore
├── scripts/
│   └── windows/
│       └── launch_ai_manager.bat   # 起動用バッチファイル
└── README.md               # このファイル
```

## 技術的な補足

- **Pure Python + tkinter + ctypes** で構成。PowerShellは一切使用していません。
- **Win32 API (ctypes)**: `EnumWindows`, `SetForegroundWindow`, `AttachConsole`, `GetConsoleWindow` 等を使用してウィンドウの検出・アクティブ化を実現。
- **WSL対応**: `wsl --list` および `wsl -d <distro> -- ps aux` コマンドでWSL内のプロセスを検出。作業ディレクトリは `readlink` をバッチ実行して取得。
- **ウィンドウ切替**: Windows Terminal等のマルチタブ環境でも `AttachConsole`/`GetConsoleWindow` により正確なタブのHWNDを解決し、Alt キーシミュレーションで確実にフォアグラウンド化。

## 動作検証

| 環境 | CLI | 検証状況 |
|------|-----|----------|
| Windows | Claude Code | ✅ 検証済み |
| Windows | Codex CLI | ✅ 検証済み |
| Windows | GitHub Copilot CLI | ✅ 検証済み |
| WSL | Codex CLI | ✅ 検証済み |
| WSL | Claude Code | 未検証 |
| WSL | GitHub Copilot CLI | 未検証 |

## ❗このプロジェクトは MIT ライセンスの下で提供されています。詳細は LICENSE ファイルをご覧ください。
