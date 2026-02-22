# SpiritBox

PPTXにナレーションを付けてPPSXとして出力する自動プレゼンテーションナレーションシステム。
詳細な設計は [design.md](design.md) を参照。

## Prerequisites

### Google Cloud API

以下の3つのAPIを有効化する必要がある：

| API | 用途 | 有効化リンク |
|-----|------|-------------|
| **Generative Language API** | Gemini 3 Pro Preview（台本生成） | [有効化](https://console.developers.google.com/apis/api/generativelanguage.googleapis.com/overview) |
| **Cloud Text-to-Speech API** | Gemini TTS（音声生成） | [有効化](https://console.developers.google.com/apis/api/texttospeech.googleapis.com/overview) |
| **Vertex AI API** | （予備） | [有効化](https://console.developers.google.com/apis/api/aiplatform.googleapis.com/overview) |

APIキーに制限をかけている場合は、上記APIを許可リストに追加すること。

### Environment Variable

```bash
# ~/.zshrc に追加
export GOOGLE_API_KEY="your-api-key"
```

### System Dependencies

```bash
brew install --cask libreoffice   # PPTX → PDF変換
```

### Python Dependencies

```bash
pip install -r requirements.txt
```
