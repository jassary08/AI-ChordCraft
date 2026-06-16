"""Lyrics ASR parsing and full-song transcription workflow."""

from __future__ import annotations

import re
from typing import Any

from src._runtime import (
    ModelJSONParseError,
    format_timestamp_precise,
    generate_text,
    normalize_timestamp_precise,
    parse_analysis_json,
    parse_timestamp_float,
    strip_thinking_text,
)


LYRIC_ASR_PROMPT = """
转录出这首歌的带时间戳的歌词
"""


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
    step: dict[str, Any] = {"audio": "full_song"}
    lyric_raw = generate_text(
        audio_path=audio_path,
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

    lyrics_segments = parse_lyric_asr_text(lyric_raw)
    if not lyrics_segments:
        warning = "整首歌词 ASR 没有返回可解析的带时间戳歌词行。"
        step["parse_error"] = warning
        warnings.append(warning)

    return dedupe_lyrics_segments(lyrics_segments), warnings
