"""Scope: script — Generate master script from all slides via Gemini 3 Pro."""

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from .config import SpiritBoxConfig

load_dotenv()


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


def _build_prompt(slide_count: int, config: SpiritBoxConfig) -> str:
    """Build the script generation prompt. (P1, P3, P4, P5)

    All prompts are in English for consistent model performance.
    Output language is controlled by the language field.
    """
    duration = _build_duration_instruction(slide_count, config)
    output_lang = "Japanese" if config.language == "ja" else "English"

    return f"""You are a professional presentation speaker.
You will be shown images and extracted text from all {slide_count} slides of a presentation.
Create a complete presentation script with coherent storytelling from start to finish.

## Presentation Context
{config.context}
{duration}
## Rules

1. **Use spoken language**: Write as natural speech that a presenter would actually say out loud, not bullet points or written prose. Include natural filler words and conversational phrases appropriate for a live presentation. Match the tone and style described in the context above.

2. **Reference visual elements**: When slides contain graphs, charts, diagrams, or images, reference them directly (e.g., "As you can see in this graph...", "Looking at this diagram..."). Describe what the visual shows in words.

3. **Smooth transitions**: Include natural transitions between slides. Don't jump abruptly to new topics. Connect each slide's content to what came before.

4. **No spoilers**: Don't discuss content from upcoming slides. Stay within each slide's scope. Build the narrative progressively.

5. **Slide markers**: Begin each slide's script with a `[---SLIDE_XX---]` marker (XX = 01, 02, ...).

## Output Language
Write the entire script in **{output_lang}**.

## Output Format

[---SLIDE_01---]
(Slide 1 script in {output_lang})

[---SLIDE_02---]
(Slide 2 script in {output_lang})

..."""


def _build_contents(
    pdf_path: str,
    slides_data: list[dict],
    config: SpiritBoxConfig,
) -> list:
    """Build contents for Gemini API request. (P1: all slides at once)

    Sends the full PDF for visual understanding, plus extracted text/notes
    for each slide as supplementary context.
    """
    contents = []

    # Send entire PDF for visual understanding (P4: Visual Context Grounding)
    with open(pdf_path, "rb") as f:
        contents.append(
            types.Part.from_bytes(data=f.read(), mime_type="application/pdf")
        )

    # Add extracted text and speaker notes per slide
    for slide_info in slides_data:
        i = slide_info["slide_number"]
        label = f"--- Slide {i} ---"
        text_info = slide_info.get("text", "")
        notes_info = slide_info.get("notes", "")
        slide_context = f"{label}\nText: {text_info}"
        if notes_info:
            slide_context += f"\nSpeaker Notes: {notes_info}"
        contents.append(slide_context)

    contents.append(_build_prompt(len(slides_data), config))
    return contents


def generate_master_script(
    project_dir: str,
    config: SpiritBoxConfig,
    slides_data: list[dict],
    pdf_path: str,
) -> str:
    """Send all slides to Gemini 3 Pro and generate master script.

    P1: Master Script First — all slides in a single request.
    Uses PDF for visual understanding + extracted text/notes as context.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY environment variable is required. "
            "Get one from Google Cloud Console → APIs & Services → Credentials."
        )

    # P6: Resumable — skip if master script already exists
    script_path = Path(project_dir) / "script" / "master_script.md"
    if script_path.exists() and script_path.stat().st_size > 0:
        return script_path.read_text(encoding="utf-8")

    client = genai.Client(api_key=api_key)

    contents = _build_contents(pdf_path, slides_data, config)

    response = client.models.generate_content(
        model="gemini-3-pro-preview",
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=65535,
        ),
    )

    try:
        script_text = response.text
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

    marker_pattern = r"\[---SLIDE_(\d+)---\]"
    parts = re.split(marker_pattern, text)

    scripts = {}
    for i in range(1, len(parts), 2):
        slide_num = int(parts[i])
        script_text = parts[i + 1].strip()
        if script_text:
            scripts[slide_num] = script_text

    return scripts
