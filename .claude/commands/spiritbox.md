SpiritBoxパイプラインを実行し、PPTXファイルからナレーション付きPPSXを生成する。

ユーザーの入力: $ARGUMENTS

## 前提条件の確認

まず以下を確認し、問題があればユーザーに伝えて修正を依頼してください：

```bash
# spiritbox パッケージ
python -c "import spiritbox; print(spiritbox.__version__)"

# GOOGLE_API_KEY
echo $GOOGLE_API_KEY

# LibreOffice
which soffice || ls /Applications/LibreOffice.app/Contents/MacOS/soffice
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

config = load_config(project_dir)
result = extract_all(project_dir, config)
print(f"Extracted {result['slide_count']} slides")
```

### Step 3: ユーザーにコンテキストを質問

スライドの内容を把握した上で、ユーザーに以下を質問してください：

1. **プレゼンの背景・目的** — 誰に向けて、何を伝えるプレゼンか？
2. **言語** — 日本語 (`ja`) or English (`en`)？ デフォルトは `ja`
3. **目標時間** — 全体で何分程度か？（0 = 制約なし）
4. **声のスタイル** — トーンや話し方の希望（例：「落ち着いた男性の声」）

回答を config に反映：

```python
from spiritbox.config import load_config, save_config

config = load_config(project_dir)
config.context = "ユーザーの回答をここに"
config.language = "ja"
config.duration_minutes = 0
config.voice_style = ""
save_config(config, project_dir)
```

### Step 4: script — 台本生成

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
```

### Step 4.5: 台本承認待ち（重要）

台本が生成されたら、以下の形式でユーザーに提示してください：

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

### Step 6: assemble — PPSX組み立て

```python
from spiritbox.assemble import assemble_ppsx
from spiritbox.config import load_config

config = load_config(project_dir)
ppsx_path = assemble_ppsx(project_dir, durations, config)
print(f"Output: {ppsx_path}")
```

## エラーリカバリ

- **LibreOffice未インストール**: `brew install --cask libreoffice`
- **GOOGLE_API_KEY未設定**: `export GOOGLE_API_KEY="..."`
- **spiritbox未インストール**: SpiritBoxリポジトリで `pip install -e .`
- **API Safety Filter**: プロンプト調整が必要。ユーザーに報告
- **途中失敗**: 各ステップは再実行で途中から再開される（既存ファイルをスキップ）
- **台本再生成**: `{project_dir}/script/master_script.md` を削除してStep 4を再実行
