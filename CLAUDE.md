# SpiritBox — Claude Code Orchestration Guide

PPTXファイルから、ナレーション付きPPSX または YouTube向けのナレーション付きスライド動画（MP4）を生成するパイプライン。
各ステップをPythonコードで実行し、ユーザーと対話しながら進める。

- 台本: Claude API（既定 `claude-opus-5-5`）。スピーカーノートをそのまま台本にすることもできる
- 音声: Gemini TTS（既定 `gemini-3.8-flash-tts`）
- 出力: PPSX（`assemble`）、MP4 + 字幕 + チャプター + サムネイル（`video`）、またはその両方

## パイプライン概要

```
init → extract → (ユーザーに質問) → script → (台本承認待ち) → voice → assemble (PPSX) / video (MP4)
```

## セットアップ確認

作業を始める前に確認すること：

```bash
# パッケージがインストールされているか
python -c "import spiritbox; print(spiritbox.__version__)"

# ANTHROPIC_API_KEY（台本生成。script_source が notes のときは不要）
echo $ANTHROPIC_API_KEY

# GOOGLE_API_KEY（音声生成）
echo $GOOGLE_API_KEY

# LibreOffice があるか
which soffice || ls /Applications/LibreOffice.app/Contents/MacOS/soffice

# ffmpeg があるか（動画出力のときだけ必要）
which ffmpeg
```

いずれかが欠けていたらユーザーに伝えて修正を依頼する。
APIキーは環境変数か、作業ディレクトリの `.env` ファイルで設定できる。

## Step 1: init — プロジェクト初期化

```python
from spiritbox.config import init_project
project_dir = init_project("path/to/presentation.pptx")
print(project_dir)  # → "presentation_spiritbox/"
```

- 引数のPPTXファイルパスはユーザーから受け取る
- `{stem}_spiritbox/` ディレクトリが作成され、sourceにPPTXがコピーされる

## Step 2: extract — テキスト抽出 & PDF変換

```python
from spiritbox.config import load_config
from spiritbox.extract import extract_all
from spiritbox.script import slides_without_notes

config = load_config(project_dir)
result = extract_all(project_dir, config)
# result = {"slides_data": [...], "pdf_path": "...", "slide_count": N}
# slides_data の各要素: slide_number, title, hidden, text, notes

noted = [s for s in result["slides_data"] if s["notes"].strip()]
print(f"ノートあり: {len(noted)}/{result['slide_count']} 枚")
print("ノートなし:", slides_without_notes(result["slides_data"]))
```

- LibreOffice でPDF変換を行うため少し時間がかかる
- 既にPDFがあればスキップされる（P6: Resumable Pipeline）

## Step 3: ユーザーにコンテキストを質問

extractが終わったらスライドの内容を把握した上で、ユーザーに以下を質問する：

1. **プレゼンの背景・目的** — 誰に向けて、何を伝えるプレゼンか？
2. **言語** — 日本語 (`ja`) or English (`en`)？ デフォルトは `ja`
3. **目標時間** — 全体で何分程度か？（0 = 制約なし）
4. **声のスタイル** — トーンや話し方の希望はあるか？（例：「落ち着いた男性の声」）。Gemini 3.x TTS にスタイル指示として渡される
5. **出力形式** — `ppsx`（PowerPoint自動再生）/ `video`（YouTube向けMP4）/ `both`
6. **台本の作り方（script_source）** — ノートが1枚以上あるときだけ質問する

### 台本の作り方（P9: Explicit Script Source）

スピーカーノートは単なるメモとして使われていることも多い。
**ノートがあるからといって自動でノートを台本に使ってはならない。** 必ずユーザーに選んでもらう。
判断材料として、ノートのあるスライド枚数と、ノートの冒頭数行の例を見せてから質問する。

| 値 | 意味 | 使う場面 |
|----|------|----------|
| `generate`（既定） | スライドからClaudeが台本を書く。ノートは背景情報としてだけ使い、読み上げない | ノートが空、またはメモ書き |
| `notes` | ノートをそのまま台本にする（LLMは呼ばない） | ノートに完成した台本を書いてある |
| `notes_refine` | ノートを下書きとして、Claudeが話し言葉に整える。内容・数字・順序は保つ | ノートに台本の下書きがある |

`notes` のとき、ノートが空のスライドはナレーションなし（無音で約3秒表示）になる。
`slides_without_notes` の結果をユーザーに伝え、そのままでよいか確認する。

回答を config.yaml に保存：

```python
from spiritbox.config import load_config, save_config

config = load_config(project_dir)
config.context = "ユーザーの回答をここに"
config.language = "ja"  # or "en"
config.duration_minutes = 10  # 0 = unconstrained
config.voice_style = "落ち着いた男性の声"  # optional
config.output_format = "video"  # "ppsx" / "video" / "both"
config.script_source = "generate"  # "generate" / "notes" / "notes_refine"
save_config(config, project_dir)
```

`output_format` が `video` / `both` のとき、台本は録画動画向け（視聴者に語りかける、フィラーなし）に書かれる。

## Step 4: script — 台本生成

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
print(f"Script saved: {project_dir}/script/master_script.md")
```

- `generate` / `notes_refine`: Claude API（`config.script_model`、既定 `claude-opus-5-5`）を呼ぶため1〜数分かかる場合がある
- `notes`: APIを呼ばず、ノートを `[---SLIDE_XX---]` 形式の台本に変換するだけ
- `script_model` を `gemini-3-pro-preview` にすると従来のGemini台本生成も使える

### **台本承認待ち（P2: Script First）**

生成された台本をユーザーに提示し、確認を求める（`notes` モードでも同じ）：

- `{project_dir}/script/master_script.md` を読んで内容を表示
- ユーザーが修正を希望したら、ファイルを直接編集するか再生成する
- **ユーザーの承認が得られるまで次のステップに進まない**

## Step 5: voice — 音声生成

```python
from spiritbox.config import load_config
from spiritbox.voice import generate_all_audio

config = load_config(project_dir)
durations = generate_all_audio(project_dir, config)
# durations = {1: 12300, 2: 8500, ...}  (ms per slide)
```

- スライドごとにWAVファイルを生成（既存ファイルはスキップ）
- 全スライド分で数分かかる場合がある

## Step 6: assemble — PPSX組み立て（output_format が ppsx / both のとき）

```python
from spiritbox.assemble import assemble_ppsx
from spiritbox.config import load_config

config = load_config(project_dir)
ppsx_path = assemble_ppsx(project_dir, durations, config)
print(f"Output: {ppsx_path}")
```

- 元のPPTXは変更されない（P8: Non-Destructive Output）
- `output/{stem}_narrated.ppsx` が生成される

## Step 7: video — YouTube向け動画（output_format が video / both のとき）

```python
from spiritbox.config import load_config
from spiritbox.video import render_video

config = load_config(project_dir)
result = render_video(project_dir, config)
print(result)
```

- `audio/slide_XXX.wav` とスライドPDFから、1920x1080 / H.264 / AAC のMP4を作る
- エンコードには動画の長さの1/6程度の時間がかかる（4コアで10分の動画が約100秒）
- 非表示スライドはPDFに出力されないので、動画にも含まれない

生成物と、ユーザーへの伝え方：

| ファイル | 内容 | YouTubeでの使い方 |
|----------|------|-------------------|
| `output/{stem}_narrated.mp4` | 動画本体 | アップロードする |
| `output/{stem}_narrated.srt` | 字幕（スライド内の文ごとの時刻は推定） | YouTube Studio →「字幕」→ ファイルをアップロード（タイミングあり） |
| `output/{stem}_narrated_chapters.txt` | チャプター一覧 | 説明欄に貼る。YouTubeは3個以上・各10秒以上のときだけチャプターを表示する（`result["chapter_count"]` を確認） |
| `output/{stem}_narrated_thumbnail.jpg` | 1枚目のスライドから作った 1280x720 サムネイル | カスタムサムネイルに使う |

## エラーリカバリ

全ステップは冪等・再開可能（P6: Resumable Pipeline）：

- **PDF変換失敗**: LibreOffice（Impress）がインストールされているか確認
- **Claude API エラー**: `ANTHROPIC_API_KEY` の設定を確認。安全性の判定で断られた場合（refusal）はコンテキストを調整して再実行
- **Gemini TTS エラー**: `GOOGLE_API_KEY` の設定を確認。一時的なエラーと空応答は自動で再試行される
- **音声生成の途中失敗**: `generate_all_audio` を再実行すれば、未生成のスライドのみ処理される
- **台本の再生成**: `script/master_script.md` を削除してStep 4を再実行
- **ffmpeg がない**: `brew install ffmpeg`
- **PDFのページ数とスライド数が合わない**: `slides/*.pdf` と `slides/images/` を削除してStep 2から再実行

## レジューム

途中で止まった場合、最後に成功したステップの次から再開できる：

```python
# どこまで進んだか確認
import os
project_dir = "presentation_spiritbox"
out = f"{project_dir}/output"
print("config.yaml:", os.path.exists(f"{project_dir}/config.yaml"))
print("PDF:", os.path.exists(f"{project_dir}/slides/presentation.pdf"))  # Step 2
print("Script:", os.path.exists(f"{project_dir}/script/master_script.md"))  # Step 4
print("Audio:", os.path.exists(f"{project_dir}/audio"))  # Step 5
print("PPSX:", any(f.endswith(".ppsx") for f in os.listdir(out)) if os.path.exists(out) else False)  # Step 6
print("MP4:", any(f.endswith(".mp4") for f in os.listdir(out)) if os.path.exists(out) else False)  # Step 7
```
