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
| 表示モード切替 | `Wide` 表示、`Portrait` 表示、`Minimize` で入る `Restore` のみの小画面を切り替え可能。各表示のサイズと位置を個別に保持 |
| ラベル管理 | 作業ディレクトリごとにラベル名・色を保存。`Wide` 表示の Label 列と `Portrait` 表示の `+ Label` ボタンから編集可能 |
| ウィンドウ切替 | リストやカードをダブルクリックすると、そのCLIのターミナルウィンドウをアクティブ化。最小化されているウィンドウの復元も試行 |
| 作業ディレクトリ表示 | 各CLIが実行されているディレクトリを表示し、複数インスタンスの区別が可能 |
| ターミナル種別表示 | Windows Terminal / PowerShell / Command Prompt 等のターミナル種別を表示 |
| 常に前面表示 | `Top` チェックで、ウィンドウを常に前面に表示（設定は自動保存） |
| 1秒間隔の自動更新 | `Wide` / `Portrait` 表示では1秒ごとに自動更新。`Minimize` 後の小画面では停止し、`Restore` 時に即時更新 |

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

表示内容は表示モードによって変化します。以下の画像は一例です。

![](./images/00001.jpg)

### 各列の説明

| 列名 | 内容 |
|------|------|
| AI CLI | CLIの名称。WSLの場合は `(WSL:ディストリビューション名)` が付加される |
| PID | プロセスID |
| Status | `▶ Processing`（処理中）または `⏸ Waiting for input`（入力待ち） |
| CPU % | プロセスツリー全体のCPU使用率 |
| Label | ラベル名。未設定時は `+ Label`、ディレクトリ未取得時は `No Label` を表示（`Wide` 表示のみ） |
| Working Directory | CLIの作業ディレクトリ |
| Terminal | ターミナルの種類（Windows Terminal, PowerShell 等） |

### 操作方法

| 操作 | 動作 |
|------|------|
| `Portrait` / `Wide` ボタン | `Wide` 表示と `Portrait` 表示を切り替え。各表示のサイズと位置は個別に保存 |
| `Minimize` ボタン | `Restore` ボタンだけを表示する小さな画面に切り替え |
| `Restore` ボタン | `Minimize` 後の小画面から直前の表示と位置に戻り、即時リフレッシュ |
| Label列（`Wide` 表示） | Labelセルをクリックしてラベルを追加・編集 |
| `+ Label` ボタン（`Portrait` 表示） | カード上の `+ Label` をクリックしてラベルを追加・編集 |
| ダブルクリック / Enter | 選択したCLIのターミナルウィンドウをアクティブ化（前面に表示）。最小化中のウィンドウも復元を試行 |
| Refreshボタン | 手動でプロセス一覧を更新 |
| Topチェック | ONにするとAI Managerウィンドウが常に前面に表示される |

作業ディレクトリが取得できないプロセスにはラベルを保存できません。

### ステータス判定ロジック

Windows / WSL ともに、ステータスは以下の2つのシグナルで判定されます。どちらかが閾値を超えると `Processing`、両方とも閾値以下なら `Waiting for input` です。

| シグナル | 閾値 | 説明 |
|----------|------|------|
| Tree CPU | 2.0% | プロセス＋全子プロセスのCPU使用率合計 |
| I/O Delta | 1,000 bytes | 前回スキャンからのI/O合計増分 |

- Windows では `psutil` によるプロセスツリーのCPU使用率とI/Oカウンタを使用
- WSL では `/proc` のCPU ticksとI/O情報からCPU使用率とI/O増分を算出

### 設定の永続化

以下の設定は `settings.json` に保存され、アプリケーション終了後も保持されます。

| 設定項目 | 保存先 |
|----------|--------|
| Topチェックの状態 | `settings.json` (`always_on_top`) |
| 最後に使った通常表示モード（`Wide` または `Portrait`） | `settings.json` (`layout_mode`) |
| 各表示モードのウィンドウサイズと表示位置（`Wide` / `Portrait` / `Minimize` 後の小画面） | `settings.json` (`window_geometries.landscape` / `portrait` / `minimized`) |
| 作業ディレクトリごとのラベル名・色 | `settings.json` (`process_labels`) |

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
- **WSL対応**: `wsl --list` および `wsl -d <distro> -- ps -eo ...` でWSL内のプロセスを検出。作業ディレクトリは `readlink` をバッチ実行して取得。
- **ウィンドウ切替**: Windows Terminal等のマルチタブ環境でも `AttachConsole`/`GetConsoleWindow` により正確なタブのHWNDを解決し、必要に応じて親ウィンドウを復元してから前面化。

## 動作検証

| 環境 | CLI | 検証状況 |
|------|-----|----------|
| Windows | Claude Code | ✅ 検証済み |
| Windows | Codex CLI | ✅ 検証済み |
| Windows | GitHub Copilot CLI | ✅ 検証済み |
| WSL | Codex CLI | ✅ 検証済み |
| WSL | Claude Code | ✅ 検証済み |
| WSL | GitHub Copilot CLI | ✅ 検証済み |

## ❗このプロジェクトは MIT ライセンスの下で提供されています。詳細は LICENSE ファイルをご覧ください。
