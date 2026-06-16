"""Arrangement generation workflow built on top of chord-sheet analysis."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from src._runtime import (
    format_timestamp,
    generate_text,
    json_for_prompt,
    parse_model_json,
    parse_timestamp_float,
    slice_audio,
)


ARRANGEMENT_GLOBAL_PROMPT_TEMPLATE = """你是一名专业编曲总监。请基于音频与已有分析结果，为后续分段编配制定整体方向。

只输出 JSON 对象，不要输出 Markdown、解释文字、代码块或 <think> 内容。自然语言字段使用中文。

用户需求：
{user_request}

歌曲上下文：
{song_context}

JSON 必须符合以下字段：
{
  "global_style": "string",
  "tempo_feel": "string",
  "harmony_language": "string",
  "instrumentation_strategy": "string",
  "dynamic_arc": "string",
  "section_priorities": [
    {
      "section": "string",
      "arrangement_goal": "string"
    }
  ]
}
"""


ARRANGEMENT_SECTION_PROMPT_TEMPLATE = """你是一名专业编曲师。请基于这一大段落的原始音频、给定和弦走向和用户乐器需求，生成可执行的分段编配方案。

重要约束：
1. 给定和弦是事实约束，不要改写根音和主干进行。
2. 可以在 voicing、张力音、转位、节奏型中建议更丰富的色彩，但要标明是编配建议。
3. 输出要面向真实演奏/制作，不要泛泛而谈。
4. 只输出 JSON 对象，不要输出 Markdown、解释文字、代码块或 <think> 内容。
5. 自然语言字段使用中文。

用户需求：
{user_request}

全局编配方向：
{global_direction}

歌曲上下文：
{song_context}

当前段落上下文：
{section_context}

JSON 必须符合以下字段：
{
  "section": "string",
  "arrangement_summary": "string",
  "groove": "string",
  "harmony_treatment": "string",
  "instruments": [
    {
      "instrument": "string",
      "role": "string",
      "chord_voicing": "string",
      "rhythm_pattern": "string",
      "style_notes": "string",
      "difficulty": "string"
    }
  ],
  "production_notes": ["string"]
}
"""


DEFAULT_INSTRUMENTS = ["guitar", "piano", "bass"]


def _clean_string_list(values: Any, fallback: list[str]) -> list[str]:
    if isinstance(values, str):
        values = [part.strip() for part in values.split(",")]
    if not isinstance(values, list):
        return fallback
    cleaned = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned or fallback


def _section_seconds(section: dict[str, Any]) -> tuple[float | None, float | None]:
    start = section.get("start_seconds")
    end = section.get("end_seconds")
    if not isinstance(start, (int, float)):
        start = parse_timestamp_float(section.get("start"))
    if not isinstance(end, (int, float)):
        end = parse_timestamp_float(section.get("end"))
    return (
        float(start) if isinstance(start, (int, float)) else None,
        float(end) if isinstance(end, (int, float)) else None,
    )


def _normalize_arrangement_chords(section: dict[str, Any]) -> list[dict[str, Any]]:
    source_chords = section.get("chords")
    if not isinstance(source_chords, list) or not source_chords:
        for child in section.get("child_sections") or []:
            if isinstance(child, dict) and isinstance(child.get("chords"), list):
                source_chords = [*(source_chords or []), *child["chords"]]

    chords: list[dict[str, Any]] = []
    for item in source_chords or []:
        if not isinstance(item, dict):
            continue
        arrangement_chord = str(
            item.get("arrangement_chord")
            or item.get("raw_chord")
            or item.get("display_chord")
            or item.get("chord")
            or ""
        ).strip()
        if not arrangement_chord:
            continue
        chords.append(
            {
                "time": item.get("time"),
                "end": item.get("end"),
                "display_chord": item.get("display_chord") or item.get("chord"),
                "arrangement_chord": arrangement_chord,
                "raw_chord": item.get("raw_chord"),
            }
        )
    return chords


def _extract_sections_for_arrangement(analysis: dict[str, Any], max_sections: int | None = None) -> list[dict[str, Any]]:
    sections = analysis.get("sections") if isinstance(analysis, dict) else []
    if not isinstance(sections, list):
        return []
    prepared: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        start_seconds, end_seconds = _section_seconds(section)
        if start_seconds is None or end_seconds is None or end_seconds <= start_seconds:
            continue
        chords = _normalize_arrangement_chords(section)
        prepared.append(
            {
                "name": section.get("name") or "段落",
                "section_type": section.get("section_type"),
                "start": section.get("start") or format_timestamp(start_seconds),
                "end": section.get("end") or format_timestamp(end_seconds),
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "duration_seconds": round(end_seconds - start_seconds, 3),
                "chords": chords,
            }
        )
    return prepared[:max_sections] if max_sections else prepared


def _coerce_json_object(raw: str, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = parse_model_json(raw)
    except Exception:
        return fallback
    return parsed if isinstance(parsed, dict) else fallback


def _song_context(analysis: dict[str, Any], sections: list[dict[str, Any]]) -> dict[str, Any]:
    overall = analysis.get("overall") if isinstance(analysis.get("overall"), dict) else {}
    return {
        "title_guess": analysis.get("title_guess"),
        "description": analysis.get("song_description"),
        "overall": {
            "key": overall.get("key"),
            "mode": overall.get("mode"),
            "tempo_bpm": overall.get("tempo_bpm"),
            "time_signature": overall.get("time_signature"),
        },
        "sections": [
            {
                "name": section.get("name"),
                "start": section.get("start"),
                "end": section.get("end"),
            }
            for section in sections
        ],
    }


def analyze_arrangement_workflow(
    audio_path: str,
    analysis: dict[str, Any],
    instruments: list[str] | str | None = None,
    style: str = "原曲风格",
    difficulty: str = "intermediate",
    density: str = "medium",
    purpose: str = "伴奏",
    backend: str = "sglang",
    base_url: str | None = None,
    api_key: str | None = None,
    model_name_or_path: str | None = None,
    device: str | None = None,
    max_new_tokens: int = 3072,
    temperature: float = 0.2,
    top_p: float = 0.9,
    top_k: int = 50,
    max_sections: int | None = None,
) -> tuple[dict[str, Any], str, float]:
    started_at = time.perf_counter()
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio file does not exist: {audio_path}")

    resolved_instruments = _clean_string_list(instruments, DEFAULT_INSTRUMENTS)
    user_request = {
        "instruments": resolved_instruments,
        "style": style or "原曲风格",
        "difficulty": difficulty or "intermediate",
        "density": density or "medium",
        "purpose": purpose or "伴奏",
    }
    sections = _extract_sections_for_arrangement(analysis, max_sections=max_sections)
    song_context = _song_context(analysis, sections)

    raw_steps: dict[str, Any] = {
        "user_request": user_request,
        "song_context": song_context,
        "sections": sections,
        "global_direction_raw": None,
        "section_arrangements": [],
        "warnings": [],
    }

    global_prompt = ARRANGEMENT_GLOBAL_PROMPT_TEMPLATE.replace(
        "{user_request}", json_for_prompt(user_request)
    ).replace("{song_context}", json_for_prompt(song_context))
    global_raw = generate_text(
        audio_path=audio_path,
        prompt=global_prompt,
        backend=backend,
        base_url=base_url,
        api_key=api_key,
        model_name_or_path=model_name_or_path,
        device=device,
        max_new_tokens=min(max(max_new_tokens, 1024), 2048),
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
    )
    global_direction = _coerce_json_object(
        global_raw,
        {
            "global_style": "",
            "tempo_feel": "",
            "harmony_language": "",
            "instrumentation_strategy": "",
            "dynamic_arc": "",
            "section_priorities": [],
        },
    )
    raw_steps["global_direction_raw"] = global_raw
    raw_steps["global_direction"] = global_direction

    arranged_sections: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="moss-arrangement-") as temp_dir:
        for index, section in enumerate(sections, start=1):
            section_audio_path = audio_path
            audio_mode = "full_audio_fallback"
            start = section.get("start_seconds")
            end = section.get("end_seconds")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
                try:
                    section_audio_path = str(Path(temp_dir) / f"arrange-section-{index:02d}.wav")
                    slice_audio(audio_path, section_audio_path, float(start), float(end))
                    audio_mode = "sliced"
                except Exception as exc:
                    section_audio_path = audio_path
                    raw_steps["warnings"].append(
                        f"{section.get('name') or f'第 {index} 段'} 音频切分失败，已使用整首音频作为上下文：{exc}"
                    )

            section_context = {
                "name": section.get("name"),
                "start": section.get("start"),
                "end": section.get("end"),
                "duration_seconds": section.get("duration_seconds"),
                "chord_progression": section.get("chords") or [],
            }
            section_prompt = ARRANGEMENT_SECTION_PROMPT_TEMPLATE.replace(
                "{user_request}", json_for_prompt(user_request)
            ).replace("{global_direction}", json_for_prompt(global_direction)).replace(
                "{song_context}", json_for_prompt(song_context)
            ).replace("{section_context}", json_for_prompt(section_context))

            raw = generate_text(
                audio_path=section_audio_path,
                prompt=section_prompt,
                backend=backend,
                base_url=base_url,
                api_key=api_key,
                model_name_or_path=model_name_or_path,
                device=device,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
            )
            fallback = {
                "section": section.get("name"),
                "arrangement_summary": "",
                "groove": "",
                "harmony_treatment": "",
                "instruments": [],
                "production_notes": [],
            }
            parsed = _coerce_json_object(raw, fallback)
            parsed.setdefault("section", section.get("name"))
            parsed["section_context"] = section_context
            arranged_sections.append(parsed)
            raw_steps["section_arrangements"].append(
                {
                    "section": section_context,
                    "audio": audio_mode,
                    "raw": raw,
                    "parsed": parsed,
                }
            )

    arrangement = {
        "user_request": user_request,
        "global_direction": global_direction,
        "sections": arranged_sections,
    }
    raw_text = json.dumps(raw_steps, ensure_ascii=False, indent=2)
    return arrangement, raw_text, time.perf_counter() - started_at


__all__ = [
    "ARRANGEMENT_GLOBAL_PROMPT_TEMPLATE",
    "ARRANGEMENT_SECTION_PROMPT_TEMPLATE",
    "analyze_arrangement_workflow",
]
