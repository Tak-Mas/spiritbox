"""Tests for the video scope (timeline, narration track, captions, chapters, MP4)."""

import json
import shutil
import subprocess
import wave

import pytest
from PIL import Image

from spiritbox.config import SpiritBoxConfig
from spiritbox.video import (
    CHAPTER_MIN_MS,
    LEAD_IN_MS,
    SILENT_SLIDE_MS,
    TAIL_MS,
    _map_slides_to_pages,
    _split_captions,
    build_chapters,
    build_srt,
    build_timeline,
    format_chapters,
    render_video,
    write_narration_track,
)
from spiritbox.voice import _save_pcm_as_wav
from tests.test_spiritbox import _make_pptx


class TestTimeline:

    def test_narrated_and_silent_slides(self):
        timeline = build_timeline([1, 2, 3], {1: 4000, 3: 2000})
        assert [s["start_ms"] for s in timeline] == [
            0,
            LEAD_IN_MS + 4000 + TAIL_MS,
            LEAD_IN_MS + 4000 + TAIL_MS + SILENT_SLIDE_MS,
        ]
        assert timeline[0]["audio_start_ms"] == LEAD_IN_MS
        assert timeline[1]["audio_start_ms"] is None
        assert timeline[1]["length_ms"] == SILENT_SLIDE_MS
        assert timeline[2]["length_ms"] == LEAD_IN_MS + 2000 + TAIL_MS

    def test_map_all_pages(self):
        slides = [{"slide_number": 1}, {"slide_number": 2, "hidden": True}]
        assert _map_slides_to_pages(slides, 2) == [1, 2]

    def test_map_skips_hidden_when_pdf_has_visible_only(self):
        slides = [{"slide_number": 1}, {"slide_number": 2, "hidden": True},
                  {"slide_number": 3}]
        assert _map_slides_to_pages(slides, 2) == [1, 3]

    def test_map_mismatch_raises(self):
        with pytest.raises(RuntimeError, match="pages"):
            _map_slides_to_pages([{"slide_number": 1}], 3)


class TestNarrationTrack:

    def test_track_matches_timeline_exactly(self, tmp_path):
        rate = 24000
        pcm = b"\x10\x00" * 2400  # 100 ms of non-silence
        timeline = build_timeline([1, 2], {1: 100})
        out = tmp_path / "n.wav"
        write_narration_track(timeline, {1: pcm}, rate, str(out))

        with wave.open(str(out), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        total_ms = timeline[-1]["start_ms"] + timeline[-1]["length_ms"]
        assert len(frames) // 2 == total_ms * rate // 1000

        lead = LEAD_IN_MS * rate // 1000
        assert frames[: lead * 2] == b"\x00\x00" * lead
        assert frames[lead * 2: lead * 2 + len(pcm)] == pcm
        assert set(frames[lead * 2 + len(pcm):]) == {0}


class TestCaptions:

    def test_split_japanese_sentences(self):
        captions = _split_captions("これは一文目です。\nこれは二文目です！", 40, "ja")
        assert captions == ["これは一文目です。", "これは二文目です！"]

    def test_long_sentence_split_at_comma(self):
        text = "あ" * 30 + "、" + "い" * 30 + "。"
        captions = _split_captions(text, 40, "ja")
        assert captions == ["あ" * 30 + "、", "い" * 30 + "。"]
        assert all(len(c) <= 40 for c in captions)

    def test_english_split_at_space(self):
        text = "word " * 30
        captions = _split_captions(text, 40, "en")
        assert all(len(c) <= 40 for c in captions)
        assert " ".join(captions).split() == text.split()

    def test_srt_timing_stays_inside_narration(self):
        timeline = build_timeline([1, 2, 3], {1: 3000, 3: 6000})
        srt = build_srt(timeline, {1: "短い文。長めの文章です。", 3: "三枚目。"}, "ja")
        blocks = [b for b in srt.strip().split("\n\n")]
        assert len(blocks) == 3
        assert blocks[0].startswith("1\n00:00:00,500 --> ")
        # Slide 3 narration starts after slide 1 (4.5 s) + silent slide 2 (3 s) + lead-in
        start3 = 4500 + SILENT_SLIDE_MS + LEAD_IN_MS
        assert f"00:00:0{start3 // 1000},{start3 % 1000:03d} --> 00:00:14,000" in blocks[2]

    def test_srt_empty_without_script(self):
        assert build_srt(build_timeline([1], {1: 1000}), {}, "ja") == ""


class TestChapters:

    def test_short_slides_are_merged(self):
        timeline = build_timeline([1, 2, 3, 4], {1: 3000, 2: 20000, 3: 20000, 4: 20000})
        titles = {1: "Title", 2: "Intro", 3: "Results", 4: "Summary"}
        chapters = build_chapters(timeline, titles)
        # Slide 1 lasts 4.5 s, so slide 2 does not start a new chapter
        assert [t for _, t in chapters] == ["Title", "Results", "Summary"]
        assert chapters[0][0] == 0
        starts = [ms for ms, _ in chapters]
        assert all(b - a >= CHAPTER_MIN_MS for a, b in zip(starts, starts[1:]))

    def test_same_title_is_merged_and_short_last_dropped(self):
        timeline = build_timeline([1, 2, 3], {1: 20000, 2: 20000, 3: 3000})
        chapters = build_chapters(timeline, {1: "A", 2: "A", 3: "B"})
        assert [t for _, t in chapters] == ["A"]

    def test_missing_title_and_format(self):
        timeline = build_timeline([1, 2], {1: 65000, 2: 20000})
        text = format_chapters(build_chapters(timeline, {}))
        assert text == "0:00 Slide 1\n1:06 Slide 2\n"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestRenderVideo:

    def test_renders_mp4_and_sidecars(self, tmp_path):
        project = tmp_path / "deck_spiritbox"
        for subdir in ["source", "slides", "script", "audio", "output"]:
            (project / subdir).mkdir(parents=True)
        _make_pptx(project / "source" / "deck.pptx",
                   [{"text": "Opening"}, {"text": "Middle"}, {"text": "Closing"}])

        # A 3-page 16:9 PDF stands in for the LibreOffice export
        pages = [Image.new("RGB", (960, 540), c) for c in ("red", "green", "blue")]
        pages[0].save(project / "slides" / "deck.pdf", save_all=True,
                      append_images=pages[1:], resolution=72)

        (project / "script" / "master_script.md").write_text(
            "[---SLIDE_01---]\n一枚目です。\n[---SLIDE_03---]\n三枚目です。\n",
            encoding="utf-8",
        )
        _save_pcm_as_wav(b"\x00\x01" * 24000, str(project / "audio" / "slide_001.wav"))
        _save_pcm_as_wav(b"\x00\x01" * 12000, str(project / "audio" / "slide_003.wav"))

        result = render_video(str(project), SpiritBoxConfig(source_filename="deck.pptx"))

        expected_sec = (LEAD_IN_MS * 2 + 1000 + 500 + TAIL_MS * 2 + SILENT_SLIDE_MS) / 1000
        assert result["duration_sec"] == expected_sec
        assert result["video"].endswith("deck_narrated.mp4")

        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height:format=duration",
             "-of", "json", result["video"]],
            capture_output=True, text=True, check=True,
        )
        info = json.loads(probe.stdout)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
        assert (video["codec_name"], video["width"], video["height"]) == ("h264", 1920, 1080)
        assert audio["codec_name"] == "aac"
        assert abs(float(info["format"]["duration"]) - expected_sec) < 0.1

        srt = open(result["subtitles"], encoding="utf-8").read()
        assert "一枚目です。" in srt and "三枚目です。" in srt
        assert open(result["chapters"], encoding="utf-8").read().startswith("0:00 Opening")
        with Image.open(result["thumbnail"]) as thumb:
            assert thumb.size == (1280, 720)

        # A second render does not overwrite the first
        second = render_video(str(project), SpiritBoxConfig(source_filename="deck.pptx"))
        assert second["video"].endswith("deck_narrated_2.mp4")
