"""AI-ChordCraft contextual music chat agent.

This module owns the prompt strategy and context compaction for follow-up
questions after a chord sheet has been generated.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from src._runtime import generate_with_sglang, strip_thinking_text


CHAT_SYSTEM_PROMPT = """你是 AI-ChordCraft 和弦谱与编配助手。请基于精简后的歌曲上下文回答用户问题。
回答要求：
1. 使用中文，直接回答用户问题。
2. 如果 selected_sections 不为空，优先围绕用户选中的片段分析；此时输入音频也是选中片段按顺序拼接后的音频。
3. 可以引用段落名、时间、和弦、整体听感与编配方案。
4. 如果用户要求修改/建议编配，可以给出可执行的乐器、节奏、和弦色彩和练习建议。
5. 不要输出 Markdown 表格，不要输出 JSON，不要输出 <think> 内容。
"""


@dataclass(slots=True)
class ChatAgentResult:
    answer: str
    raw: str
    prompt: str
    model_mode: str
    elapsed_seconds: float


def truncate_text(value: Any, limit: int = 360) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _message_to_dict(message: Any) -> dict[str, str] | None:
    role = getattr(message, "role", None)
    content = getattr(message, "content", None)
    if isinstance(message, dict):
        role = message.get("role")
        content = message.get("content")

    text = str(content or "").strip()
    if not text:
        return None
    return {
        "role": role if role in {"user", "assistant"} else "user",
        "content": truncate_text(text, 700),
    }


def validate_chat_messages(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []
    safe_messages = [item for item in (_message_to_dict(message) for message in messages) if item]
    if not safe_messages or safe_messages[-1]["role"] != "user":
        raise ValueError("Chat request must end with a user message.")
    return safe_messages[-6:]


def normalize_model_mode(model_mode: str | None) -> str:
    resolved = (model_mode or "instruct").strip().lower()
    if resolved in {"thinking", "instruct"}:
        return resolved
    return "instruct"


def compact_chords(chords: Any, limit: int = 16) -> list[dict[str, Any]]:
    if not isinstance(chords, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in chords:
        if not isinstance(item, dict):
            continue
        chord = str(item.get("chord") or item.get("arrangement_chord") or "").strip()
        if not chord:
            continue
        compact.append(
            {
                "time": item.get("time") or item.get("start"),
                "end": item.get("end"),
                "chord": chord,
            }
        )
        if len(compact) >= limit:
            break
    return compact


def compact_lyrics(lyrics: Any, limit: int = 6) -> list[dict[str, Any]]:
    if limit <= 0 or not isinstance(lyrics, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in lyrics:
        if not isinstance(item, dict):
            continue
        text = truncate_text(item.get("text"), 80)
        if not text:
            continue
        compact.append({"start": item.get("start"), "end": item.get("end"), "text": text})
        if len(compact) >= limit:
            break
    return compact


def compact_section(section: Any, chord_limit: int = 16, lyric_limit: int = 4) -> dict[str, Any] | None:
    if not isinstance(section, dict):
        return None
    item = {
        "name": section.get("name"),
        "parent_name": section.get("parent_name"),
        "start": section.get("start"),
        "end": section.get("end"),
        "chords": compact_chords(section.get("chords"), limit=chord_limit),
        "lyrics": compact_lyrics(section.get("lyrics"), limit=lyric_limit),
    }
    children = section.get("child_sections")
    if isinstance(children, list):
        compact_children = [compact_section(child, chord_limit=8, lyric_limit=0) for child in children[:4]]
        item["child_sections"] = [child for child in compact_children if child]
    return item


def strip_lyrics_keys(section: dict[str, Any]) -> dict[str, Any]:
    section.pop("lyrics", None)
    children = section.get("child_sections")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                strip_lyrics_keys(child)
    return section


def compact_chat_analysis(analysis: Any) -> dict[str, Any]:
    if not isinstance(analysis, dict):
        return {}
    sections = analysis.get("sections")
    compact_sections: list[dict[str, Any]] = []
    if isinstance(sections, list):
        for section in sections[:8]:
            compact = compact_section(section, chord_limit=12, lyric_limit=0)
            if compact:
                compact_sections.append(strip_lyrics_keys(compact))
    overall = analysis.get("overall") if isinstance(analysis.get("overall"), dict) else {}
    return {
        "title_guess": analysis.get("title_guess"),
        "overall": {
            "key": overall.get("key"),
            "mode": overall.get("mode"),
            "tempo_bpm": overall.get("tempo_bpm"),
        },
        "song_description": truncate_text(analysis.get("song_description"), 520),
        "sections_overview": compact_sections,
        "uncertain_points": [
            truncate_text(item, 120)
            for item in (analysis.get("uncertain_points") or [])[:3]
        ],
    }


def compact_selected_sections(sections: Any) -> list[dict[str, Any]]:
    if not isinstance(sections, list):
        return []
    compact: list[dict[str, Any]] = []
    for section in sections[:6]:
        item = compact_section(section, chord_limit=24, lyric_limit=0)
        if item:
            compact.append(strip_lyrics_keys(item))
    return compact


def build_chat_context(
    *,
    filename: str,
    analysis: dict[str, Any],
    selected_sections: list[dict[str, Any]],
    arrangement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = {
        "filename": filename,
        "selected_sections": compact_selected_sections(selected_sections),
        "analysis": compact_chat_analysis(analysis),
    }
    if arrangement:
        context["arrangement"] = {
            "summary": truncate_text(arrangement.get("arrangement_summary"), 360)
            if isinstance(arrangement, dict)
            else "",
        }
    return context


def build_chat_prompt(
    *,
    filename: str,
    analysis: dict[str, Any],
    selected_sections: list[dict[str, Any]],
    messages: list[Any],
    arrangement: dict[str, Any] | None = None,
) -> str:
    safe_messages = validate_chat_messages(messages)
    context = build_chat_context(
        filename=filename,
        analysis=analysis,
        selected_sections=selected_sections,
        arrangement=arrangement,
    )
    return (
        f"{CHAT_SYSTEM_PROMPT}\n"
        f"当前上下文：\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        f"对话历史：\n{json.dumps(safe_messages, ensure_ascii=False, indent=2)}\n\n"
        "请回答最后一条 user 消息。"
    )


def run_chat_agent(
    *,
    audio_path: str,
    filename: str,
    analysis: dict[str, Any],
    selected_sections: list[dict[str, Any]],
    messages: list[Any],
    model_mode: str = "instruct",
    base_url: str | None = None,
    thinking_base_url: str | None = None,
    instruct_base_url: str | None = None,
    arrangement: dict[str, Any] | None = None,
    max_new_tokens: int = 2048,
    temperature: float = 0.2,
    top_p: float = 0.9,
    top_k: int = 50,
) -> ChatAgentResult:
    started_at = time.perf_counter()
    resolved_mode = normalize_model_mode(model_mode)
    resolved_base_url = base_url or instruct_base_url or thinking_base_url
    prompt = build_chat_prompt(
        filename=filename,
        analysis=analysis,
        selected_sections=selected_sections,
        messages=messages,
        arrangement=arrangement,
    )
    raw_text = generate_with_sglang(
        audio_path=audio_path,
        prompt=prompt,
        base_url=resolved_base_url,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
    )
    answer = strip_thinking_text(raw_text).strip()
    return ChatAgentResult(
        answer=answer,
        raw=raw_text,
        prompt=prompt,
        model_mode=resolved_mode,
        elapsed_seconds=time.perf_counter() - started_at,
    )
