"""Scope: script — Build the master script (Claude / Gemini, or the speaker notes)."""

import base64
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from .config import SpiritBoxConfig

load_dotenv()

_MARKER_PATTERN = r"\[---SLIDE_(\d+)---\]"

# Claude API: 32 MB per request; base64 inflates the PDF by 4/3.
_CLAUDE_MAX_PDF_BYTES = 22 * 1024 * 1024
_CLAUDE_MAX_TOKENS = 64000
# Models that take adaptive thinking + effort + server-side refusal fallbacks.
_CLAUDE_CURRENT_MODELS = (
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-fable-5-1",
    "claude-sonnet-5-5",
)
_CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _build_duration_instruction(slide_count: int, config: SpiritBoxConfig) -> str:
    """Build time constraint instruction."""
    if config.duration_minutes <= 0 or slide_count <= 0:
        return ""

    avg_sec = config.duration_minutes * 60 // slide_count

    if config.language == "ja":
        speaking_rate = "Japanese speaking rate is approximately 350-400 characters per minute."
    else:
        speaking_rate = "English speaking rate is approximately 150-160 words per minute."

    return f"""
## Time Constraint
- Target total duration: **{config.duration_minutes} minutes**
- Total slides: {slide_count}
- Average per slide: ~{avg_sec} seconds
- Title/divider slides should be shorter (15-30s); allocate more time to content-heavy slides.
- {speaking_rate} Adjust each slide's script length to match the target duration.
"""


def _build_notes_instruction(config: SpiritBoxConfig) -> str:
    """Explain how the speaker notes must be used. (P9: Explicit Script Source)

    Notes are often private memos, so they are background only unless the
    user explicitly chose a notes-based script source.
    """
    if config.script_source == "notes_refine":
        return """
## Speaker Notes = Draft Narration
The speaker notes of each slide contain the author's draft narration.
Rewrite each draft into polished spoken narration for that slide.
- Keep the author's content, claims, numbers, and order. Do not add facts that appear neither on the slide nor in the draft.
- You may fix wording, add smooth transitions, and drop obvious memos (to-dos, reminders, references) that are not meant to be read aloud.
- If a slide has no draft, write its narration from the slide content.
"""
    return """
## Speaker Notes
The speaker notes are the author's private notes. Use them only as background information.
They may contain internal memos (to-dos, reminders, references) that must not be read aloud.
"""


def _build_prompt(slide_count: int, config: SpiritBoxConfig) -> str:
    """Build the script generation prompt. (P1, P3, P4, P5)

    All prompts are in English for consistent model performance.
    Output language is controlled by the language field.
    """
    duration = _build_duration_instruction(slide_count, config)
    notes = _build_notes_instruction(config)
    output_lang = "Japanese" if config.language == "ja" else "English"

    if config.is_video:
        role = "You are a professional narrator of presentation videos (e.g. YouTube)."
        speech_rule = (
            "Write as natural speech addressed to viewers who watch a recorded "
            "video, not bullet points or written prose. Do not use filler words "
            "(e.g. \"um\", \"えー\"), and do not refer to live-event elements such "
            "as a Q&A session or the audience in the room. Match the tone and "
            "style described in the context above."
        )
    else:
        role = "You are a professional presentation speaker."
        speech_rule = (
            "Write as natural speech that a presenter would actually say out "
            "loud, not bullet points or written prose. Include natural filler "
            "words and conversational phrases appropriate for a live "
            "presentation. Match the tone and style described in the context above."
        )

    return f"""{role}
You will be shown images and extracted text from all {slide_count} slides of a presentation.
Create a complete presentation script with coherent storytelling from start to finish.

## Presentation Context
{config.context}
{duration}{notes}
## Rules

1. **Use spoken language**: {speech_rule}

2. **Reference visual elements**: When slides contain graphs, charts, diagrams, or images, reference them directly (e.g., "As you can see in this graph...", "Looking at this diagram..."). Describe what the visual shows in words.

3. **Smooth transitions**: Include natural transitions between slides. Don't jump abruptly to new topics. Connect each slide's content to what came before.

4. **No spoilers**: Don't discuss content from upcoming slides. Stay within each slide's scope. Build the narrative progressively.

5. **Slide markers**: Begin each slide's script with a `[---SLIDE_XX---]` marker (XX = 01, 02, ...).

6. **Plain text for speech**: A text-to-speech engine reads the script verbatim. Do not use Markdown (headings, bold, bullet lists), emojis, or bracketed stage directions.

## Output Language
Write the entire script in **{output_lang}**.

## Output Format
Output only the script, starting with [---SLIDE_01---] and covering every slide up to [---SLIDE_{slide_count:02d}---].

[---SLIDE_01---]
(Slide 1 script in {output_lang})

[---SLIDE_02---]
(Slide 2 script in {output_lang})

..."""


def _format_slide_text(slide_info: dict) -> str:
    """Extracted text and speaker notes of one slide, as model context."""
    label = f"--- Slide {slide_info['slide_number']} ---"
    if slide_info.get("hidden"):
        # LibreOffice leaves hidden slides out of the PDF, so page numbers shift.
        label = f"--- Slide {slide_info['slide_number']} (hidden; not in the PDF) ---"
    slide_context = f"{label}\nText: {slide_info.get('text', '')}"
    notes_info = slide_info.get("notes", "").strip()
    if notes_info:
        slide_context += f"\nSpeaker Notes: {notes_info}"
    return slide_context


def _build_contents(
    pdf_path: str,
    slides_data: list[dict],
    config: SpiritBoxConfig,
) -> list:
    """Build contents for Gemini API request. (P1: all slides at once)

    Sends the full PDF for visual understanding, plus extracted text/notes
    for each slide as supplementary context.
    """
    from google.genai import types

    contents = []

    # Send entire PDF for visual understanding (P4: Visual Context Grounding)
    with open(pdf_path, "rb") as f:
        contents.append(
            types.Part.from_bytes(data=f.read(), mime_type="application/pdf")
        )

    # Add extracted text and speaker notes per slide
    for slide_info in slides_data:
        contents.append(_format_slide_text(slide_info))

    contents.append(_build_prompt(len(slides_data), config))
    return contents


def _generate_with_gemini(
    config: SpiritBoxConfig,
    slides_data: list[dict],
    pdf_path: str,
) -> str:
    """Generate the master script with a Gemini model (legacy backend)."""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY environment variable is required. "
            "Get one from Google Cloud Console → APIs & Services → Credentials."
        )

    client = genai.Client(api_key=api_key)

    contents = _build_contents(pdf_path, slides_data, config)

    response = client.models.generate_content(
        model=config.script_model,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=65535,
        ),
    )

    try:
        return response.text
    except ValueError:
        # Safety filter or no candidates
        block_reason = ""
        if response.candidates:
            block_reason = str(response.candidates[0].finish_reason)
        raise RuntimeError(
            f"Gemini API returned no content. "
            f"The request may have been blocked by safety filters. "
            f"Reason: {block_reason or 'unknown'}"
        )


def _claude_model_options(model: str) -> dict:
    """Request options that only the current Claude models accept."""
    if model not in _CLAUDE_CURRENT_MODELS:
        return {}
    return {
        # Thinking is adaptive by default; effort controls its depth.
        "output_config": {"effort": "high"},
        # On a safety decline, re-run on Anthropic's recommended fallback model.
        "betas": [_CLAUDE_FALLBACK_BETA],
        "fallbacks": "default",
    }


def _generate_with_claude(
    config: SpiritBoxConfig,
    slides_data: list[dict],
    pdf_path: str,
) -> str:
    """Generate the master script with Claude. (P1, P4)

    The PDF goes in as a document block (Claude reads both the text and the
    rendered page images), followed by the extracted text/notes and the prompt.
    """
    import anthropic

    pdf_bytes = Path(pdf_path).read_bytes()
    if len(pdf_bytes) > _CLAUDE_MAX_PDF_BYTES:
        raise RuntimeError(
            f"Slide PDF is {len(pdf_bytes) / 1024 / 1024:.1f} MB; the Claude API "
            f"accepts up to ~{_CLAUDE_MAX_PDF_BYTES // 1024 // 1024} MB. "
            "Compress the images in the PPTX and rerun extract."
        )

    slide_texts = "\n\n".join(_format_slide_text(s) for s in slides_data)
    content = [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
            },
            "title": "Slide deck",
        },
        {"type": "text", "text": slide_texts},
        {"type": "text", "text": _build_prompt(len(slides_data), config)},
    ]

    # Credentials: ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile.
    client = anthropic.Anthropic()
    try:
        # Streaming: long outputs would otherwise hit HTTP timeouts.
        with client.beta.messages.stream(
            model=config.script_model,
            max_tokens=_CLAUDE_MAX_TOKENS,
            messages=[{"role": "user", "content": content}],
            **_claude_model_options(config.script_model),
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise RuntimeError(
            "Claude API authentication failed. Set ANTHROPIC_API_KEY "
            "(environment variable or .env file)."
        ) from e

    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise RuntimeError(
            f"Claude declined to write the script (category: {category or 'unknown'}). "
            "Adjust the context or slides and retry."
        )
    if message.stop_reason == "max_tokens":
        raise RuntimeError(
            "The script was cut off at the output token limit. "
            "Shorten duration_minutes or split the deck."
        )

    text = "".join(b.text for b in message.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("Claude API returned no script text.")
    return text


def _strip_preamble(script_text: str) -> str:
    """Drop any text the model wrote before the first slide marker."""
    match = re.search(_MARKER_PATTERN, script_text)
    if match is None:
        return script_text
    return script_text[match.start():]


def build_script_from_notes(slides_data: list[dict]) -> str:
    """Use the speaker notes verbatim as the master script. (P9)

    A slide without notes gets an empty section, so it has no narration
    (the PPSX/video shows it silently for a few seconds).
    """
    sections = []
    for slide_info in slides_data:
        marker = f"[---SLIDE_{slide_info['slide_number']:02d}---]"
        notes = slide_info.get("notes", "").strip()
        sections.append(f"{marker}\n{notes}\n" if notes else f"{marker}\n")
    return "\n".join(sections)


def slides_without_notes(slides_data: list[dict]) -> list[int]:
    """Slide numbers whose speaker notes are empty."""
    return [
        s["slide_number"] for s in slides_data if not s.get("notes", "").strip()
    ]


def generate_master_script(
    project_dir: str,
    config: SpiritBoxConfig,
    slides_data: list[dict],
    pdf_path: str,
) -> str:
    """Build the master script and save it as master_script.md.

    P1: Master Script First — all slides in a single request.
    P9: config.script_source decides whether the script is generated,
    taken verbatim from the speaker notes, or polished from the notes.
    The script model is chosen by config.script_model ("claude-*" or "gemini-*").
    """
    # P6: Resumable — skip if master script already exists
    script_path = Path(project_dir) / "script" / "master_script.md"
    if script_path.exists() and script_path.stat().st_size > 0:
        return script_path.read_text(encoding="utf-8")

    config.validate()

    if config.script_source == "notes":
        script_text = build_script_from_notes(slides_data)
    elif config.script_model.startswith("claude-"):
        script_text = _generate_with_claude(config, slides_data, pdf_path)
    elif config.script_model.startswith("gemini-"):
        script_text = _generate_with_gemini(config, slides_data, pdf_path)
    else:
        raise ValueError(
            f"Unsupported script_model: '{config.script_model}'. "
            "Use a 'claude-*' or 'gemini-*' model ID."
        )

    script_text = _strip_preamble(script_text)
    save_master_script(script_text, project_dir)
    return script_text


def save_master_script(script_text: str, project_dir: str) -> str:
    """Save master script as a Markdown file. (P2, P6)"""
    script_path = Path(project_dir) / "script" / "master_script.md"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script_text, encoding="utf-8")
    return str(script_path)


def parse_master_script(project_dir: str) -> dict[int, str]:
    """Split master script by slide markers. (P5: Slide Marker Protocol)

    Returns: {slide_number: script_text}
    """
    script_path = Path(project_dir) / "script" / "master_script.md"
    text = script_path.read_text(encoding="utf-8")

    parts = re.split(_MARKER_PATTERN, text)

    scripts = {}
    for i in range(1, len(parts), 2):
        slide_num = int(parts[i])
        script_text = parts[i + 1].strip()
        if script_text:
            scripts[slide_num] = script_text

    return scripts
