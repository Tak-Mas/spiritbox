# SpiritBox

PPTXにナレーションを付けてPPSXとして出力する自動プレゼンテーションナレーションシステム。
Gemini 3 Pro のマルチモーダル機能でスライドを読み取り、一貫した台本を生成し、Gemini TTS で音声化し、PPSXに埋め込む。

詳細な設計は [design.md](design.md) を参照。

## セットアップ

### 1. Google Cloud API の有効化

以下の2つのAPIを有効化する：

| API | 用途 | 有効化リンク |
|-----|------|-------------|
| **Generative Language API** | Gemini 3 Pro（台本生成） | [有効化](https://console.developers.google.com/apis/api/generativelanguage.googleapis.com/overview) |
| **Cloud Text-to-Speech API** | Gemini TTS（音声生成） | [有効化](https://console.developers.google.com/apis/api/texttospeech.googleapis.com/overview) |

APIキーに制限をかけている場合は、上記2つのAPIを許可リストに追加すること。

### 2. 環境変数

```bash
# ~/.zshrc に追加するか、プロジェクトルートに .env ファイルを作成
export GOOGLE_API_KEY="your-api-key"
```

### 3. システム依存

```bash
brew install --cask libreoffice   # PPTX → PDF変換に必要
```

### 4. インストール

```bash
pip install -e .
```

### 5. グローバルコマンド（任意）

```bash
bash install.sh    # どのディレクトリからでも /spiritbox が使える
```

## 使い方

Claude Code で `/spiritbox` コマンドを実行する：

```
/spiritbox deck.pptx
```

対話的にコンテキスト質問 → 台本確認 → 音声生成を進める。

## プロジェクト構造

```
spiritbox/
├── __init__.py      # パッケージ定義 (version)
├── config.py        # 設定管理 (SpiritBoxConfig, init_project)
├── extract.py       # テキスト抽出 & PDF変換
├── script.py        # Gemini 3 Pro で台本生成
├── voice.py         # Gemini TTS で音声生成
└── assemble.py      # PPSX 組み立て
```

### 生成されるプロジェクトディレクトリ

```
{name}_spiritbox/
├── config.yaml                    # 言語・時間・声・コンテキスト
├── source/presentation.pptx       # 元ファイルのコピー（変更されない）
├── slides/presentation.pdf        # Gemini 3 Pro 用PDF
├── script/master_script.md        # 生成された台本
├── audio/slide_001.wav ...        # スライドごとの音声
└── output/presentation_narrated.ppsx  # 最終出力
```
