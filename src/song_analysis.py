"""Song analysis and chord-sheet generation helpers for AI-ChordCraft."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

from src._runtime import (
    DEFAULT_OVERALL_AUDIO_WINDOW_SECONDS,
    format_timestamp,
    generate_text as _generate_text,
    json_for_prompt as _json_for_prompt,
    normalize_timestamp,
    parse_analysis_json,
    parse_boundary_array_for_stage as _parse_boundary_array_for_stage,
    parse_chord_array_for_stage as _parse_chord_array_for_stage,
    parse_model_json,
    parse_timestamp,
    parse_timestamp_float,
    probe_audio_duration as _probe_audio_duration,
    read_sglang_base_url,
    slice_audio as _slice_audio,
    slice_audio_window as _slice_audio_window,
    strip_thinking_text as _strip_thinking_text,
)
from src.chord_recognition import (
    ChordRecognitionError,
    assign_chords_to_sections,
    choose_chords_for_intervals,
    classify_single_chord_segment,
    estimate_song_metadata,
    postprocess_chord_events,
    recognize_chords,
    snap_sections_to_chord_boundaries,
)
from src.structure_recognition import recognize_structure_with_songformer, sections_to_outline_json
from src.lyric_asr import (
    LYRIC_ASR_PROMPT,
    analyze_full_song_lyrics_with_llm as _analyze_full_song_lyrics_with_llm,
    coerce_lyrics_segments as _coerce_lyrics_segments,
    dedupe_lyrics_segments as _dedupe_lyrics_segments,
    parse_lyric_asr_text,
)


DEFAULT_WORKFLOW_MAX_SECTIONS = 12
DEFAULT_CHORD_ENGINE = "plkd-btc"
DEFAULT_STRUCTURE_ENGINE = "songformer"
DEFAULT_SECTION_BOUNDARY_SNAP_SECONDS = 1.0


OVERALL_PROMPT = """请从风格与速度、调性与和声、乐器编配、结构安排以及整体情绪几个方面描述这段音乐。
"""


SECTION_PROMPT_TEMPLATE = """
用json转录出这段音乐的和弦进行
"""


SECTION_NAME_ALIASES = {
    "intro": "前奏",
    "前奏": "前奏",
    "verse": "主歌",
    "主歌": "主歌",
    "pre-chorus": "预副歌",
    "pre chorus": "预副歌",
    "prechorus": "预副歌",
    "预副歌": "预副歌",
    "chorus": "副歌",
    "副歌": "副歌",
    "bridge": "桥段",
    "桥段": "桥段",
    "solo": "Solo",
    "独奏": "Solo",
    "outro": "尾奏",
    "尾奏": "尾奏",
    "interlude": "间奏",
    "间奏": "间奏",
    "other": "其他",
    "其他": "其他",
    "full song": "整首歌",
    "整首歌": "整首歌",
}

def _estimate_chord_segment_seconds(overall: dict[str, Any]) -> float:
    tempo = overall.get("tempo_bpm")
    try:
        bpm = float(tempo)
    except (TypeError, ValueError):
        bpm = 90.0
    if bpm <= 0:
        bpm = 90.0

    beats_per_bar = 4
    signature = str(overall.get("time_signature") or "").strip()
    match = re.match(r"^(\d{1,2})\s*/\s*\d{1,2}$", signature)
    if match:
        beats_per_bar = max(1, min(12, int(match.group(1))))

    bar_seconds = 60.0 / bpm * beats_per_bar
    return max(1.8, min(6.0, bar_seconds))


def _is_degenerate_structure(sections: list[dict[str, Any]], duration_seconds: int | float | None) -> bool:
    if not sections:
        return True
    if not isinstance(duration_seconds, (int, float)) or duration_seconds < 60:
        return False
    if len(sections) <= 1:
        return True
    if len(sections) > 2:
        return False

    first = sections[0]
    first_type = str(first.get("section_type") or _base_section_name(first.get("name"))).strip()
    start = _section_start_seconds(first)
    end = _section_end_seconds(first)
    if start is None or end is None:
        return False
    first_duration_ratio = max(0.0, end - start) / max(float(duration_seconds), 1.0)
    return first_type in {"前奏", "Intro", "整首歌", "Full Song"} and first_duration_ratio >= 0.65


def _resolve_task_base_urls(
    base_url: str | None = None,
    thinking_base_url: str | None = None,
    instruct_base_url: str | None = None,
) -> dict[str, str]:
    thinking_url = (
        thinking_base_url
        or base_url
        or os.environ.get("CHORDCRAFT_SGLANG_THINKING_BASE_URL")
        or os.environ.get("MOSS_MUSIC_SGLANG_THINKING_BASE_URL")
        or read_sglang_base_url()
    ).rstrip("/")
    instruct_url = (
        instruct_base_url
        or os.environ.get("CHORDCRAFT_SGLANG_INSTRUCT_BASE_URL")
        or os.environ.get("MOSS_MUSIC_SGLANG_INSTRUCT_BASE_URL")
        or base_url
        or thinking_url
    ).rstrip("/")
    return {
        "overall_analysis": instruct_url,
        "section_harmony_analysis": instruct_url,
        "chord_boundary_detection": instruct_url,
        "lyric_asr": instruct_url,
    }


def _detect_generate_service(base_url: str, timeout_seconds: float = 2.0) -> tuple[bool, str | None]:
    endpoint = f"{base_url.rstrip('/')}/generate"
    try:
        response = requests.get(endpoint, timeout=timeout_seconds)
    except requests.RequestException as exc:
        return False, f"MOSS-Music /generate 服务不可用，已跳过歌词 ASR：{exc}"

    if response.status_code < 500:
        return True, None
    return False, f"MOSS-Music /generate 服务返回 HTTP {response.status_code}，已跳过歌词 ASR。"



def _coerce_overall(value: Any) -> dict[str, Any]:
    overall = value if isinstance(value, dict) else {}
    return {
        "key": overall.get("key"),
        "mode": overall.get("mode"),
        "tempo_bpm": overall.get("tempo_bpm"),
        "time_signature": overall.get("time_signature"),
        "capo_suggestion": overall.get("capo_suggestion"),
        "feel": overall.get("feel"),
        "confidence": overall.get("confidence") or "low",
    }



def _base_section_name(value: Any) -> str:
    text = str(value or "其他").strip()
    if not text:
        return "其他"
    normalized = re.sub(r"\s+", " ", text).strip()
    normalized = re.sub(r"\s*(?:\d+|[一二三四五六七八九十]+)\s*$", "", normalized).strip()
    key = normalized.lower()
    return SECTION_NAME_ALIASES.get(key, SECTION_NAME_ALIASES.get(normalized, normalized))


def _number_section_names(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    for section in sections:
        base_name = _base_section_name(section.get("name"))
        section["section_type"] = base_name
        totals[base_name] = totals.get(base_name, 0) + 1

    counts: dict[str, int] = {}
    for section in sections:
        base_name = section.get("section_type") or _base_section_name(section.get("name"))
        counts[base_name] = counts.get(base_name, 0) + 1
        section["name"] = f"{base_name} {counts[base_name]}" if totals.get(base_name, 0) > 1 else base_name
    return sections


def _coalesce_adjacent_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    for section in sections:
        section_type = section.get("section_type") or _base_section_name(section.get("name"))
        source_sections = section.get("source_sections")
        if not isinstance(source_sections, list) or not source_sections:
            source_sections = [
                {
                    "name": section.get("name"),
                    "start": section.get("start"),
                    "end": section.get("end"),
                }
            ]
        current = grouped[-1] if grouped else None
        if current and current.get("section_type") == section_type:
            current["end"] = section.get("end") or current.get("end")
            current["chords"] = _merge_chord_lists(current.get("chords") or [], section.get("chords") or [])
            current.setdefault("source_sections", []).extend(source_sections)
            continue

        item = dict(section)
        item["section_type"] = section_type
        item["source_sections"] = source_sections
        grouped.append(item)

    return grouped


def _coerce_sections(value: Any, max_sections: int = DEFAULT_WORKFLOW_MAX_SECTIONS) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    sections: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        base_name = _base_section_name(item.get("name"))
        start_seconds = parse_timestamp_float(item.get("start"))
        end_seconds = parse_timestamp_float(item.get("end"))
        section = {
            "name": base_name,
            "section_type": base_name,
            "start": format_timestamp(start_seconds) if start_seconds is not None else normalize_timestamp(item.get("start")),
            "end": format_timestamp(end_seconds) if end_seconds is not None else normalize_timestamp(item.get("end")),
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "chords": item.get("chords") or [],
        }
        sections.append(section)

    sections.sort(
        key=lambda section: (
            section.get("start_seconds")
            if isinstance(section.get("start_seconds"), (int, float))
            else parse_timestamp(section.get("start")) or 0
        )
    )
    for section in sections:
        section["source_sections"] = [
            {
                "name": section.get("name"),
                "start": section.get("start"),
                "end": section.get("end"),
                "start_seconds": section.get("start_seconds"),
                "end_seconds": section.get("end_seconds"),
            }
        ]
    return _number_section_names(sections[:max_sections])


def _section_start_seconds(section: dict[str, Any]) -> float | None:
    value = section.get("start_seconds")
    if isinstance(value, (int, float)):
        return float(value)
    return parse_timestamp_float(section.get("start"))


def _section_end_seconds(section: dict[str, Any]) -> float | None:
    value = section.get("end_seconds")
    if isinstance(value, (int, float)):
        return float(value)
    return parse_timestamp_float(section.get("end"))


def _valid_section_bounds(section: dict[str, Any]) -> tuple[float, float] | None:
    start = _section_start_seconds(section)
    end = _section_end_seconds(section)
    if start is None or end is None or end <= start:
        return None
    return start, end


def _normalize_section_times(sections: list[dict[str, Any]], duration_seconds: int | None) -> list[dict[str, Any]]:
    if not sections:
        return sections

    normalized = [dict(section) for section in sections]
    if _section_start_seconds(normalized[0]) is None:
        normalized[0]["start"] = "00:00"
        normalized[0]["start_seconds"] = 0.0

    starts = [_section_start_seconds(section) for section in normalized]
    for index, section in enumerate(normalized):
        start = starts[index]
        end = _section_end_seconds(section)
        next_start = starts[index + 1] if index + 1 < len(starts) else None

        if start is None:
            previous_end = _section_end_seconds(normalized[index - 1]) if index > 0 else 0.0
            start = previous_end if previous_end is not None else 0
            section["start"] = format_timestamp(start)
            section["start_seconds"] = float(start)
            starts[index] = start

        if end is None and next_start is not None and next_start > start:
            section["end"] = format_timestamp(next_start)
            section["end_seconds"] = float(next_start)
        elif end is None and duration_seconds is not None and duration_seconds > start:
            section["end"] = format_timestamp(duration_seconds)
            section["end_seconds"] = float(duration_seconds)
        elif end is not None and end <= start:
            fallback_end = next_start if next_start is not None and next_start > start else duration_seconds
            if fallback_end is not None and fallback_end > start:
                section["end"] = format_timestamp(fallback_end)
                section["end_seconds"] = float(fallback_end)

    return normalized


def _normalize_chord_item(item: Any, section_start_seconds: int = 0) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    chord = str(item.get("chord") or "").strip()
    if not chord:
        return None
    relative_seconds = parse_timestamp(item.get("time"))
    absolute_seconds = None if relative_seconds is None else section_start_seconds + relative_seconds
    return {
        "time": format_timestamp(absolute_seconds),
        "chord": chord,
    }


def _normalize_chords(value: Any, section_start_seconds: int = 0) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    chords = []
    for item in value:
        normalized = _normalize_chord_item(item, section_start_seconds=section_start_seconds)
        if normalized:
            chords.append(normalized)
    return chords


def _merge_section_detail(outline_section: dict[str, Any], detail: Any) -> dict[str, Any]:
    merged = dict(outline_section)
    section_start_seconds = parse_timestamp(outline_section.get("start")) or 0
    if isinstance(detail, list):
        chords = _normalize_chords(detail, section_start_seconds=section_start_seconds)
    elif isinstance(detail, dict):
        chords = _normalize_chords(detail.get("chords"), section_start_seconds=section_start_seconds)
    else:
        chords = []

    if chords:
        merged["chords"] = chords
    else:
        merged["chords"] = []

    merged["name"] = outline_section.get("name") or "Other"
    merged["start"] = outline_section.get("start")
    merged["end"] = outline_section.get("end")
    if isinstance(detail, dict):
        if detail.get("local_key"):
            merged["local_key"] = detail.get("local_key")
        if detail.get("confidence"):
            merged["confidence"] = detail.get("confidence")
    merged["section_type"] = outline_section.get("section_type") or _base_section_name(merged.get("name"))
    return merged


def _merge_chord_lists(left: list[Any], right: list[Any]) -> list[Any]:
    merged = [item for item in left if isinstance(item, dict)]
    for item in right:
        if isinstance(item, dict):
            merged.append(item)

    def sort_key(item: dict[str, Any]) -> tuple[int, int]:
        seconds = parse_timestamp(item.get("time"))
        return (1, 10**9) if seconds is None else (0, seconds)

    merged.sort(key=sort_key)
    return merged


def _combine_text_parts(parts: list[Any]) -> str:
    cleaned = []
    for part in parts:
        text = str(part or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return "；".join(cleaned)


def _group_display_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    for section in sections:
        section_type = section.get("section_type") or _base_section_name(section.get("name"))
        child = dict(section)
        child["section_type"] = section_type
        child.pop("child_sections", None)

        current = grouped[-1] if grouped else None
        if current and current.get("section_type") == section_type:
            current["end"] = child.get("end") or current.get("end")
            current["chords"] = _merge_chord_lists(current.get("chords") or [], child.get("chords") or [])
            current["child_sections"].append(child)
            continue

        grouped.append(
            {
                "name": section_type,
                "section_type": section_type,
                "start": child.get("start"),
                "end": child.get("end"),
                "chords": list(child.get("chords") or []),
                "child_sections": [child],
            }
        )

    return _number_section_names(grouped)


def _derive_global_progressions(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    progressions: list[dict[str, Any]] = []
    for section in sections:
        chords = []
        for item in section.get("chords") or []:
            if isinstance(item, dict) and item.get("chord"):
                chord = str(item["chord"]).strip()
                if chord and (not chords or chords[-1] != chord):
                    chords.append(chord)
        if not chords:
            likely = section.get("likely_chords") or []
            chords = [str(chord).strip() for chord in likely if str(chord).strip()]
        if not chords:
            continue
        progression = tuple(chords[:8])
        if progression in seen:
            continue
        seen.add(progression)
        progressions.append(
            {
                "label": str(section.get("name") or "段落"),
                "progression": list(progression),
                "where": f"{section.get('start') or '?'} - {section.get('end') or '?'}",
            }
        )
        if len(progressions) >= 6:
            break
    return progressions


def _build_practice_tips(analysis: dict[str, Any]) -> list[str]:
    overall = analysis.get("overall") or {}
    tips = []
    if overall.get("tempo_bpm"):
        tips.append(f"先用 {overall['tempo_bpm']} BPM 的 70%-80% 慢速练习，再回到原速。")
    if overall.get("capo_suggestion"):
        tips.append(f"按 {overall['capo_suggestion']} 试一版开放和弦指法，比较是否更接近原曲音色。")
    tips.append("先循环练主歌/副歌的核心走向，再把前奏、桥段和尾奏接进完整结构。")
    tips.append("对标注 ? 的和弦单独回放确认低音走向和三音色彩。")
    return tips


def _normalize_analysis_sections(analysis: dict[str, Any]) -> dict[str, Any]:
    sections = analysis.get("sections")
    if not isinstance(sections, list):
        return analysis

    normalized: list[dict[str, Any]] = []
    for item in sections:
        if not isinstance(item, dict):
            continue
        section = dict(item)
        base_name = _base_section_name(section.get("name"))
        section["name"] = base_name
        section["section_type"] = base_name
        section["summary"] = section.get("summary") or ""
        section["likely_chords"] = section.get("likely_chords") or []
        section["chords"] = section.get("chords") or []
        normalized.append(section)

    normalized.sort(key=lambda section: parse_timestamp(section.get("start")) or 0)
    raw_sections = _number_section_names([dict(section) for section in normalized])
    analysis["raw_sections"] = raw_sections
    analysis["sections"] = _group_display_sections(raw_sections)

    if not analysis.get("global_chord_progressions"):
        analysis["global_chord_progressions"] = _derive_global_progressions(analysis["sections"])
    if not analysis.get("practice_tips"):
        analysis["practice_tips"] = _build_practice_tips(analysis)
    return analysis


def _analyze_section_chords_with_llm(
    audio_path: str,
    sections: list[dict[str, Any]],
    song_context: dict[str, Any],
    raw_steps: dict[str, Any],
    task_base_urls: dict[str, str],
    backend: str,
    api_key: str | None,
    model_name_or_path: str | None,
    device: str | None,
    section_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    detailed_sections: list[dict[str, Any]] = []
    detail_parse_warnings: list[str] = []
    slicing_available = True
    slicing_fallback_used = False

    with tempfile.TemporaryDirectory(prefix="moss-sections-") as temp_dir:
        for index, section in enumerate(sections, start=1):
            section_audio_path = audio_path
            bounds = _valid_section_bounds(section)
            audio_mode = "full"
            if bounds is not None and slicing_available:
                start, end = bounds
                section_audio_path = str(Path(temp_dir) / f"section-{index:02d}.wav")
                try:
                    _slice_audio(audio_path, section_audio_path, start, end)
                    audio_mode = "sliced"
                except RuntimeError as exc:
                    slicing_available = False
                    slicing_fallback_used = True
                    section_audio_path = audio_path
                    audio_mode = "full_audio_fallback"
                    raw_steps.setdefault("slicing_warnings", []).append(str(exc))
            elif bounds is not None and not slicing_available:
                slicing_fallback_used = True
                audio_mode = "full_audio_fallback"

            section_prompt = SECTION_PROMPT_TEMPLATE.replace(
                "{song_context}", _json_for_prompt(song_context)
            ).replace("{section_context}", _json_for_prompt(section))
            detail_raw = _generate_text(
                audio_path=section_audio_path,
                prompt=section_prompt,
                backend=backend,
                base_url=task_base_urls["section_harmony_analysis"],
                api_key=api_key,
                model_name_or_path=model_name_or_path,
                device=device,
                max_new_tokens=section_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
            )
            raw_steps.setdefault("section_details", []).append(
                {
                    "section": section,
                    "audio": audio_mode,
                    "raw": detail_raw,
                }
            )
            try:
                detail = _parse_chord_array_for_stage(
                    detail_raw,
                    f"段落和声分析阶段（{section.get('name') or f'第 {index} 段'}）",
                )
            except ModelJSONParseError as exc:
                warning = (
                    f"{section.get('name') or f'第 {index} 段'} 的和声分析没有返回合法 JSON，"
                    "已保留结构段落并跳过该段细节。"
                )
                raw_steps["section_details"][-1]["parse_error"] = str(exc)
                detail_parse_warnings.append(warning)
                detailed_sections.append(_merge_section_detail(section, []))
                continue
            detailed_sections.append(_merge_section_detail(section, detail))

    return detailed_sections, detail_parse_warnings, slicing_fallback_used


def _intervals_from_boundary_items(
    section: dict[str, Any],
    boundary_items: list[dict[str, Any]],
    min_gap_seconds: float = 0.8,
    max_interval_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    section_start = parse_timestamp_float(section.get("start")) or 0.0
    section_end = parse_timestamp_float(section.get("end"))
    if section_end is None or section_end <= section_start:
        return [], []

    points = [section_start]
    for item in boundary_items:
        relative = parse_timestamp_float(item.get("time"))
        if relative is None:
            continue
        absolute = section_start + relative
        if section_start <= absolute < section_end:
            points.append(absolute)

    if max_interval_seconds is not None and max_interval_seconds > 0:
        cursor = section_start + max_interval_seconds
        while cursor < section_end:
            points.append(cursor)
            cursor += max_interval_seconds
    points.append(section_end)

    cleaned_points: list[float] = []
    for point in sorted(set(round(value, 3) for value in points)):
        if cleaned_points and point - cleaned_points[-1] < min_gap_seconds:
            continue
        cleaned_points.append(point)
    if cleaned_points[-1] < section_end:
        cleaned_points.append(section_end)

    boundaries = [
        {
            "time": format_timestamp(point),
            "time_seconds": point,
        }
        for point in cleaned_points
    ]
    intervals = []
    for start, end in zip(cleaned_points, cleaned_points[1:]):
        if end <= start:
            continue
        intervals.append(
            {
                "section": section.get("name"),
                "section_start": section.get("start"),
                "section_end": section.get("end"),
                "start": format_timestamp(start),
                "end": format_timestamp(end),
                "start_seconds": start,
                "end_seconds": end,
            }
        )
    return intervals, boundaries


def _detect_chord_boundaries_with_llm(
    audio_path: str,
    sections: list[dict[str, Any]],
    raw_steps: dict[str, Any],
    task_base_urls: dict[str, str],
    backend: str,
    api_key: str | None,
    model_name_or_path: str | None,
    device: str | None,
    boundary_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
    chord_segment_seconds: float,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    intervals: list[dict[str, Any]] = []
    warnings: list[str] = []
    slicing_available = True
    slicing_fallback_used = False

    with tempfile.TemporaryDirectory(prefix="moss-boundaries-") as temp_dir:
        for index, section in enumerate(sections, start=1):
            section_audio_path = audio_path
            bounds = _valid_section_bounds(section)
            audio_mode = "full"
            if bounds is not None and slicing_available:
                start, end = bounds
                section_audio_path = str(Path(temp_dir) / f"boundary-section-{index:02d}.wav")
                try:
                    _slice_audio(audio_path, section_audio_path, start, end)
                    audio_mode = "sliced"
                except RuntimeError as exc:
                    slicing_available = False
                    slicing_fallback_used = True
                    section_audio_path = audio_path
                    audio_mode = "full_audio_fallback"
                    raw_steps.setdefault("slicing_warnings", []).append(str(exc))
            elif bounds is not None and not slicing_available:
                slicing_fallback_used = True
                audio_mode = "full_audio_fallback"

            prompt = SECTION_PROMPT_TEMPLATE.replace(
                "{song_context}",
                "{}",
            ).replace("{section_context}", _json_for_prompt(section))
            raw = _generate_text(
                audio_path=section_audio_path,
                prompt=prompt,
                backend=backend,
                base_url=task_base_urls["chord_boundary_detection"],
                api_key=api_key,
                model_name_or_path=model_name_or_path,
                device=device,
                max_new_tokens=boundary_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
            )
            item = {
                "section": section,
                "audio": audio_mode,
                "prompt_mode": "section_chord_prompt_time_only",
                "raw": raw,
            }
            raw_steps.setdefault("chord_boundaries", []).append(item)
            try:
                boundary_items = _parse_boundary_array_for_stage(
                    raw,
                    f"和弦换点标注阶段（{section.get('name') or f'第 {index} 段'}）",
                )
            except ModelJSONParseError as exc:
                boundary_items = [{"time": 0.0}]
                item["parse_error"] = str(exc)
                warnings.append(
                    f"{section.get('name') or f'第 {index} 段'} 的和弦换点没有返回合法 JSON，"
                    "已退化为按速度/拍号生成的小节级和弦区间。"
                )

            section_intervals, boundaries = _intervals_from_boundary_items(
                section,
                boundary_items,
                max_interval_seconds=chord_segment_seconds,
            )
            item["boundaries"] = boundaries
            item["intervals"] = section_intervals
            item["grid_segment_seconds"] = chord_segment_seconds
            intervals.extend(section_intervals)

    return intervals, warnings, slicing_fallback_used


def _select_hybrid_chords_by_interval_slices(
    audio_path: str,
    intervals: list[dict[str, Any]],
    full_song_events: list[dict[str, Any]],
    key: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected_events: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    previous_by_section: dict[str, str] = {}

    with tempfile.TemporaryDirectory(prefix="moss-chord-intervals-") as temp_dir:
        for index, interval in enumerate(intervals, start=1):
            start = parse_timestamp_float(interval.get("start_seconds"))
            end = parse_timestamp_float(interval.get("end_seconds"))
            section_name = str(interval.get("section") or "")
            if start is None or end is None or end <= start:
                continue
            duration = end - start
            previous_chord = previous_by_section.get(section_name)
            decision: dict[str, Any] = {
                "start": format_timestamp(start),
                "end": format_timestamp(end),
                "start_seconds": start,
                "end_seconds": end,
                "section": interval.get("section"),
                "method": "single_segment_hpcp_template",
                "selected": None,
                "candidates": [],
            }

            try:
                segment_path = str(Path(temp_dir) / f"chord-interval-{index:03d}.wav")
                _slice_audio(audio_path, segment_path, start, end)
                choice = classify_single_chord_segment(
                    segment_path,
                    key=key,
                    top_k=5,
                )
                decision["segment_analysis"] = {
                    "source": choice.get("source"),
                    "duration_seconds": choice.get("duration_seconds"),
                    "frame_count": choice.get("frame_count"),
                    "chroma": choice.get("chroma"),
                    "bass_chroma": choice.get("bass_chroma"),
                }
                decision["candidates"] = choice.get("candidates") or []
                decision["selected"] = choice.get("selected")
            except (ChordRecognitionError, RuntimeError) as exc:
                decision["method"] = "full_song_interval_vote_fallback"
                decision["segment_error"] = str(exc)
                _, fallback_decisions = choose_chords_for_intervals(full_song_events, [interval])
                fallback = fallback_decisions[0] if fallback_decisions else {}
                candidates = fallback.get("candidates") if isinstance(fallback, dict) else []
                selected = fallback.get("selected") if isinstance(fallback, dict) else None
                if previous_chord and isinstance(candidates, list) and len(candidates) > 1:
                    selected = next(
                        (candidate for candidate in candidates if candidate.get("chord") != previous_chord),
                        selected,
                    )
                decision["candidates"] = candidates or []
                decision["selected"] = selected

            selected = decision.get("selected")
            if not isinstance(selected, dict) or not selected.get("chord"):
                decisions.append(decision)
                continue

            chord = str(selected["chord"])
            if previous_chord == chord:
                decision["suppressed"] = "same_as_previous_chord"
                decisions.append(decision)
                continue

            event = {
                "time": format_timestamp(start),
                "end": format_timestamp(end),
                "time_seconds": start,
                "end_seconds": end,
                "chord": chord,
                "source": decision.get("method"),
                "confidence": selected.get("max_confidence"),
            }
            selected_events.append(event)
            previous_by_section[section_name] = chord
            decisions.append(decision)

    return selected_events, decisions



def analyze_song_workflow(
    audio_path: str,
    backend: str = "sglang",
    base_url: str | None = None,
    thinking_base_url: str | None = None,
    instruct_base_url: str | None = None,
    api_key: str | None = None,
    model_name_or_path: str | None = None,
    device: str | None = None,
    max_new_tokens: int = 4096,
    temperature: float = 0.0,
    top_p: float = 1.0,
    top_k: int = 50,
    max_sections: int = DEFAULT_WORKFLOW_MAX_SECTIONS,
    chord_engine: str = DEFAULT_CHORD_ENGINE,
    structure_engine: str = DEFAULT_STRUCTURE_ENGINE,
) -> tuple[dict[str, Any], str, float]:
    started_at = time.perf_counter()
    resolved_structure_engine = (structure_engine or DEFAULT_STRUCTURE_ENGINE).strip().lower()
    if resolved_structure_engine not in {"songformer", "songformer-service", "songformer-http", "songformer-local"}:
        raise ValueError("structure_engine only supports SongFormer: songformer, songformer-service, or songformer-local.")

    overall_tokens = min(max(max_new_tokens, 1024), 2048)
    lyric_tokens = min(max(max_new_tokens, 1536), 4096)
    section_tokens = min(max(max_new_tokens, 1536), 3072)
    boundary_tokens = min(max(max_new_tokens, 768), 1536)
    task_base_urls = _resolve_task_base_urls(
        base_url=base_url,
        thinking_base_url=thinking_base_url,
        instruct_base_url=instruct_base_url,
    )
    llm_service_available, llm_service_warning = (
        _detect_generate_service(task_base_urls["lyric_asr"])
        if backend == "sglang"
        else (False, f"后端 {backend} 不支持 MOSS-Music /generate，已跳过歌词 ASR。")
    )
    duration_seconds = _probe_audio_duration(audio_path)

    overall_audio_info: dict[str, Any] = {"audio": "skipped", "reason": "moss_music_service_unavailable"}
    overall_raw = ""
    if llm_service_available:
        overall_audio_info = {"audio": "full"}
        with tempfile.TemporaryDirectory(prefix="moss-overall-") as overall_temp_dir:
            overall_audio_path = audio_path
            try:
                overall_audio_path, overall_audio_info = _slice_audio_window(
                    audio_path,
                    str(Path(overall_temp_dir) / "overall-window.wav"),
                    duration_seconds,
                    max_window_seconds=DEFAULT_OVERALL_AUDIO_WINDOW_SECONDS,
                )
                overall_raw = _generate_text(
                    audio_path=overall_audio_path,
                    prompt=OVERALL_PROMPT,
                    backend=backend,
                    base_url=task_base_urls["overall_analysis"],
                    api_key=api_key,
                    model_name_or_path=model_name_or_path,
                    device=device,
                    max_new_tokens=overall_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    top_k=top_k,
                )
            except RuntimeError as exc:
                overall_audio_info = {
                    "audio": "skipped_slice_error",
                    "error": str(exc),
                    "reason": "avoid_llm_context_overflow",
                }
    song_description = _strip_thinking_text(overall_raw).strip()
    overall_analysis: dict[str, Any] = {}
    overall = _coerce_overall(overall_analysis)
    chord_segment_seconds = _estimate_chord_segment_seconds(overall)

    songformer_mode = "local" if resolved_structure_engine == "songformer-local" else "service"
    structure_source = f"songformer-{songformer_mode}"
    structure_warning = None
    structure_recognition: dict[str, Any] | None = None
    outline_raw = ""
    outline: dict[str, Any] = {}
    sections: list[dict[str, Any]] = []
    songformer_sections, songformer_raw = recognize_structure_with_songformer(
        audio_path,
        mode=songformer_mode,
        duration_seconds=duration_seconds,
        max_sections=max(max_sections, 64),
    )
    outline_raw = sections_to_outline_json(songformer_sections)
    outline = {"sections": songformer_sections}
    sections = _normalize_section_times(
        _coerce_sections(songformer_sections, max_sections=max(max_sections, len(songformer_sections))),
        duration_seconds=duration_seconds,
    )
    structure_recognition = songformer_raw
    if _is_degenerate_structure(sections, duration_seconds):
        structure_warning = "SongFormer 结构划分结果较粗，已保留 SongFormer 原始结果。"

    lyric_parse_warning = None
    lyrics_segments: list[dict[str, Any]] = []
    resolved_chord_engine = (chord_engine or DEFAULT_CHORD_ENGINE).strip().lower()
    if resolved_chord_engine == "llm":
        chord_workflow_steps = ["audio_section_slicing", "section_harmony_analysis"]
    elif resolved_chord_engine == "hybrid":
        chord_workflow_steps = [
            "chord_boundary_detection",
            "automatic_chord_recognition",
            "hybrid_chord_selection",
            "map_chords_to_sections",
        ]
    else:
        chord_workflow_steps = ["automatic_chord_recognition", "map_chords_to_sections"]

    raw_steps: dict[str, Any] = {
        "workflow": [
            "overall_analysis" if llm_service_available else "overall_analysis_skipped",
            "songformer_structure",
            "lyric_asr" if llm_service_available else "lyric_asr_skipped",
            *chord_workflow_steps,
            "group_sections",
        ],
        "llm_service_available": llm_service_available,
        "llm_service_warning": llm_service_warning,
        "chord_engine": resolved_chord_engine,
        "structure_engine": resolved_structure_engine,
        "structure_source": structure_source,
        "chord_segment_seconds": chord_segment_seconds,
        "overall_analysis": overall_raw,
        "overall_analysis_audio": overall_audio_info,
        "lyric_asr": [],
        "lyric_asr_parse_error": None,
        "songformer_structure": outline_raw,
        "structure_recognition": structure_recognition,
        "structure_recognition_warning": structure_warning,
        "chord_recognition": None,
        "chord_boundaries": [],
        "hybrid_chord_selection": None,
        "section_boundary_alignment": None,
        "section_details": [],
        "slicing_warnings": [],
        "task_base_urls": task_base_urls if backend == "sglang" else {},
    }
    lyric_warnings: list[str] = []
    if llm_service_available:
        lyrics_segments, lyric_warnings = _analyze_full_song_lyrics_with_llm(
            audio_path=audio_path,
            raw_steps=raw_steps,
            task_base_urls=task_base_urls,
            backend=backend,
            api_key=api_key,
            model_name_or_path=model_name_or_path,
            device=device,
            lyric_tokens=lyric_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
        )
        if lyric_warnings:
            lyric_parse_warning = "歌词 ASR 没有返回可解析的带时间戳歌词行。"
        elif not lyrics_segments:
            lyric_parse_warning = "歌词 ASR 没有返回可解析的带时间戳歌词行，已跳过歌词结果。"
    raw_steps["lyric_asr_parse_error"] = lyric_parse_warning
    detailed_sections: list[dict[str, Any]] = []
    detail_parse_warnings: list[str] = []
    boundary_parse_warnings: list[str] = []
    chord_recognition_warning = None
    metadata_warning = None
    automatic_metadata: dict[str, Any] = {}
    song_context = {
        "title_guess": None,
        "song_description": song_description,
        "overall": _coerce_overall(overall_analysis),
        "sections": [
            {
                "name": section.get("name"),
                "start": section.get("start"),
                "end": section.get("end"),
            }
            for section in sections
        ],
    }

    slicing_fallback_used = False
    if resolved_chord_engine == "llm":
        detailed_sections, detail_parse_warnings, slicing_fallback_used = _analyze_section_chords_with_llm(
            audio_path=audio_path,
            sections=sections,
            song_context=song_context,
            raw_steps=raw_steps,
            task_base_urls=task_base_urls,
            backend=backend,
            api_key=api_key,
            model_name_or_path=model_name_or_path,
            device=device,
            section_tokens=section_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
        )
    elif resolved_chord_engine == "hybrid":
        intervals, boundary_parse_warnings, slicing_fallback_used = _detect_chord_boundaries_with_llm(
            audio_path=audio_path,
            sections=sections,
            raw_steps=raw_steps,
            task_base_urls=task_base_urls,
            backend=backend,
            api_key=api_key,
            model_name_or_path=model_name_or_path,
            device=device,
            boundary_tokens=boundary_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            chord_segment_seconds=chord_segment_seconds,
        )
        if not intervals:
            for section in sections:
                section_intervals, _ = _intervals_from_boundary_items(
                    section,
                    [{"time": 0.0}],
                    max_interval_seconds=chord_segment_seconds,
                )
                intervals.extend(section_intervals)
        try:
            raw_chord_events = recognize_chords(audio_path, engine="essentia")
            hybrid_events, decisions = _select_hybrid_chords_by_interval_slices(
                audio_path=audio_path,
                intervals=intervals,
                full_song_events=raw_chord_events,
                key=None,
            )
            chord_events = postprocess_chord_events(
                hybrid_events,
                key=None,
                min_duration_seconds=0.0,
                low_confidence_threshold=-1.0,
            )
            raw_steps["chord_recognition"] = {
                "engine": "essentia",
                "used_by": "hybrid",
                "raw_events": raw_chord_events,
            }
            automatic_metadata = estimate_song_metadata(audio_path, chord_events)
            raw_steps["automatic_metadata"] = automatic_metadata
            raw_steps["hybrid_chord_selection"] = {
                "intervals": intervals,
                "decisions": decisions,
                "events": hybrid_events,
                "postprocessed_events": chord_events,
                "segment_analysis": True,
                "fallback": "full_song_interval_vote",
                "postprocessing": {
                    "enabled": True,
                    "mode": "light",
                    "min_duration_seconds": 0.0,
                    "low_confidence_threshold": -1.0,
                },
            }
            aligned_sections, boundary_adjustments = snap_sections_to_chord_boundaries(
                sections,
                chord_events,
                max_snap_seconds=DEFAULT_SECTION_BOUNDARY_SNAP_SECONDS,
            )
            raw_steps["section_boundary_alignment"] = {
                "enabled": True,
                "max_snap_seconds": DEFAULT_SECTION_BOUNDARY_SNAP_SECONDS,
                "adjustments": boundary_adjustments,
            }
            detailed_sections = assign_chords_to_sections(chord_events, aligned_sections)
        except ChordRecognitionError as exc:
            chord_recognition_warning = (
                f"Hybrid 和弦识别需要 Essentia，但自动和弦识别执行失败：{exc}"
            )
            raw_steps["chord_recognition"] = {
                "engine": "essentia",
                "used_by": "hybrid",
                "error": str(exc),
            }
            raw_steps["hybrid_chord_selection"] = {
                "intervals": intervals,
                "error": str(exc),
            }
            detailed_sections = [dict(section, chords=[]) for section in sections]
    else:
        try:
            raw_chord_events = recognize_chords(audio_path, engine=resolved_chord_engine)
            chord_events = postprocess_chord_events(
                raw_chord_events,
                key=None,
            )
            raw_steps["chord_recognition"] = {
                "engine": resolved_chord_engine,
                "postprocessing": {
                    "enabled": True,
                    "key": None,
                    "min_duration_seconds": 1.2,
                    "low_confidence_threshold": 0.18,
                },
                "raw_events": raw_chord_events,
                "events": chord_events,
            }
            automatic_metadata = estimate_song_metadata(audio_path, chord_events)
            raw_steps["automatic_metadata"] = automatic_metadata
            aligned_sections, boundary_adjustments = snap_sections_to_chord_boundaries(
                sections,
                chord_events,
                max_snap_seconds=DEFAULT_SECTION_BOUNDARY_SNAP_SECONDS,
            )
            raw_steps["section_boundary_alignment"] = {
                "enabled": True,
                "max_snap_seconds": DEFAULT_SECTION_BOUNDARY_SNAP_SECONDS,
                "adjustments": boundary_adjustments,
            }
            detailed_sections = assign_chords_to_sections(chord_events, aligned_sections)
        except ChordRecognitionError as exc:
            chord_recognition_warning = (
                f"自动和弦识别引擎 {resolved_chord_engine} 不可用或执行失败：{exc}"
            )
            raw_steps["chord_recognition"] = {
                "engine": resolved_chord_engine,
                "error": str(exc),
            }
            detailed_sections = [dict(section, chords=[]) for section in sections]

    if not automatic_metadata:
        automatic_metadata = estimate_song_metadata(audio_path, [])
        raw_steps["automatic_metadata"] = automatic_metadata
    if not any(automatic_metadata.get(key) for key in ["key", "mode", "tempo_bpm", "time_signature"]):
        metadata_warning = "自动基础信息估计没有得到可用的调性、速度或拍号结果。"

    overall = {
        "key": automatic_metadata.get("key"),
        "mode": automatic_metadata.get("mode"),
        "tempo_bpm": automatic_metadata.get("tempo_bpm"),
        "time_signature": automatic_metadata.get("time_signature"),
        "capo_suggestion": None,
        "feel": None,
        "confidence": automatic_metadata.get("confidence") or "low",
    }

    display_sections = _group_display_sections(detailed_sections)
    if resolved_chord_engine == "llm":
        workflow_chord_steps = [
            {"name": "audio_section_slicing", "status": "fallback" if slicing_fallback_used else "done"},
            {
                "name": "section_harmony_analysis",
                "status": "partial" if detail_parse_warnings else "done",
                "sections": len(detailed_sections),
            },
        ]
    elif resolved_chord_engine == "hybrid":
        workflow_chord_steps = [
            {
                "name": "chord_boundary_detection",
                "status": "partial" if boundary_parse_warnings or slicing_fallback_used else "done",
                "sections": len(sections),
            },
            {
                "name": "automatic_chord_recognition",
                "status": "partial" if chord_recognition_warning else "done",
                "engine": "essentia",
            },
            {
                "name": "hybrid_chord_selection",
                "status": "partial" if chord_recognition_warning else "done",
                "sections": len(detailed_sections),
            },
            {"name": "map_chords_to_sections", "status": "done", "sections": len(detailed_sections)},
        ]
    else:
        workflow_chord_steps = [
            {
                "name": "automatic_chord_recognition",
                "status": "partial" if chord_recognition_warning else "done",
                "engine": resolved_chord_engine,
            },
            {"name": "map_chords_to_sections", "status": "done", "sections": len(detailed_sections)},
        ]

    workflow_notes: list[str] = []
    if resolved_chord_engine == "llm" and slicing_fallback_used:
        workflow_notes.append("当前环境无法使用 ffmpeg 切分音频，和弦逐段分析已改用整首音频加段落时间戳。")

    analysis = {
        "title_guess": None,
        "song_description": song_description,
        "overall": overall,
        "lyrics_segments": lyrics_segments,
        "sections": display_sections,
        "raw_sections": detailed_sections,
        "global_chord_progressions": _derive_global_progressions(display_sections),
        "practice_tips": _build_practice_tips(
            {"overall": overall, "sections": display_sections}
        ),
        "uncertain_points": [],
        "workflow": {
            "mode": "staged",
            "llm_service_available": llm_service_available,
            "steps": [
                {
                    "name": "overall_analysis",
                    "status": "done" if llm_service_available else "skipped",
                },
                {
                    "name": "songformer_structure",
                    "status": "partial" if structure_warning else "done",
                    "engine": structure_source,
                },
                {
                    "name": "lyric_asr",
                    "status": (
                        "skipped"
                        if not llm_service_available
                        else "partial" if lyric_parse_warning else "done"
                    ),
                    "audio": "full_song",
                    "lyrics": len(lyrics_segments),
                },
                *workflow_chord_steps,
                {"name": "group_sections", "status": "done", "sections": len(display_sections)},
            ],
            "notes": workflow_notes,
        },
    }
    if resolved_chord_engine == "llm" and slicing_fallback_used:
        analysis["uncertain_points"].append(
            "当前环境无法使用 ffmpeg 切分音频，逐段分析已降级为使用整首音频配合段落时间戳。"
        )
    if llm_service_warning:
        analysis["uncertain_points"].append(llm_service_warning)
    if chord_recognition_warning:
        analysis["uncertain_points"].append(chord_recognition_warning)
    if lyric_parse_warning:
        analysis["uncertain_points"].append(lyric_parse_warning)
    if structure_warning:
        analysis["uncertain_points"].append(structure_warning)
    if metadata_warning:
        analysis["uncertain_points"].append(metadata_warning)
    analysis["uncertain_points"].extend(boundary_parse_warnings)
    analysis["uncertain_points"].extend(detail_parse_warnings)
    analysis["uncertain_points"].extend(lyric_warnings)

    raw_text = json.dumps(raw_steps, ensure_ascii=False, indent=2)
    return analysis, raw_text, time.perf_counter() - started_at


def analyze_song(
    audio_path: str,
    backend: str = "sglang",
    base_url: str | None = None,
    thinking_base_url: str | None = None,
    instruct_base_url: str | None = None,
    api_key: str | None = None,
    model_name_or_path: str | None = None,
    device: str | None = None,
    max_new_tokens: int = 4096,
    temperature: float = 0.0,
    top_p: float = 1.0,
    top_k: int = 50,
    workflow: bool = True,
    max_sections: int = DEFAULT_WORKFLOW_MAX_SECTIONS,
    chord_engine: str = DEFAULT_CHORD_ENGINE,
    structure_engine: str = DEFAULT_STRUCTURE_ENGINE,
) -> tuple[dict[str, Any], str, float]:
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio file does not exist: {audio_path}")

    if not workflow:
        raise ValueError("One-shot analysis has been removed. Use the staged workflow.")

    return analyze_song_workflow(
        audio_path=audio_path,
        backend=backend,
        base_url=base_url,
        thinking_base_url=thinking_base_url,
        instruct_base_url=instruct_base_url,
        api_key=api_key,
        model_name_or_path=model_name_or_path,
        device=device,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        max_sections=max_sections,
        chord_engine=chord_engine,
        structure_engine=structure_engine,
    )


def _value(value: Any, fallback: str = "-") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _format_chord_line(chords: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    for index, item in enumerate(chords, start=1):
        time_label = _value(item.get("time"), "")
        if item.get("end"):
            time_label = f"{time_label}-{_value(item.get('end'), '')}"
        chord = _value(item.get("display_chord") or item.get("chord"))

        cell = f"{time_label} {chord}".strip()
        current.append(cell)

        if index % 4 == 0:
            lines.append(" | ".join(current))
            current = []
    if current:
        lines.append(" | ".join(current))
    return lines


def _lyrics_for_section(section: dict[str, Any], lyrics_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    section_start = parse_timestamp_float(section.get("start_seconds")) or parse_timestamp_float(section.get("start"))
    section_end = parse_timestamp_float(section.get("end_seconds")) or parse_timestamp_float(section.get("end"))
    if section_start is None or section_end is None or section_end <= section_start:
        return []

    matched = []
    for segment in lyrics_segments:
        if not isinstance(segment, dict):
            continue
        lyric_start = parse_timestamp_float(segment.get("start_seconds")) or parse_timestamp_float(segment.get("start"))
        lyric_end = parse_timestamp_float(segment.get("end_seconds")) or parse_timestamp_float(segment.get("end"))
        if lyric_start is None:
            continue
        if lyric_end is None or lyric_end <= lyric_start:
            lyric_end = lyric_start + 1
        if lyric_end > section_start and lyric_start < section_end:
            matched.append(segment)
    matched.sort(key=lambda item: parse_timestamp_float(item.get("start_seconds")) or parse_timestamp_float(item.get("start")) or 0)
    return matched


def render_chord_sheet(analysis: dict[str, Any], source_name: str | None = None) -> str:
    overall = analysis.get("overall") or {}
    title = _value(analysis.get("title_guess"), Path(source_name).stem if source_name else "未命名歌曲")

    lines = [
        f"# {title}",
        "",
        "## 歌曲概览",
        "",
        f"- 音频来源：{_value(source_name)}",
        f"- 调性：{_value(overall.get('key'))}",
        f"- 调式：{_value(overall.get('mode'))}",
        f"- 速度：{_value(overall.get('tempo_bpm'))} BPM",
        f"- 拍号：{_value(overall.get('time_signature'))}",
        f"- 置信度：{_value(overall.get('confidence'))}",
        "",
    ]

    song_description = str(analysis.get("song_description") or "").strip()
    if song_description:
        lines.extend(["## 整体听感", "", song_description, ""])

    lines.extend(["", "## 和弦谱", ""])

    sections = analysis.get("sections") or []
    lyrics_segments = analysis.get("lyrics_segments") or []
    if not sections:
        lines.append("_没有返回段落分析。_")
    for section in sections:
        name = _value(section.get("name"), "段落")
        start = _value(section.get("start"), "?")
        end = _value(section.get("end"), "?")
        lines.extend(
            [
                f"### {name} [{start} - {end}]",
                "",
            ]
        )
        child_sections = section.get("child_sections") if isinstance(section.get("child_sections"), list) else []
        if not child_sections:
            child_sections = [section]

        for child in child_sections:
            child_name = _value(child.get("name"), "小段落")
            child_start = _value(child.get("start"), "?")
            child_end = _value(child.get("end"), "?")
            lines.extend([f"#### {child_name} [{child_start} - {child_end}]", ""])
            chord_lines = _format_chord_line(child.get("chords") or [])
            if chord_lines:
                lines.extend(chord_lines)
            else:
                lines.append("_这个小段落没有返回和弦。_")
            child_lyrics = _lyrics_for_section(child, lyrics_segments if isinstance(lyrics_segments, list) else [])
            if child_lyrics:
                lines.append("")
                for segment in child_lyrics:
                    lines.append(
                        f"[{_value(segment.get('start'), '?')} - {_value(segment.get('end'), '?')}] "
                        f"{_value(segment.get('text'))}"
                    )
            lines.append("")
        lines.append("")

    lines.extend(["## 练习建议", ""])
    tips = analysis.get("practice_tips") or []
    lines.extend(f"- {tip}" for tip in tips) if tips else lines.append("- -")

    uncertain_points = analysis.get("uncertain_points") or []
    if uncertain_points:
        lines.extend(["", "## 不确定点", ""])
        lines.extend(f"- {point}" for point in uncertain_points)

    return "\n".join(lines).strip() + "\n"


__all__ = [
    "OVERALL_PROMPT",
    "LYRIC_ASR_PROMPT",
    "SECTION_PROMPT_TEMPLATE",
    "DEFAULT_CHORD_ENGINE",
    "DEFAULT_STRUCTURE_ENGINE",
    "analyze_song",
    "analyze_song_workflow",
    "parse_analysis_json",
    "parse_lyric_asr_text",
    "parse_model_json",
    "render_chord_sheet",
]
