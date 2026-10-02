"""Scope: video — Render slides + narration into a YouTube-ready MP4.

Outputs (P8: Non-Destructive, next to the PPSX in output/):
    {stem}_narrated.mp4            1920x1080 H.264 + AAC
    {stem}_narrated.srt            captions (sentence timing estimated per slide)
    {stem}_narrated_chapters.txt   YouTube chapter list for the description
    {stem}_narrated_thumbnail.jpg  1280x720 thumbnail from the first slide
"""

import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from PIL import Image

from .config import SpiritBoxConfig, get_source_pptx
from .extract import extract_all
from .script import parse_master_script

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30
THUMBNAIL_SIZE = (1280, 720)

LEAD_IN_MS = 500  # silence before each slide's narration
TAIL_MS = 1000  # silence after each slide's narration
SILENT_SLIDE_MS = 3000  # display time of a slide without narration
CHAPTER_MIN_MS = 10_000  # YouTube ignores chapters shorter than 10 s

_DEFAULT_SAMPLE_RATE = 24000
_CAPTION_MAX_CHARS = {"ja": 40, "en": 80}


# ---------------------------------------------------------------------------
# Slide images
# ---------------------------------------------------------------------------

def render_slide_images(pdf_path: str, images_dir: str) -> list[Path]:
    """Render every PDF page to a PNG that fits in 1920x1080. (P6: Resumable)

    An image is re-rendered only when it is missing or older than the PDF.
    """
    import pypdfium2 as pdfium

    pdf_path = Path(pdf_path)
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    pdf_mtime = pdf_path.stat().st_mtime

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        paths = []
        for i in range(len(pdf)):
            out = images_dir / f"page_{i + 1:03d}.png"
            if not out.exists() or out.stat().st_mtime < pdf_mtime:
                page = pdf[i]
                width, height = page.get_size()
                scale = min(VIDEO_WIDTH / width, VIDEO_HEIGHT / height)
                page.render(scale=scale).to_pil().save(out)
            paths.append(out)
        return paths
    finally:
        pdf.close()


def _map_slides_to_pages(slides_data: list[dict], page_count: int) -> list[int]:
    """Slide numbers in PDF page order.

    LibreOffice does not export hidden slides, so the PDF may hold only the
    visible ones. Hidden slides are then left out of the video.
    """
    all_slides = [s["slide_number"] for s in slides_data]
    if page_count == len(all_slides):
        return all_slides
    visible = [s["slide_number"] for s in slides_data if not s.get("hidden")]
    if page_count == len(visible):
        return visible
    raise RuntimeError(
        f"The slide PDF has {page_count} pages, but the deck has "
        f"{len(all_slides)} slides ({len(visible)} visible). "
        "Delete slides/*.pdf and rerun extract."
    )


# ---------------------------------------------------------------------------
# Timeline and narration track
# ---------------------------------------------------------------------------

def _read_wav(path: Path) -> tuple[bytes, int]:
    """Return (16-bit mono PCM, sample rate) of a narration WAV."""
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise RuntimeError(f"{path.name}: expected 16-bit mono WAV")
        return wf.readframes(wf.getnframes()), wf.getframerate()


def build_timeline(slide_numbers: list[int], audio_ms: dict[int, int]) -> list[dict]:
    """Place each slide on the video timeline.

    A narrated slide lasts LEAD_IN_MS + narration + TAIL_MS;
    a slide without narration lasts SILENT_SLIDE_MS.
    """
    timeline = []
    t = 0
    for n in slide_numbers:
        narration = audio_ms.get(n, 0)
        if narration > 0:
            length = LEAD_IN_MS + narration + TAIL_MS
            audio_start = t + LEAD_IN_MS
        else:
            length = SILENT_SLIDE_MS
            audio_start = None
        timeline.append({
            "slide_number": n,
            "start_ms": t,
            "length_ms": length,
            "audio_start_ms": audio_start,
            "audio_ms": narration,
        })
        t += length
    return timeline


def _ms_to_samples(ms: int, rate: int) -> int:
    return round(ms * rate / 1000)


def write_narration_track(
    timeline: list[dict],
    pcm_by_slide: dict[int, bytes],
    sample_rate: int,
    output_path: str,
):
    """Write one continuous WAV whose segments line up with the timeline.

    Each segment is padded/trimmed to its exact sample count, so the audio
    never drifts from the slide changes.
    """
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for seg in timeline:
            segment_end = _ms_to_samples(seg["start_ms"] + seg["length_ms"], sample_rate)
            n_samples = segment_end - _ms_to_samples(seg["start_ms"], sample_rate)
            pcm = b""
            if seg["audio_start_ms"] is not None:
                lead = _ms_to_samples(seg["audio_start_ms"] - seg["start_ms"], sample_rate)
                pcm = b"\x00\x00" * lead + pcm_by_slide.get(seg["slide_number"], b"")
            pcm = pcm[: n_samples * 2]
            pcm += b"\x00\x00" * (n_samples - len(pcm) // 2)
            wf.writeframes(pcm)


# ---------------------------------------------------------------------------
# Captions and chapters
# ---------------------------------------------------------------------------

def _split_captions(text: str, max_chars: int, language: str) -> list[str]:
    """Split a slide's script into caption lines of at most max_chars."""
    joiner = "" if language == "ja" else " "
    text = joiner.join(line.strip() for line in text.splitlines() if line.strip())
    sentences = re.split(r"(?<=[。！？!?])\s*|(?<=\.)\s+", text)

    captions = []
    for sentence in (s.strip() for s in sentences):
        while len(sentence) > max_chars:
            cut = max(sentence.rfind(c, 0, max_chars) for c in "、，,")
            if cut <= 0:
                cut = sentence.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars - 1
            captions.append(sentence[: cut + 1].strip())
            sentence = sentence[cut + 1:].strip()
        if sentence:
            captions.append(sentence)
    return captions


def _srt_time(ms: int) -> str:
    hours, rest = divmod(int(ms), 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def build_srt(
    timeline: list[dict],
    scripts: dict[int, str],
    language: str = "ja",
) -> str:
    """Build SRT captions from the script.

    Slide boundaries are exact; within a slide, each caption gets a share of
    the narration time proportional to its length (an estimate).
    """
    max_chars = _CAPTION_MAX_CHARS.get(language, 80)
    entries = []
    for seg in timeline:
        text = scripts.get(seg["slide_number"], "")
        if seg["audio_start_ms"] is None or not text:
            continue
        captions = _split_captions(text, max_chars, language)
        total_chars = sum(len(c) for c in captions)
        t = float(seg["audio_start_ms"])
        for caption in captions:
            length = seg["audio_ms"] * len(caption) / total_chars
            entries.append((round(t), round(t + length), caption))
            t += length

    blocks = [
        f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{caption}\n"
        for i, (start, end, caption) in enumerate(entries, 1)
    ]
    return "\n".join(blocks)


def _chapter_time(ms: int) -> str:
    total_sec = int(ms // 1000)
    hours, rest = divmod(total_sec, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def build_chapters(timeline: list[dict], titles: dict[int, str]) -> list[tuple[int, str]]:
    """YouTube chapters: (start_ms, title), first at 0:00, each >= 10 s.

    A slide starts a new chapter only when the current chapter has lasted
    10 s and the title differs from it (continuation slides are merged).
    """
    if not timeline:
        return []
    total_ms = timeline[-1]["start_ms"] + timeline[-1]["length_ms"]

    chapters = []
    for seg in timeline:
        n = seg["slide_number"]
        title = (titles.get(n) or f"Slide {n}")[:80]
        if not chapters:
            chapters.append((0, title))
        elif (seg["start_ms"] - chapters[-1][0] >= CHAPTER_MIN_MS
              and title != chapters[-1][1]):
            chapters.append((seg["start_ms"], title))

    if len(chapters) > 1 and total_ms - chapters[-1][0] < CHAPTER_MIN_MS:
        chapters.pop()
    return chapters


def format_chapters(chapters: list[tuple[int, str]]) -> str:
    return "".join(f"{_chapter_time(ms)} {title}\n" for ms, title in chapters)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def _find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg is required for video output.\n"
            "Install: brew install ffmpeg"
        )
    return ffmpeg


def _write_concat_list(timeline, page_by_slide, images, path: Path):
    """ffconcat list: each slide image shown for its segment length."""

    def entry(image: Path) -> str:
        return "file '" + str(image.resolve()).replace("'", "'\\''") + "'\n"

    lines = ["ffconcat version 1.0\n"]
    for seg in timeline:
        lines.append(entry(images[page_by_slide[seg["slide_number"]]]))
        lines.append(f"duration {seg['length_ms'] / 1000:.3f}\n")
    # The concat demuxer ignores the last duration unless the file is repeated.
    lines.append(entry(images[page_by_slide[timeline[-1]["slide_number"]]]))
    path.write_text("".join(lines), encoding="utf-8")


def _encode_video(concat_path: Path, narration_path: Path, output_path: Path):
    w, h = VIDEO_WIDTH, VIDEO_HEIGHT
    video_filter = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={VIDEO_FPS},format=yuv420p"
    )
    cmd = [
        _find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
        "-i", str(narration_path),
        "-vf", video_filter,
        "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr.strip()}")


def _write_thumbnail(image_path: Path, output_path: Path):
    """1280x720 JPEG of the first slide (letterboxed if not 16:9)."""
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        img.thumbnail(THUMBNAIL_SIZE, Image.LANCZOS)
        canvas = Image.new("RGB", THUMBNAIL_SIZE, (0, 0, 0))
        canvas.paste(img, ((THUMBNAIL_SIZE[0] - img.width) // 2,
                           (THUMBNAIL_SIZE[1] - img.height) // 2))
        canvas.save(output_path, "JPEG", quality=90)


def render_video(project_dir: str, config: SpiritBoxConfig = None) -> dict:
    """Render the narrated slide video and its YouTube sidecar files.

    Reads slides/*.pdf (extract) and audio/slide_XXX.wav (voice).

    Returns:
        {"video": str, "subtitles": str | None, "chapters": str,
         "chapter_count": int, "thumbnail": str, "duration_sec": float}
    """
    config = config or SpiritBoxConfig()
    project = Path(project_dir)
    audio_dir = project / "audio"
    output_dir = project / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    extraction = extract_all(project_dir, config)
    slides_data = extraction["slides_data"]
    images = render_slide_images(extraction["pdf_path"], str(project / "slides" / "images"))
    slide_numbers = _map_slides_to_pages(slides_data, len(images))
    page_by_slide = {n: i for i, n in enumerate(slide_numbers)}

    # Narration per slide (slides without a WAV are shown silently)
    pcm_by_slide, audio_ms, sample_rate = {}, {}, None
    for n in slide_numbers:
        wav_path = audio_dir / f"slide_{n:03d}.wav"
        if not wav_path.exists():
            continue
        pcm, rate = _read_wav(wav_path)
        if sample_rate is not None and rate != sample_rate:
            raise RuntimeError(
                f"{wav_path.name} is {rate} Hz but other slides are {sample_rate} Hz. "
                "Regenerate the audio with one TTS model."
            )
        sample_rate = rate
        pcm_by_slide[n] = pcm
        audio_ms[n] = len(pcm) // 2 * 1000 // rate
    sample_rate = sample_rate or _DEFAULT_SAMPLE_RATE

    timeline = build_timeline(slide_numbers, audio_ms)
    total_ms = timeline[-1]["start_ms"] + timeline[-1]["length_ms"]

    # Output names (avoid overwriting earlier renders)
    stem = get_source_pptx(project_dir, config).stem
    base = f"{stem}_narrated"
    video_path = output_dir / f"{base}.mp4"
    n = 2
    while video_path.exists():
        base = f"{stem}_narrated_{n}"
        video_path = output_dir / f"{base}.mp4"
        n += 1

    with tempfile.TemporaryDirectory(dir=str(project)) as tmp:
        tmp_dir = Path(tmp)
        narration_path = tmp_dir / "narration.wav"
        concat_path = tmp_dir / "slides.ffconcat"
        write_narration_track(timeline, pcm_by_slide, sample_rate, str(narration_path))
        _write_concat_list(timeline, page_by_slide, images, concat_path)
        _encode_video(concat_path, narration_path, video_path)

    # Captions from the approved script
    subtitles_path = None
    script_path = project / "script" / "master_script.md"
    if script_path.exists():
        srt = build_srt(timeline, parse_master_script(project_dir), config.language)
        if srt:
            subtitles_path = output_dir / f"{base}.srt"
            subtitles_path.write_text(srt, encoding="utf-8")

    titles = {s["slide_number"]: s.get("title", "") for s in slides_data}
    chapters = build_chapters(timeline, titles)
    chapters_path = output_dir / f"{base}_chapters.txt"
    chapters_path.write_text(format_chapters(chapters), encoding="utf-8")

    thumbnail_path = output_dir / f"{base}_thumbnail.jpg"
    _write_thumbnail(images[0], thumbnail_path)

    return {
        "video": str(video_path),
        "subtitles": str(subtitles_path) if subtitles_path else None,
        "chapters": str(chapters_path),
        "chapter_count": len(chapters),
        "thumbnail": str(thumbnail_path),
        "duration_sec": total_ms / 1000,
    }
