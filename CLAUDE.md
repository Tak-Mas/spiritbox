# SpiritBox — Claude Code Orchestration Guide

PPTXファイルからナレーション付きPPSXを生成するパイプライン。
各ステップをPythonコードで実行し、ユーザーと対話しながら進める。

## パイプライン概要

```
init → extract → (ユーザーにコンテキスト質問) → script → (台本承認待ち) → voice → assemble
```

## セットアップ確認

作業を始める前に確認すること：

```bash
# パッケージがインストールされているか
python -c "import spiritbox; print(spiritbox.__version__)"

# GOOGLE_API_KEY が設定されているか
echo $GOOGLE_API_KEY

# LibreOffice があるか
which soffice || ls /Applications/LibreOffice.app/Contents/MacOS/soffice
```

いずれかが欠けていたらユーザーに伝えて修正を依頼する。

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

config = load_config(project_dir)
result = extract_all(project_dir, config)
# result = {"slides_data": [...], "pdf_path": "...", "slide_count": N}
```

- LibreOffice でPDF変換を行うため少し時間がかかる
- 既にPDFがあればスキップされる（P6: Resumable Pipeline）

## Step 3: ユーザーにコンテキストを質問

extractが終わったらスライドの内容を把握した上で、ユーザーに以下を質問する：

1. **プレゼンの背景・目的** — 誰に向けて、何を伝えるプレゼンか？
2. **言語** — 日本語 (`ja`) or English (`en`)？ デフォルトは `ja`
3. **目標時間** — 全体で何分程度か？（0 = 制約なし）
4. **声のスタイル** — トーンや話し方の希望はあるか？（例：「落ち着いた男性の声」）

回答を config.yaml に保存：

```python
from spiritbox.config import load_config, save_config

config = load_config(project_dir)
config.context = "ユーザーの回答をここに"
config.language = "ja"  # or "en"
config.duration_minutes = 10  # 0 = unconstrained
config.voice_style = "落ち着いた男性の声"  # optional
save_config(config, project_dir)
```

## Step 4: script — 台本生成

```python
from spiritbox.config import load_config
from spiritbox.extract import extract_all
from spiritbox.script import generate_master_script, save_master_script

config = load_config(project_dir)
extraction = extract_all(project_dir, config)
script_text = generate_master_script(
    project_dir, config,
    extraction["slides_data"], extraction["pdf_path"]
)
path = save_master_script(script_text, project_dir)
print(f"Script saved: {path}")
```

- gemini-3-pro-preview APIを呼ぶため30秒〜1分かかる場合がある

### **台本承認待ち（P2: Script First）**

生成された台本をユーザーに提示し、確認を求める：

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

## Step 6: assemble — PPSX組み立て

```python
from spiritbox.assemble import assemble_ppsx
from spiritbox.config import load_config

config = load_config(project_dir)
ppsx_path = assemble_ppsx(project_dir, durations, config)
print(f"Output: {ppsx_path}")
```

- 元のPPTXは変更されない（P8: Non-Destructive Output）
- `output/{stem}_narrated.ppsx` が生成される

## エラーリカバリ

全ステップは冪等・再開可能（P6: Resumable Pipeline）：

- **PDF変換失敗**: LibreOfficeがインストールされているか確認
- **API エラー**: `GOOGLE_API_KEY` の設定を確認。Safety filterでブロックされた場合はプロンプトの調整が必要
- **音声生成の途中失敗**: `generate_all_audio` を再実行すれば、未生成のスライドのみ処理される
- **台本の再生成**: `script/master_script.md` を削除してStep 4を再実行

## レジューム

途中で止まった場合、最後に成功したステップの次から再開できる：

```python
# どこまで進んだか確認
import os
project_dir = "presentation_spiritbox"
print("config.yaml:", os.path.exists(f"{project_dir}/config.yaml"))
print("PDF:", os.path.exists(f"{project_dir}/slides/presentation.pdf"))  # Step 2
print("Script:", os.path.exists(f"{project_dir}/script/master_script.md"))  # Step 4
print("Audio:", os.path.exists(f"{project_dir}/audio"))  # Step 5
print("PPSX:", any(f.endswith(".ppsx") for f in os.listdir(f"{project_dir}/output")) if os.path.exists(f"{project_dir}/output") else False)
```
