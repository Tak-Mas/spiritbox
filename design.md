# DESIGN.md — SpiritBox: Automated Presentation Narration System

## Philosophy

プレゼンテーションは「視覚的なスライド」と「聴覚的な語り」が組み合わさって
初めて完成する、一連のストーリーテリングである。

SpiritBoxは、完成したスライド（PPTX）の全貌をGeminiの大容量コンテキストで
一度に読み取り、全体を通した発表台本を構築する。
その台本をgemini-2.5-pro-preview-ttsで音声化し、元のPPTXに埋め込んで
「ナレーション付き自動再生プレゼンテーション（PPSX）」として出力する。

単なるテキスト読み上げではなく、図表の解釈、スライド間の繋ぎ、
発表者としての自然な語りを自動構築することを目的とする。

SpiritBoxはClaude Code上で動作するAgentである。
ユーザーはPPTXを渡して指示するだけで、スクリプトと音声がついたPPSXが返ってくる。
スタンドアロンCLIは提供せず、Claude Codeの `/spiritbox` コマンドが唯一のインターフェースである。

---

## Scopes

- `config`: プロジェクト初期化・設定管理（`init_project`, `load_config`, `save_config`）
- `extract`: PPTXからPDF変換・テキスト・スピーカーノートを一括抽出
- `script`: 全スライド情報をgemini-3-pro-previewに一括入力し、一貫性のあるマスター台本を生成
- `voice`: マスター台本をスライドごとに分割し、gemini-2.5-pro-preview-ttsで音声ファイルを生成
- `assemble`: 生成した音声を元のPPTXに埋め込み、スライド自動送りを設定してPPSXとして出力

---

## Principles

### P1: Master Script First — 全体最適化されたストーリーテリング
全スライドを一度に入力し「最初から最後まで筋の通った1つのマスター台本」を生成する。
スライドごとの逐次生成では全体の一貫性が失われ、後の展開を先回りして語る破綻が生じる。

### P2: Script First — 音声化の前に必ずユーザーの承認を挟む
台本を生成したら会話内で提示し、同時にMarkdownファイル（master_script.md）としても保存する。
ユーザーは会話内で修正指示を出すか、エディタでMarkdownを直接編集できる。
ユーザーがOKするまで音声生成には進まない。

### P3: Spoken Language — 「書き言葉」ではなく「話し言葉」
スライドの箇条書きや体言止めをそのまま読み上げず、
自然な話し言葉に翻訳する。聴衆に語りかける構成にすることが台本生成の最重要任務。

### P4: Visual Context Grounding — 視覚情報の言語化
gemini-3-pro-previewでグラフや図解を読み取り、
「こちらのグラフをご覧ください」のように視覚要素に言及するプレゼン特有の言い回しを台本に組み込む。

### P5: Slide Marker Protocol — 台本内の分割マーカー
マスター台本内に `[---SLIDE_01---]` 形式のマーカーを挿入し、スライドの切り替わり位置を明示する。
語り口の連続性を保ちつつ、音声生成時の分割ポイントとして機能させる。

### P6: Resumable Pipeline — 再開可能なパイプライン
各フェーズの出力はファイルとしてディスクに永続化する。
途中で中断しても再開でき、特定スライドの音声だけを再生成することも可能。

### P7: Convention over Configuration — 規約による構造化
プロジェクトのディレクトリ構成は固定の規約に従う。
ユーザーが設定するのは「トーン」「対象聴衆」「声の種類」「言語」のみ。

### P8: Non-Destructive Output — 元ファイルを壊さない
入力されたPPTXは一切変更しない。ナレーション付きPPSXは別ファイルとして新規作成する。

---

## Architecture

### Execution Model

    User ←→ Claude Code (Orchestrator)
                  │
                  ├── Python scripts を実行
                  │     ├── extract.py    → スライド抽出
                  │     ├── script.py     → gemini-3-pro-preview で台本生成
                  │     ├── voice.py      → gemini-2.5-pro-preview-tts で音声生成
                  │     └── assemble.py   → PPTX + 音声 → PPSX
                  │
                  └── Google Generative Language API
                        ├── gemini-3-pro-preview (Vision + 台本生成)
                        └── gemini-2.5-pro-preview-tts (音声生成)

Claude CodeがOrchestratorとして全体のワークフローを管理する。
ユーザーはClaude Codeとの会話の中でPPTXを渡し、指示を出す。

### Google Cloud API

以下の2つのAPIの有効化が必要：

| 用途 | API | エンドポイント | モデル |
|------|-----|----------------|--------|
| スライド読み取り + 台本生成 | Generative Language API | generativelanguage.googleapis.com | gemini-3-pro-preview |
| 音声生成 | Cloud Text-to-Speech API | texttospeech.googleapis.com | gemini-2.5-pro-preview-tts |

SDK: `google-genai` を使用。認証は `GOOGLE_API_KEY` で指定。
`python-dotenv` により、環境変数またはプロジェクトルートの `.env` ファイルから読み込む。

### gemini-2.5-pro-preview-tts 制約

- テキスト上限: 4,000文字 / リクエスト
- 音声上限: 約655秒 / リクエスト
- 出力: 24kHz PCM（WAVに変換して使用）
- スタイル制御: 自然言語プロンプト + マークアップタグ（[whispering], [laughing] 等）
- 対応言語: 日本語 (ja-JP) ★GA、英語 (en-US) ★GA

### gemini-3-pro-preview (Vision + 台本生成) 制約

- コンテキストウィンドウ: 1M tokens
- PDFを直接入力（PPTX→PDF変換後、Gemini APIにPDFとして送信）
- 台本品質を最優先するため、最上位モデル（gemini-3-pro-preview）を使用

### PPSX 組み立て技術

python-pptx で音声埋め込み後、ZIP-level post-processing で自動再生を設定:

1. `shapes.add_movie()` で音声ファイル（WAV）をスライドに埋め込み（`<p:video>` として生成される）
2. 保存後、ZIPレベルでタイミングXMLの `delay="indefinite"` → `delay="0"` に置換（自動再生化）
3. `<p:transition advTm="X">` で音声の長さに合わせたスライド自動送り
4. Content-Type置換で .ppsx 形式に変換（ダブルクリックでスライドショー直接開始）
5. 出力ファイル名の衝突回避（自動連番付与）

参考実装: [AnnotatePptx](https://github.com/yoonghm/AnnotatePptx)

---

## Project Directory Structure

    {project_name}_spiritbox/
    ├── config.yaml              # トーン、聴衆、Voice、言語
    ├── source/
    │   └── presentation.pptx    # 入力ファイル（コピー、変更されない）
    ├── slides/
    │   └── presentation.pdf     # PPTX→PDF変換（gemini-3-pro-preview入力用）
    ├── script/
    │   └── master_script.md     # マスター台本（会話内でも表示、エディタでも編集可能）
    ├── audio/
    │   ├── slide_001.wav        # 中間生成物（個別音声、3桁ゼロパディング）
    │   ├── slide_002.wav
    │   └── ...
    └── output/
        └── presentation_narrated.ppsx  # 最終成果物

---

## Config (config.yaml)

    language: ja              # ja / en
    duration_minutes: 10      # 発表時間（分）。0 = 指定なし
    voice: Aoede              # gemini-2.5-pro-preview-tts voice name（32種から選択）
    voice_style: ""           # 自然言語で声のスタイルを指示（省略可）
    context: |                # Claude Code が AskUserQuestion で収集したリッチなコンテキスト
      新規取引先のCTO向け製品提案。導入を決めてもらうことがゴール。
      フォーマルだが堅すぎず、技術的な信頼感を重視。
      聴衆はITに精通しているので専門用語はそのまま使ってよい。

対応言語: 日本語 (ja) と英語 (en) のみ。
Voice デフォルト: 日本語 → Aoede、英語 → Kore。config で変更可能。
発表時間: スライド枚数に応じて各スライドの台本量を自動配分。
context: Claude Code がスライド解析後に AskUserQuestion で収集する。
         tone/audience を個別フィールドにせず、自然言語でリッチに記述。

---

## Experience

### 基本フロー

    User: このPPTXにナレーションつけて [presentation.pptx]

    Claude Code: スライドを解析中... 10枚検出しました。
                 内容: SaaS製品の紹介スライドのようです。

                 いくつか質問させてください：
                 [AskUserQuestion]
                 1. このプレゼンの目的は？
                    a) 新規取引先への提案
                    b) 既存顧客へのアップセル
                    c) 社内向け製品説明
                 2. 聴衆に期待するアクションは？
                    a) 導入を決めてもらう
                    b) 次回ミーティングに繋げる
                    c) 理解してもらう
                 3. トーンは？
                    a) フォーマル（堅めの敬語）
                    b) ビジネスカジュアル（丁寧だが親しみやすい）
                    c) エネルギッシュ（営業ピッチ風）

    User: a, a, b

    Claude Code: コンテキストを設定しました:
                 「新規取引先への製品提案。導入を決めてもらうことがゴール。
                  ビジネスカジュアルなトーンで、丁寧だが親しみやすく。」

                 発表時間は何分ですか？（指定なしならお任せで生成します）

    User: 10分

    Claude Code: マスター台本を生成しました。

                 [---SLIDE_01---]
                 みなさん、こんにちは！本日は弊社の新しいサービスについて
                 ご紹介させていただきます。...

                 [---SLIDE_02---]
                 まず、現状の課題からお話しします。
                 こちらのグラフをご覧ください。...
                 ...

                 台本は script/master_script.md にも保存しています。
                 このままでよければ「OK」、修正があれば指示してください。
                 （じっくり編集したい場合は master_script.md を直接編集してください）

    User: Slide 5のコスト削減の部分をもう少し具体的に。

    Claude Code: 修正しました。（修正箇所を表示）
                 これで音声生成に進みますか？

    User: OK

    Claude Code: 音声生成中...
                 ✓ slide_01.wav (12.3s)
                 ✓ slide_02.wav (8.7s)
                 ...
                 ✓ slide_10.wav (15.1s)

                 PPSXを組み立て中...
                 ✓ 完了: output/presentation_narrated.ppsx
                 ダブルクリックでナレーション付きプレゼンが自動再生されます。

### 英語プレゼンの場合

    User: This PPTX needs narration in English. Formal tone, for investors.
          [pitch_deck.pptx]

    Claude Code: Analyzing slides... 15 slides detected.
                 Generating master script in English, formal tone...
                 (以降同じフローを英語で実行)

### 台本をエディタで編集する場合

    User: このPPTXにナレーションつけて。台本はファイルで確認したい。

    Claude Code: (台本生成後)
                 script/master_script.md に保存しました。
                 エディタで編集して、完了したら教えてください。

    User: (エディタで master_script.md を編集・保存)
          編集した。これで音声作って。

    Claude Code: 更新された master_script.md を読み込みました。
                 音声生成に進みます...

### 特定スライドの音声だけ再生成

    User: Slide 3と7の台本を修正した。この2枚だけ音声作り直して。

    Claude Code: slide_03, slide_07 の音声を再生成します。
                 ✓ slide_03.wav (9.2s) — 更新
                 ✓ slide_07.wav (11.5s) — 更新
                 PPSXを再構築中...
                 ✓ 完了: output/presentation_narrated.ppsx

### エラー時

    Claude Code: 音声生成中にエラーが発生しました（Slide 5/10）

                 ✓ 生成済み: slide_01.wav 〜 slide_04.wav（保持）
                 ✗ 失敗: slide_05 — API rate limit exceeded
                 - 未処理: slide_06 〜 slide_10

                 再開しますか？ slide_05 から続行します。

---

## Distribution

単一リポジトリで完結する配布モデル：

```bash
git clone https://github.com/YOURNAME/SpiritBox.git
cd SpiritBox
pip install -e .            # Python パッケージ
bash install.sh             # グローバル /spiritbox コマンド（任意）
```

### リポジトリ構成

    SpiritBox/
    ├── spiritbox/                    # Python パッケージ本体
    │   ├── __init__.py
    │   ├── config.py
    │   ├── extract.py
    │   ├── script.py
    │   ├── voice.py
    │   └── assemble.py
    ├── .claude/commands/spiritbox.md  # /spiritbox コマンド定義（プロジェクトローカル）
    ├── install.sh                    # グローバル /spiritbox コマンドのインストーラー
    ├── uninstall.sh                  # アンインストーラー
    ├── pyproject.toml
    ├── CLAUDE.md                     # Claude Code オーケストレーションガイド
    ├── design.md                     # 本ファイル
    └── .env                          # GOOGLE_API_KEY（.gitignore対象）

`install.sh` は `.claude/commands/spiritbox.md` を `~/.claude/commands/` にコピーし、
どのディレクトリからでも `/spiritbox` が使えるようにする。

---

## Implementation Order

1. `config` — プロジェクト初期化・設定管理
2. `extract` — PPTXからスライド画像・テキスト抽出
3. `script` — gemini-3-pro-previewによるマスター台本生成
4. `voice` — gemini-2.5-pro-preview-ttsによる音声生成
5. `assemble` — PPTX + 音声 → PPSX組み立て
