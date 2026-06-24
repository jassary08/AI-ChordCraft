"""Lyrics ASR parsing and full-song transcription workflow."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from src._runtime import (
    DEFAULT_OVERALL_AUDIO_WINDOW_SECONDS,
    ModelJSONParseError,
    format_timestamp_precise,
    generate_text,
    normalize_timestamp_precise,
    parse_analysis_json,
    parse_timestamp_float,
    probe_audio_duration,
    slice_audio,
    strip_thinking_text,
)


LYRIC_ASR_PROMPT = """
转录出这首歌的带时间戳的歌词
"""

LYRIC_ASR_WINDOW_SECONDS = DEFAULT_OVERALL_AUDIO_WINDOW_SECONDS


def coerce_lyrics_segments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    segments: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start_seconds = parse_timestamp_float(item.get("start"))
        end_seconds = parse_timestamp_float(item.get("end"))
        segments.append(
            {
                "start": format_timestamp_precise(start_seconds)
                if start_seconds is not None
                else normalize_timestamp_precise(item.get("start")),
                "end": format_timestamp_precise(end_seconds)
                if end_seconds is not None
                else normalize_timestamp_precise(item.get("end")),
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "text": text,
            }
        )
    segments.sort(
        key=lambda segment: (
            segment.get("start_seconds")
            if isinstance(segment.get("start_seconds"), (int, float))
            else parse_timestamp_float(segment.get("start")) or 0
        )
    )
    return segments


def parse_lyric_asr_text(text: str) -> list[dict[str, Any]]:
    cleaned = strip_thinking_text(text)
    fenced = re.search(r"```(?:text|lyrics?|lrc)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()

    try:
        parsed = parse_analysis_json(cleaned)
    except ModelJSONParseError:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("lyrics_segments"), list):
        return coerce_lyrics_segments(parsed.get("lyrics_segments"))

    segments: list[dict[str, Any]] = []
    line_pattern = re.compile(
        r"^\s*[\[\【]\s*"
        r"(?P<start>\d{1,3}:\d{2}(?:\.\d+)?|\d+(?:\.\d+)?s?)"
        r"\s*[-–—~到至]\s*"
        r"(?P<end>\d{1,3}:\d{2}(?:\.\d+)?|\d+(?:\.\d+)?s?)"
        r"\s*[\]\】]\s*"
        r"(?P<text>.+?)\s*$"
    )
    for line in cleaned.splitlines():
        match = line_pattern.match(line)
        if not match:
            continue
        lyric_text = match.group("text").strip()
        if not lyric_text:
            continue
        start_seconds = parse_timestamp_float(match.group("start"))
        end_seconds = parse_timestamp_float(match.group("end"))
        segments.append(
            {
                "start": format_timestamp_precise(start_seconds),
                "end": format_timestamp_precise(end_seconds),
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "text": lyric_text,
            }
        )

    segments = [
        segment
        for segment in segments
        if segment.get("start") is not None and segment.get("end") is not None
    ]
    segments.sort(key=lambda segment: float(segment.get("start_seconds") or 0.0))
    return segments


def dedupe_lyrics_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[int | None, int | None, str]] = set()
    for segment in sorted(segments, key=lambda item: float(item.get("start_seconds") or 0.0)):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = parse_timestamp_float(segment.get("start_seconds"))
        end = parse_timestamp_float(segment.get("end_seconds"))
        key = (
            int(round(start * 10)) if start is not None else None,
            int(round(end * 10)) if end is not None else None,
            re.sub(r"\s+", "", text),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(segment)
    return deduped


def _iter_lyric_windows(duration_seconds: int | float | None) -> list[tuple[float, float]]:
    if not isinstance(duration_seconds, (int, float)) or duration_seconds <= 0:
        return [(0.0, float(LYRIC_ASR_WINDOW_SECONDS))]

    windows: list[tuple[float, float]] = []
    start = 0.0
    duration = float(duration_seconds)
    while start < duration:
        end = min(duration, start + float(LYRIC_ASR_WINDOW_SECONDS))
        if end > start:
            windows.append((start, end))
        start = end
    return windows or [(0.0, min(duration, float(LYRIC_ASR_WINDOW_SECONDS)))]


def _offset_lyrics_to_song_timeline(
    segments: list[dict[str, Any]],
    window_start_seconds: float,
    window_end_seconds: float,
) -> list[dict[str, Any]]:
    adjusted: list[dict[str, Any]] = []
    tolerance = 1.0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue

        start_seconds = parse_timestamp_float(segment.get("start_seconds"))
        end_seconds = parse_timestamp_float(segment.get("end_seconds"))
        if start_seconds is None:
            start_seconds = parse_timestamp_float(segment.get("start"))
        if end_seconds is None:
            end_seconds = parse_timestamp_float(segment.get("end"))
        if start_seconds is None:
            continue

        # Most windowed ASR outputs timestamps relative to the sliced audio.
        # If the model already returns full-song timestamps inside this window,
        # keep them as-is instead of adding the offset twice.
        looks_absolute = (
            window_start_seconds > 0
            and start_seconds >= window_start_seconds - tolerance
            and start_seconds <= window_end_seconds + tolerance
        )
        absolute_start = start_seconds if looks_absolute else start_seconds + window_start_seconds
        if end_seconds is None:
            absolute_end = absolute_start + 1.0
        else:
            absolute_end = end_seconds if looks_absolute else end_seconds + window_start_seconds
        if absolute_end <= absolute_start:
            absolute_end = absolute_start + 1.0

        adjusted.append(
            {
                "start": format_timestamp_precise(absolute_start),
                "end": format_timestamp_precise(absolute_end),
                "start_seconds": absolute_start,
                "end_seconds": absolute_end,
                "text": text,
            }
        )
    return adjusted


def analyze_full_song_lyrics_with_llm(
    audio_path: str,
    raw_steps: dict[str, Any],
    task_base_urls: dict[str, str],
    backend: str,
    api_key: str | None,
    model_name_or_path: str | None,
    device: str | None,
    lyric_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    duration_seconds = probe_audio_duration(audio_path)
    windows = _iter_lyric_windows(duration_seconds)
    merged_segments: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="moss-lyrics-") as temp_dir:
        for index, (window_start, window_end) in enumerate(windows, start=1):
            step: dict[str, Any] = {
                "audio": "sliced_window" if len(windows) > 1 else "full_song",
                "window_index": index,
                "window_count": len(windows),
                "start_seconds": window_start,
                "end_seconds": window_end,
            }
            window_audio_path = audio_path
            if len(windows) > 1:
                window_audio_path = str(Path(temp_dir) / f"lyrics-window-{index:03d}.wav")
                try:
                    slice_audio(audio_path, window_audio_path, window_start, window_end)
                except RuntimeError as exc:
                    warning = (
                        f"歌词 ASR 第 {index}/{len(windows)} 个 90 秒窗口切分失败，已跳过：{exc}"
                    )
                    step["error"] = str(exc)
                    step["parse_error"] = warning
                    warnings.append(warning)
                    raw_steps.setdefault("lyric_asr", []).append(step)
                    continue

            lyric_raw = generate_text(
                audio_path=window_audio_path,
                prompt=LYRIC_ASR_PROMPT,
                backend=backend,
                base_url=task_base_urls["lyric_asr"],
                api_key=api_key,
                model_name_or_path=model_name_or_path,
                device=device,
                max_new_tokens=lyric_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
            )
            step["raw"] = lyric_raw
            raw_steps.setdefault("lyric_asr", []).append(step)

            window_segments = parse_lyric_asr_text(lyric_raw)
            if not window_segments:
                warning = (
                    f"歌词 ASR 第 {index}/{len(windows)} 个 90 秒窗口没有返回可解析的带时间戳歌词行。"
                )
                step["parse_error"] = warning
                warnings.append(warning)
                continue
            merged_segments.extend(
                _offset_lyrics_to_song_timeline(
                    window_segments,
                    window_start_seconds=window_start,
                    window_end_seconds=window_end,
                )
            )

    return dedupe_lyrics_segments(merged_segments), warnings
