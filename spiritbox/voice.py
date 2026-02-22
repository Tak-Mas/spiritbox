"""Scope: voice — Generate per-slide audio from master script via Gemini TTS."""

import os
import re
import wave
from pathlib import Path

from google import genai
from google.genai import types

from .config import SpiritBoxConfig
from .script import parse_master_script

# Gemini TTS text limit: ~5000 chars per request (safe margin).
_TTS_MAX_CHARS = 4000


def _create_client() -> genai.Client:
    """Create Gemini API client using API key."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY environment variable is required. "
            "Get one from Google Cloud Console → APIs & Services → Credentials."
        )
    return genai.Client(api_key=api_key)


def _save_pcm_as_wav(
    pcm_data: bytes, output_path: str, sample_rate: int = 24000
):
    """Save raw LINEAR16 PCM data as a WAV file."""
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)


def get_wav_duration_ms(wav_path: str) -> int:
    """Get WAV file duration in milliseconds."""
    with wave.open(wav_path, "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return int(frames / rate * 1000)


def _split_text_for_tts(text: str, max_chars: int = _TTS_MAX_CHARS) -> list[str]:
    """Split text into chunks that fit within the TTS API limit.

    Splits on sentence boundaries (。or .) to maintain natural speech flow.
    """
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r'(?<=[。．.!！?？\n])', text)

    chunks = []
    current_chunk = ""

    for sentence in sentences:
        if not sentence:
            continue
        test = current_chunk + sentence
        if len(test) > max_chars:
            if current_chunk:
                chunks.append(current_chunk)
            if len(sentence) > max_chars:
                # Force-split long sentence
                for i in range(0, len(sentence), max_chars):
                    chunks.append(sentence[i:i + max_chars])
                current_chunk = ""
            else:
                current_chunk = sentence
        else:
            current_chunk = test

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _synthesize_text(
    client: genai.Client,
    text: str,
    config: SpiritBoxConfig,
) -> bytes:
    """Synthesize a single text chunk to PCM audio bytes via Gemini TTS."""
    response = client.models.generate_content(
        model="gemini-2.5-pro-preview-tts",
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=config.effective_voice,
                    )
                )
            ),
        ),
    )

    return response.candidates[0].content.parts[0].inline_data.data


def generate_slide_audio(
    client: genai.Client,
    text: str,
    config: SpiritBoxConfig,
    output_path: str,
) -> int:
    """Generate audio for one slide's script. Handles text chunking.

    Returns: audio duration in milliseconds
    """
    chunks = _split_text_for_tts(text)

    pcm_parts = []
    for chunk in chunks:
        pcm_parts.append(_synthesize_text(client, chunk, config))

    # Concatenate all PCM chunks
    all_pcm = b"".join(pcm_parts)
    _save_pcm_as_wav(all_pcm, output_path)

    return get_wav_duration_ms(output_path)


def generate_all_audio(
    project_dir: str,
    config: SpiritBoxConfig,
    slides: list[int] | None = None,
) -> dict[int, int]:
    """Generate audio for all slides (or specified slides). (P6: Resumable)

    Args:
        project_dir: project directory
        config: SpiritBox config
        slides: slide numbers to (re)generate. None = all, skip existing.

    Returns: {slide_number: duration_ms}
    """
    scripts = parse_master_script(project_dir)

    if not scripts:
        raise RuntimeError(
            "No slide markers found in master_script.md. "
            "Expected format: [---SLIDE_01---] followed by script text. "
            "Regenerate the script or check the marker format."
        )

    audio_dir = Path(project_dir) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    target_slides = slides if slides else sorted(scripts.keys())
    durations = {}

    client = _create_client()

    for slide_num in target_slides:
        if slide_num not in scripts:
            continue

        wav_path = audio_dir / f"slide_{slide_num:03d}.wav"

        # P6: skip existing files when generating all (not explicit re-generation)
        if wav_path.exists() and slides is None:
            durations[slide_num] = get_wav_duration_ms(str(wav_path))
            continue

        duration = generate_slide_audio(
            client=client,
            text=scripts[slide_num],
            config=config,
            output_path=str(wav_path),
        )
        durations[slide_num] = duration

    return durations
