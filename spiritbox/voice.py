"""Scope: voice — Generate per-slide audio from master script via Gemini TTS."""

import io
import os
import re
import time
import wave
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .config import SpiritBoxConfig
from .script import parse_master_script

load_dotenv()

# Characters per TTS request. gemini-3.8-flash-tts returns at most 16,384
# audio tokens (~8 min) per request; 1,500 Japanese characters is ~4 min.
_TTS_MAX_CHARS = 1500
_TTS_DEFAULT_SAMPLE_RATE = 24000
_TTS_MAX_ATTEMPTS = 3


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


def _is_legacy_tts_model(model: str) -> bool:
    """Gemini 2.x TTS models take prebuilt_voice_config and return raw PCM."""
    return model.startswith("gemini-2")


def _build_tts_request(text: str, config: SpiritBoxConfig) -> tuple:
    """Build (contents, generation config) for one TTS request."""
    if _is_legacy_tts_model(config.tts_model):
        voice_config = types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name=config.effective_voice,
            )
        )
        contents = text
    else:
        # Gemini 3.x TTS: voice by name, style as per-part speech metadata.
        voice_config = types.VoiceConfig(voice=config.effective_voice)
        speech_metadata = (
            types.SpeechMetadata(style=config.voice_style)
            if config.voice_style else None
        )
        contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=text, speech_metadata=speech_metadata)],
            )
        ]

    generation_config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=voice_config),
    )
    return contents, generation_config


def _decode_audio(data: bytes, mime_type: str | None) -> tuple[bytes, int]:
    """Return (16-bit mono PCM, sample rate) from a TTS audio part.

    Gemini 3.x TTS returns a complete WAV file; Gemini 2.x returns raw
    LINEAR16 PCM with the rate in the MIME type (audio/L16;codec=pcm;rate=24000).
    """
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data), "rb") as wf:
            if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
                raise RuntimeError(
                    "Unexpected TTS audio format: "
                    f"{wf.getnchannels()} channel(s), {wf.getsampwidth() * 8}-bit"
                )
            return wf.readframes(wf.getnframes()), wf.getframerate()

    rate = _TTS_DEFAULT_SAMPLE_RATE
    match = re.search(r"rate=(\d+)", mime_type or "")
    if match:
        rate = int(match.group(1))
    return data, rate


def _extract_audio_part(response):
    """Return the inline audio blob of a TTS response, or None."""
    for candidate in response.candidates or []:
        content = candidate.content
        for part in (content.parts or []) if content else []:
            if part.inline_data is not None and part.inline_data.data:
                return part.inline_data
    return None


def _synthesize_text(
    client: genai.Client,
    text: str,
    config: SpiritBoxConfig,
) -> tuple[bytes, int]:
    """Synthesize one text chunk via Gemini TTS.

    Returns: (16-bit mono PCM bytes, sample rate)
    Retries rate limits, server errors, and empty audio responses.
    """
    contents, generation_config = _build_tts_request(text, config)

    for attempt in range(1, _TTS_MAX_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=config.tts_model,
                contents=contents,
                config=generation_config,
            )
        except genai_errors.APIError as e:
            retryable = e.code == 429 or e.code >= 500
            if not retryable or attempt == _TTS_MAX_ATTEMPTS:
                raise
        else:
            audio = _extract_audio_part(response)
            if audio is not None:
                return _decode_audio(audio.data, audio.mime_type)
            if attempt == _TTS_MAX_ATTEMPTS:
                reason = ""
                if response.candidates:
                    reason = str(response.candidates[0].finish_reason)
                raise RuntimeError(
                    f"TTS returned no audio (reason: {reason or 'unknown'})."
                )
        time.sleep(2 ** attempt)


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
    sample_rate = None
    for chunk in chunks:
        pcm, rate = _synthesize_text(client, chunk, config)
        if sample_rate is not None and rate != sample_rate:
            raise RuntimeError(
                f"TTS sample rate changed between chunks ({sample_rate} → {rate} Hz)"
            )
        sample_rate = rate
        pcm_parts.append(pcm)

    # Concatenate all PCM chunks
    all_pcm = b"".join(pcm_parts)
    _save_pcm_as_wav(all_pcm, output_path, sample_rate or _TTS_DEFAULT_SAMPLE_RATE)

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
