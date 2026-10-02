SpiritBoxパイプラインを実行し、PPTXファイルからナレーション付きPPSX、またはYouTube向けのナレーション付きスライド動画（MP4）を生成する。

ユーザーの入力: $ARGUMENTS

## 前提条件の確認

まず以下を確認し、問題があればユーザーに伝えて修正を依頼してください：

```bash
# spiritbox パッケージ
python -c "import spiritbox; print(spiritbox.__version__)"

# ANTHROPIC_API_KEY（台本生成。script_source が notes のときは不要）
echo $ANTHROPIC_API_KEY

# GOOGLE_API_KEY（音声生成）
echo $GOOGLE_API_KEY

# LibreOffice
which soffice || ls /Applications/LibreOffice.app/Contents/MacOS/soffice

# ffmpeg（動画出力のときだけ必要）
which ffmpeg
```

## パイプライン

以下のステップを順に実行してください。全ステップは冪等・再開可能です。

### Step 1: init — プロジェクト初期化

```python
from spiritbox.config import init_project
project_dir = init_project("$ARGUMENTS")
print(project_dir)
```

- `$ARGUMENTS` にユーザーが指定したPPTXファイルパスが入る
- カレントディレクトリに `{stem}_spiritbox/` が作成される

### Step 2: extract — テキスト抽出 & PDF変換

```python
from spiritbox.config import load_config
from spiritbox.extract import extract_all
from spiritbox.script import slides_without_notes

config = load_config(project_dir)
result = extract_all(project_dir, config)
print(f"Extracted {result['slide_count']} slides")
noted = [s for s in result["slides_data"] if s["notes"].strip()]
print(f"ノートあり: {len(noted)} 枚 / ノートなし: {slides_without_notes(result['slides_data'])}")
```

### Step 3: ユーザーにコンテキストを質問

スライドの内容を把握した上で、ユーザーに以下を質問してください：

1. **プレゼンの背景・目的** — 誰に向けて、何を伝えるプレゼンか？
2. **言語** — 日本語 (`ja`) or English (`en`)？ デフォルトは `ja`
3. **目標時間** — 全体で何分程度か？（0 = 制約なし）
4. **声のスタイル** — トーンや話し方の希望（例：「落ち着いた男性の声」）
5. **出力形式** — `ppsx`（PowerPoint自動再生）/ `video`（YouTube向けMP4）/ `both`
6. **台本の作り方** — ノートが1枚以上あるときだけ質問する（下記）

**台本の作り方（script_source）**: ノートはメモとして使われていることも多いので、ノートがあっても自動でノートを台本にしないでください。
ノートのある枚数と冒頭数行の例を見せた上で、次の3つから選んでもらいます。

- `generate`（既定）: スライドからClaudeが台本を書く。ノートは背景情報としてだけ使い、読み上げない
- `notes`: ノートをそのまま台本にする。ノートが空のスライドはナレーションなしになる
- `notes_refine`: ノートを下書きとして、Claudeが内容を保ったまま話し言葉に整える

回答を config に反映：

```python
from spiritbox.config import load_config, save_config

config = load_config(project_dir)
config.context = "ユーザーの回答をここに"
config.language = "ja"
config.duration_minutes = 0
config.voice_style = ""
config.output_format = "ppsx"  # "ppsx" / "video" / "both"
config.script_source = "generate"  # "generate" / "notes" / "notes_refine"
save_config(config, project_dir)
```

### Step 4: script — 台本生成

```python
from spiritbox.config import load_config
from spiritbox.extract import extract_all
from spiritbox.script import generate_master_script

config = load_config(project_dir)
extraction = extract_all(project_dir, config)
script_text = generate_master_script(
    project_dir, config,
    extraction["slides_data"], extraction["pdf_path"]
)
```

### Step 4.5: 台本承認待ち（重要）

台本が生成されたら（`notes` モードでも）、以下の形式でユーザーに提示してください：

1. `{project_dir}/script/master_script.md` の**フルパス**をリンクとして提示する
2. 「台本を確認してください。直接ファイルを編集することもできます。OKならそのまま進めます。」というメッセージを添える
3. 台本の全文をチャットに貼り付ける必要はない（ファイルで確認してもらう）

**ユーザーの承認が得られるまで次のステップに進まないでください。**
修正を希望された場合はファイルを編集するか再生成してください。

### Step 5: voice — 音声生成

```python
from spiritbox.config import load_config
from spiritbox.voice import generate_all_audio

config = load_config(project_dir)
durations = generate_all_audio(project_dir, config)
total = sum(durations.values()) / 1000
print(f"Generated audio for {len(durations)} slides ({total:.1f}s total)")
```

### Step 6: assemble — PPSX組み立て（output_format が ppsx / both）

```python
from spiritbox.assemble import assemble_ppsx
from spiritbox.config import load_config

config = load_config(project_dir)
ppsx_path = assemble_ppsx(project_dir, durations, config)
print(f"Output: {ppsx_path}")
```

### Step 7: video — YouTube向け動画（output_format が video / both）

```python
from spiritbox.config import load_config
from spiritbox.video import render_video

config = load_config(project_dir)
result = render_video(project_dir, config)
print(result)
```

完了したら、次の4つをユーザーに伝えてください：

- `video`: アップロードするMP4（1920x1080）
- `subtitles`: YouTube Studio の「字幕」にアップロードできるSRT（文ごとの時刻は推定）
- `chapters`: 説明欄に貼るチャプター一覧。`chapter_count` が3未満のときはYouTubeにチャプターが表示されないことも伝える
- `thumbnail`: 1280x720 のサムネイル

## エラーリカバリ

- **LibreOffice未インストール**: `brew install --cask libreoffice`
- **ffmpeg未インストール**: `brew install ffmpeg`
- **ANTHROPIC_API_KEY未設定**: `export ANTHROPIC_API_KEY="..."`
- **GOOGLE_API_KEY未設定**: `export GOOGLE_API_KEY="..."`
- **spiritbox未インストール**: SpiritBoxリポジトリで `pip install -e .`
- **Claudeが台本生成を断った（refusal）**: コンテキストを調整して再実行。ユーザーに報告
- **途中失敗**: 各ステップは再実行で途中から再開される（既存ファイルをスキップ）
- **台本再生成**: `{project_dir}/script/master_script.md` を削除してStep 4を再実行
