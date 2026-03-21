<p align="left">
  <a href="README_en.md"><img src="https://img.shields.io/badge/English Mode-blue.svg" alt="English"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/日本語 モード-red.svg" alt="日本語"></a>
</p>

# AI CLI Watcher - AI CLI Process Monitor

AI系CLIツール（Claude Code / Codex CLI / GitHub Copilot CLI）の稼働状況をリアルタイムで監視するWindows向けデスクトップアプリケーションです。

- 各プロセスのステータス（処理中/ユーザー入力待ち）を一覧で把握できます。
- プロセスをダブルクリックすることで、ウィンドウをアクティブ表示できます。
- 別の仮想デスクトップで開いている Windows Terminal の前面化にも対応しています。

> [!IMPORTANT]
> 仮想デスクトップを使っていても、別のデスクトップで開いている Windows Terminal をリストやカードからアクティブ化できます。

## 機能一覧

| 機能                 | 説明                                                                                                                       |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| プロセス自動検出     | Windows上およびWSL上で動作するAI CLIプロセスを自動的に検出                                                                 |
| ステータス表示       | 各プロセスの状態を「Processing（処理中）」「Waiting for input（入力待ち）」で表示                                          |
| 色分け表示           | 入力待ち＝緑系、処理中＝赤系の背景色で一目で判別可能                                                                       |
| 表示モード切替       | `Table` (一覧表) 表示、`Cards` (縦長カード) 表示、`Minimize` (最小化) 表示を切り替え可能。各表示のサイズと位置を個別に保持 |
| ラベル管理           | 作業ディレクトリごとにラベル名・色を保存。`Table` 表示の Label 列と `Cards` 表示の `+ Label` ボタンから編集可能            |
| 仮想デスクトップ対応 | 別の仮想デスクトップで開いている Windows Terminal も、リストやカードからアクティブ化可能                                  |
| ウィンドウ切替       | リストやカードをダブルクリックすると、そのCLIのターミナルウィンドウをアクティブ化。最小化されているウィンドウの復元も試行  |
| 作業ディレクトリ表示 | 各CLIが実行されているディレクトリを表示し、複数インスタンスの区別が可能                                                    |
| ターミナル種別表示   | Windows Terminal / PowerShell / Command Prompt 等のターミナル種別を表示                                                    |
| 常に前面表示         | `Always on Top` チェックで、ウィンドウを常に前面に表示（設定は自動保存）                                                   |
| 自動更新             | `Table` / `Cards` 表示では既定で2秒ごとに自動更新。`settings.json` で変更可能。`Minimize` 表示では停止し、`Restore` 時に即時更新 |

## 対応CLI

| CLI                     | Windows | WSL |
| ----------------------- | ------- | --- |
| Claude Code (Anthropic) | ✅      | ✅  |
| Codex CLI (OpenAI)      | ✅      | ✅  |
| GitHub Copilot CLI      | ✅      | ✅  |

- 各CLIの複数インスタンスの同時検出に対応
- node/npm/npx 経由で起動されたプロセスも検出可能
- VS Code拡張のバックグラウンドプロセスやWindows Copilotアプリ等の誤検出を除外

## 動作環境

- **OS**: Windows 10 / 11
- **配布版の実行環境**: 追加ランタイム不要
- **ソースからのビルド環境**: `.NET 10 SDK`
- **WSL監視を使う場合**: WSL と、各ディストリビューション内の `python3` を推奨

`python3` が無くても、Windows側の監視と WSL プロセスの基本検出自体は動作します。
ただし WSL 監視では、作業ディレクトリ取得、I/O 情報の取得、CPU/I/O を使った詳細なステータス判定の精度に制限が出ます。

## 起動方法

Releases から配布用の `app` フォルダをダウンロード後、解凍して中にある `AI-CLI-Watcher.exe` を実行してください。
追加の `.NET Runtime` は不要です。

---

※srcからビルドを行う場合、以下のように PowerShell スクリプトを実行してください。

```
.\publish.ps1 -CleanOutput
```

`publish.ps1` は自己完結の single-file publish を使うため、`app/` 直下は基本的に `AI-CLI-Watcher.exe` と `settings.json` だけになります。
古い publish 形式から切り替える場合は、最初の1回だけ `-CleanOutput` を付けて出し直してください。

## 使い方

### 画面構成

表示内容は表示モードによって変化します。以下は各表示モードのサンプルです。

| 表示モード | サンプル画像            |
| ---------- | ----------------------- |
| `Table`    | ![](./images/00001.jpg) |
| `Cards`    | ![](./images/00002.jpg) |
| `Minimize` | ![](./images/00003.jpg) |

### 各項目の説明

| 項目名    | 内容                                                                                                                                                                                       |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| AI CLI    | CLIの名称。WSLの場合は `(WSL:ディストリビューション名)` が付加される                                                                                                                       |
| PID       | プロセスID                                                                                                                                                                                 |
| Status    | `▶ Processing`（処理中）または `⏸ Waiting for input`（入力待ち）                                                                                                                           |
| CPU %     | プロセスツリー全体のCPU使用率                                                                                                                                                              |
| Label     | ラベル名。`Table` 表示では Label 項目に表示され、未設定時は `+ Label`、ディレクトリ未取得時は `No Label` を表示。`Cards` 表示ではカード上の `+ Label` ボタンまたは保存済みラベルとして表示 |
| Directory | CLIの作業ディレクトリ。長いパスは末尾を残して省略表示                                                                                                                                      |
| Terminal  | ターミナルの種類（Windows Terminal, PowerShell 等）                                                                                                                                        |

### 操作方法

| 操作                     | 動作                                                                                            |
| ------------------------ | ----------------------------------------------------------------------------------------------- |
| `Cards` / `Table` ボタン | `Table` 表示と `Cards` 表示を切り替え。各表示のサイズと位置は個別に保存                         |
| `Minimize` ボタン        | `Restore` ボタンだけを表示する小さな画面に切り替え                                              |
| `Restore` ボタン         | `Minimize` 後の小画面から直前の表示と位置に戻り、即時リフレッシュ                               |
| `+ Label` ボタン         | `+ Label` をクリックしてラベルを追加・編集                                                      |
| ダブルクリック / Enter   | 選択したCLIのターミナルウィンドウをアクティブ化（前面に表示）。最小化中のウィンドウも復元を試行。別の仮想デスクトップ上の Windows Terminal にも対応 |
| ステータス欄表示         | 現在時刻に加えて、`Auto refresh` または直近の `Scan` 所要時間を表示                             |
| `Always on Top` チェック | ONにするとAI CLI Watcherウィンドウが常に前面に表示される                                        |

作業ディレクトリが取得できないプロセスにはラベルを保存できません。

### ステータス判定ロジック

Windows / WSL ともに、ステータスは以下の2つのシグナルで判定されます。どちらかが閾値を超えると `Processing`、両方とも閾値以下なら `Waiting for input` です。

| シグナル  | 閾値                 | 説明                                                                       |
| --------- | -------------------- | -------------------------------------------------------------------------- |
| Tree CPU  | 2.0%                 | プロセス＋全子プロセスのCPU使用率合計                                      |
| I/O Delta | 1,000 activity score | 前回スキャンからのI/O活動量増分（Windowsはbytesに加えてI/O操作回数も加味） |

- Windows では C# / Win32 ベースのネイティブ実装でプロセスツリー情報を取得
- WSL では `ps` と `/proc` を使用し、`python3` が利用できる場合は CPU ticks / I/O 情報 / 作業ディレクトリも取得

### 設定の永続化

以下の設定は `settings.json` に保存され、アプリケーション終了後も保持されます。
起動時に `settings.json` が存在しない場合、JSONとして読めない場合、または設定構成が不正な場合は、管理対象の設定だけをシステム初期値で補完・正規化した内容で自動再生成されます。

| 設定項目                                                                   | 保存先                                                                     |
| -------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `Always on Top` チェックの状態                                             | `settings.json` (`always_on_top`)                                          |
| 最後に使った通常表示モード（`Table` または `Cards`）                       | `settings.json` (`layout_mode`)                                            |
| 自動更新間隔                                                               | `settings.json` (`refresh_interval_ms`)                                    |
| ステータス欄の詳細表示内容                                                 | `settings.json` (`status_detail_mode`)                                     |
| 各表示モードのウィンドウサイズと表示位置（`Table` / `Cards` / `Minimize`） | `settings.json` (`window_geometries.landscape` / `portrait` / `minimized`) |
| 作業ディレクトリごとのラベル名・色                                         | `settings.json` (`process_labels`)                                         |

主な設定値は以下の通りです。

| キー                   | 型       | 既定値               | 許容値 / 説明                                                                 |
| ---------------------- | -------- | -------------------- | ----------------------------------------------------------------------------- |
| `always_on_top`        | boolean  | `false`              | `true` / `false`                                                              |
| `layout_mode`          | string   | `"landscape"`        | `"landscape"` / `"portrait"`                                                  |
| `refresh_interval_ms`  | number   | `2000`               | `1000`, `2000`, `3000`, `5000`。無効値は `2000` に正規化                     |
| `status_detail_mode`   | string   | `"refresh_interval"` | `"refresh_interval"` / `"refresh_interval_ms"` で更新間隔表示、`"scan_duration"` / `"scan_duration_ms"` で直近スキャン時間表示 |

設定例:

```json
{
  "always_on_top": false,
  "layout_mode": "landscape",
  "refresh_interval_ms": 2000,
  "status_detail_mode": "refresh_interval"
}
```

## ファイル構成

```
AI-CLI-Watcher/
├── .gitignore                   # ルートの除外設定
├── LICENSE                      # ライセンス
├── README.md                    # 日本語README
├── README_en.md                 # 英語README
├── publish.ps1                  # 配布用ビルドスクリプト
├── images/                      # README掲載用の画面サンプル
│   ├── 00001.jpg
│   ├── 00002.jpg
│   └── 00003.jpg
└── src/
    ├── .gitignore               # ビルド生成物の除外設定
    ├── AI-CLI-Watcher.sln       # ソリューション
    ├── AI-CLI-Watcher.csproj    # WPFプロジェクト定義
    ├── App.xaml                 # アプリケーション定義
    ├── App.xaml.cs              # アプリケーション初期化
    ├── MainWindow.xaml          # メインウィンドウUI
    ├── MainWindow.xaml.cs       # メインウィンドウのロジック
    ├── AssemblyInfo.cs          # アセンブリ情報
    ├── app_icon.ico             # アプリアイコン
    ├── Helpers/
    │   └── ColorHelper.cs       # 色関連ヘルパー
    ├── Models/
    │   ├── AppSettings.cs       # 設定モデル
    │   ├── CliDefinition.cs     # 監視対象CLI定義
    │   └── CliProcess.cs        # 検出プロセスモデル
    ├── Services/
    │   ├── ProcessScanner.cs    # Windows側プロセス検出
    │   ├── SettingsService.cs   # 設定の読み書き
    │   ├── Win32Api.cs          # Win32 API連携
    │   └── WslScanner.cs        # WSL側プロセス検出
    ├── Themes/
    │   └── DarkTheme.xaml       # テーマ定義
    └── Views/
        ├── LabelEditorDialog.xaml    # ラベル編集ダイアログUI
        └── LabelEditorDialog.cs      # ラベル編集ダイアログ処理
```

※ `app/`、`src/bin/`、`src/obj/`、`settings.json` はビルドや実行時に生成されるため省略しています。

## 動作検証

| 環境    | CLI                | 検証状況    |
| ------- | ------------------ | ----------- |
| Windows | Claude Code        | ✅ 検証済み |
| Windows | Codex CLI          | ✅ 検証済み |
| Windows | GitHub Copilot CLI | ✅ 検証済み |
| WSL     | Codex CLI          | ✅ 検証済み |
| WSL     | Claude Code        | ✅ 検証済み |
| WSL     | GitHub Copilot CLI | ✅ 検証済み |

## ❗このプロジェクトは MIT ライセンスの下で提供されています。詳細は LICENSE ファイルをご覧ください。
