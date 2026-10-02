# SpiritBox

PPTXにナレーションを付けて、PPSX または YouTube向けのスライド動画（MP4）として出力する自動プレゼンテーションナレーションシステム。
Claude（claude-opus-5-5）でスライドPDFを読み取って一貫した台本を生成し、gemini-3.8-flash-tts で音声化し、PPSXに埋め込むか、MP4に書き出す。
スピーカーノートに書いた台本をそのまま（または整えて）使うこともできる。

詳細な設計は [design.md](design.md) を参照。

## セットアップ

### 1. Anthropic API キー（台本生成）

[Claude Console](https://platform.claude.com/) でAPIキーを発行する。
台本をスピーカーノートからそのまま作る場合（`script_source: notes`）は不要。

### 2. Google Cloud API の有効化（音声生成）

以下のAPIを有効化する：

| API | 用途 | 有効化リンク |
|-----|------|-------------|
| **Generative Language API** | gemini-3.8-flash-tts（音声生成）、gemini-3-pro-preview（台本をGeminiで生成する場合） | [有効化](https://console.developers.google.com/apis/api/generativelanguage.googleapis.com/overview) |
| **Cloud Text-to-Speech API** | Gemini TTS（音声生成） | [有効化](https://console.developers.google.com/apis/api/texttospeech.googleapis.com/overview) |

APIキーに制限をかけている場合は、上記のAPIを許可リストに追加すること。

### 3. 環境変数

```bash
# ~/.zshrc に追加するか、プロジェクトルートに .env ファイルを作成
export ANTHROPIC_API_KEY="your-anthropic-api-key"
export GOOGLE_API_KEY="your-google-api-key"
```

### 4. システム依存

```bash
brew install --cask libreoffice   # PPTX → PDF変換に必要
brew install ffmpeg               # 動画出力（MP4）に必要
```

### 5. インストール

```bash
pip install -e .
```

### 6. グローバルコマンド（任意）

```bash
bash install.sh    # どのディレクトリからでも /spiritbox が使える
```

## 使い方

Claude Code で `/spiritbox` コマンドを実行する：

```
/spiritbox deck.pptx
```

対話的にコンテキスト質問 → 台本確認 → 音声生成 → PPSX / 動画の出力を進める。

### 主な設定（config.yaml）

| キー | 値 | 説明 |
|------|-----|------|
| `output_format` | `ppsx` / `video` / `both` | 出力形式。`video` はYouTube向けMP4・字幕・チャプター・サムネイルを作る |
| `script_source` | `generate` / `notes` / `notes_refine` | 台本の出どころ。ノートはメモの場合もあるので、自動では切り替えず必ず確認する |
| `script_model` | `claude-opus-5-5`（既定） | `gemini-3-pro-preview` も指定できる |
| `tts_model` | `gemini-3.8-flash-tts`（既定） | `gemini-2.5-pro-preview-tts` も指定できる |

## プロジェクト構造

```
spiritbox/
├── __init__.py      # パッケージ定義 (version)
├── config.py        # 設定管理 (SpiritBoxConfig, init_project)
├── extract.py       # テキスト抽出 & PDF変換
├── script.py        # claude-opus-5-5 で台本生成（またはノートから台本化）
├── voice.py         # gemini-3.8-flash-tts で音声生成
├── assemble.py      # PPSX 組み立て
└── video.py         # YouTube向けMP4・字幕・チャプター・サムネイル
```

### 生成されるプロジェクトディレクトリ

```
{name}_spiritbox/
├── config.yaml                    # 言語・時間・声・コンテキスト
├── source/presentation.pptx       # 元ファイルのコピー（変更されない）
├── slides/presentation.pdf        # 台本モデル用PDF
├── slides/images/page_001.png ... # 動画用スライド画像
├── script/master_script.md        # 生成された台本
├── audio/slide_001.wav ...        # スライドごとの音声
└── output/
    ├── presentation_narrated.ppsx           # 最終出力（ppsx / both）
    ├── presentation_narrated.mp4            # 最終出力（video / both）
    ├── presentation_narrated.srt            # 字幕
    ├── presentation_narrated_chapters.txt   # YouTubeチャプター
    └── presentation_narrated_thumbnail.jpg  # サムネイル
```
