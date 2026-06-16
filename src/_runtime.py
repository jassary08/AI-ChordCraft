"""Private shared runtime helpers for AI-ChordCraft feature modules."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests


DEFAULT_SGLANG_BASE_URL = "http://127.0.0.1:30000"
DEFAULT_REQUEST_TIMEOUT = 600
DEFAULT_OVERALL_AUDIO_WINDOW_SECONDS = 90.0


class ModelJSONParseError(ValueError):
    """Raised when a model call does not return a parseable JSON object."""


def read_sglang_base_url() -> str:
    return (
        os.environ.get("CHORDCRAFT_SGLANG_BASE_URL")
        or os.environ.get("MOSS_MUSIC_SGLANG_BASE_URL")
        or DEFAULT_SGLANG_BASE_URL
    ).rstrip("/")


def read_request_timeout() -> int:
    return int(
        os.environ.get("CHORDCRAFT_REQUEST_TIMEOUT")
        or os.environ.get("MOSS_MUSIC_REQUEST_TIMEOUT")
        or str(DEFAULT_REQUEST_TIMEOUT)
    )


def read_sglang_model_name() -> str:
    return (
        os.environ.get("CHORDCRAFT_SGLANG_MODEL_NAME")
        or os.environ.get("MOSS_MUSIC_SGLANG_MODEL_NAME")
        or ""
    ).strip()


def build_sglang_headers(api_key: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = (
        api_key
        or os.environ.get("CHORDCRAFT_SGLANG_API_KEY")
        or os.environ.get("MOSS_MUSIC_SGLANG_API_KEY", "")
    ).strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def extract_response_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("text"), str):
        return payload["text"].strip()
    if isinstance(payload.get("generated_text"), str):
        return payload["generated_text"].strip()

    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict):
        first_choice = choices[0]
        if isinstance(first_choice.get("text"), str):
            return first_choice["text"].strip()

        message = first_choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if chunks:
                return "".join(chunks).strip()

    raise ValueError("The model response did not contain generated text.")


def generate_with_sglang(
    audio_path: str,
    prompt: str,
    base_url: str | None = None,
    api_key: str | None = None,
    model_name_or_path: str | None = None,
    max_new_tokens: int = 4096,
    temperature: float = 0.0,
    top_p: float = 1.0,
    top_k: int = 50,
) -> str:
    model_name = (model_name_or_path or read_sglang_model_name()).strip()
    payload = {
        "text": prompt,
        "audio_data": audio_path,
        "sampling_params": {
            "max_new_tokens": max_new_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
        },
    }
    if model_name:
        payload["model"] = model_name
    endpoint = f"{(base_url or read_sglang_base_url()).rstrip('/')}/generate"
    response = requests.post(
        endpoint,
        headers=build_sglang_headers(api_key),
        json=payload,
        timeout=read_request_timeout(),
    )
    response.raise_for_status()
    return extract_response_text(response.json())


def generate_text(
    audio_path: str,
    prompt: str,
    backend: str,
    base_url: str | None = None,
    api_key: str | None = None,
    model_name_or_path: str | None = None,
    device: str | None = None,
    max_new_tokens: int = 4096,
    temperature: float = 0.0,
    top_p: float = 1.0,
    top_k: int = 50,
) -> str:
    _ = device
    if backend == "sglang":
        return generate_with_sglang(
            audio_path=audio_path,
            prompt=prompt,
            base_url=base_url,
            api_key=api_key,
            model_name_or_path=model_name_or_path,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
        )
    raise ValueError("AI-ChordCraft currently supports the 'sglang' backend only.")


def strip_thinking_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"<think\b[^>]*>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    dangling = re.search(r"<think\b[^>]*>", cleaned, flags=re.IGNORECASE)
    if dangling:
        after = cleaned[dangling.end() :]
        json_fence = re.search(r"```(?:json)?\s*", after, flags=re.IGNORECASE)
        if json_fence:
            return after[json_fence.start() :].strip()

        markers = []
        for pattern in [
            r'\{\s*"lyrics_segments"\s*:',
            r'\{\s*"sections"\s*:',
            r'\{\s*"title_guess"\s*:',
            r'\{\s*"name"\s*:',
            r'\{\s*"key"\s*:',
            r'\[\s*\{\s*"time"\s*:',
        ]:
            matches = list(re.finditer(pattern, after))
            if matches:
                markers.append(matches[-1].start())
        if markers:
            return after[max(markers) :].strip()

    return cleaned


def model_json_candidates(text: str) -> list[tuple[int, Any]]:
    cleaned = strip_thinking_text(text)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()

    decoder = json.JSONDecoder()
    candidates: list[tuple[int, Any]] = []
    for index, char in enumerate(cleaned):
        if char not in "{[":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        score = _score_json_candidate(parsed)
        if score > 0:
            candidates.append((score, parsed))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates


def _score_json_candidate(parsed: Any) -> int:
    if isinstance(parsed, dict):
        score = 0
        if isinstance(parsed.get("sections"), list):
            score += 10
        if isinstance(parsed.get("lyrics_segments"), list):
            score += 9
        if isinstance(parsed.get("chords"), list):
            score += 8
        if isinstance(parsed.get("overall"), dict):
            score += 4
        if any(parsed.get(key) is not None for key in ["key", "mode", "tempo_bpm", "time_signature"]):
            score += 4
        if parsed.get("title_guess") is not None:
            score += 2
        if parsed.get("name") is not None:
            score += 2
        if parsed.get("summary") is not None:
            score += 1
        return score
    if not isinstance(parsed, list):
        return 0
    if not parsed:
        return 1
    if not all(isinstance(item, dict) for item in parsed):
        return 0
    score = 0
    if any(str(item.get("chord") or "").strip() for item in parsed):
        score += 10
    if all("time" in item and "chord" in item for item in parsed):
        score += 4
    if all("time" in item for item in parsed):
        score += 3
    if any("start" in item and "end" in item for item in parsed):
        score += 2
    return score


def parse_model_json(text: str) -> Any:
    candidates = model_json_candidates(text)
    if candidates:
        return candidates[0][1]
    raise ModelJSONParseError("Could not parse a JSON value from the model response.")


def parse_analysis_json(text: str) -> dict[str, Any]:
    cleaned = strip_thinking_text(text)
    candidates = [(score, parsed) for score, parsed in model_json_candidates(text) if isinstance(parsed, dict)]

    if candidates:
        if '"sections"' in cleaned:
            for _, candidate in candidates:
                if isinstance(candidate.get("sections"), list):
                    return candidate
            raise ModelJSONParseError("The model response contains sections but no complete top-level sections JSON.")
        if '"lyrics_segments"' in cleaned:
            for _, candidate in candidates:
                if isinstance(candidate.get("lyrics_segments"), list):
                    return candidate
            raise ModelJSONParseError(
                "The model response contains lyrics_segments but no complete top-level lyrics_segments JSON."
            )
        return candidates[0][1]

    raise ModelJSONParseError("Could not parse a JSON object from the model response.")


def model_output_preview(text: str, limit: int = 700) -> str:
    cleaned = re.sub(r"\s+", " ", strip_thinking_text(text)).strip()
    if len(cleaned) <= limit:
        return cleaned or "<empty>"
    return f"{cleaned[:limit]}..."


def parse_analysis_json_for_stage(text: str, stage: str) -> dict[str, Any]:
    try:
        return parse_analysis_json(text)
    except ModelJSONParseError as exc:
        preview = model_output_preview(text)
        raise ModelJSONParseError(
            f"{stage}没有返回可解析的 JSON。"
            f"这通常是模型输出了说明文字、Markdown、空响应，或 JSON 格式不合法。"
            f"模型输出片段：{preview}"
        ) from exc


def recover_sections_from_partial_json(text: str, max_sections: int) -> list[dict[str, Any]]:
    cleaned = strip_thinking_text(text)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()

    sections_key = re.search(r'"sections"\s*:\s*\[', cleaned)
    if not sections_key:
        return []

    decoder = json.JSONDecoder()
    body = cleaned[sections_key.end() :]
    sections: list[dict[str, Any]] = []
    index = 0
    while index < len(body) and len(sections) < max_sections:
        brace_index = body.find("{", index)
        if brace_index < 0:
            break
        try:
            parsed, offset = decoder.raw_decode(body[brace_index:])
        except json.JSONDecodeError:
            index = brace_index + 1
            continue
        if (
            isinstance(parsed, dict)
            and parsed.get("name") is not None
            and ("start" in parsed or "end" in parsed)
        ):
            sections.append(parsed)
        index = brace_index + max(offset, 1)
    return sections


def parse_structure_json_for_stage(text: str, stage: str, max_sections: int) -> dict[str, Any]:
    try:
        return parse_analysis_json_for_stage(text, stage)
    except ModelJSONParseError as exc:
        recovered_sections = recover_sections_from_partial_json(text, max_sections=max_sections)
        if recovered_sections:
            return {"sections": recovered_sections}
        raise exc


def parse_chord_array_for_stage(text: str, stage: str) -> list[dict[str, Any]]:
    try:
        parsed = parse_model_json(text)
    except ModelJSONParseError as exc:
        preview = model_output_preview(text)
        raise ModelJSONParseError(
            f"{stage}没有返回可解析的 JSON 数组。"
            f"这通常是模型输出了说明文字、Markdown、空响应，或 JSON 格式不合法。"
            f"模型输出片段：{preview}"
        ) from exc

    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict) and isinstance(parsed.get("chords"), list):
        return [item for item in parsed["chords"] if isinstance(item, dict)]

    preview = model_output_preview(text)
    raise ModelJSONParseError(f"{stage}返回的 JSON 不是和弦数组。模型输出片段：{preview}")


def parse_boundary_array_for_stage(text: str, stage: str) -> list[dict[str, Any]]:
    try:
        parsed = parse_model_json(text)
    except ModelJSONParseError as exc:
        preview = model_output_preview(text)
        raise ModelJSONParseError(
            f"{stage}没有返回可解析的 JSON 数组。"
            f"这通常是模型输出了说明文字、Markdown、空响应，或 JSON 格式不合法。"
            f"模型输出片段：{preview}"
        ) from exc

    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        for key in ["boundaries", "chord_boundaries", "changes"]:
            if isinstance(parsed.get(key), list):
                return [item for item in parsed[key] if isinstance(item, dict)]

    preview = model_output_preview(text)
    raise ModelJSONParseError(f"{stage}返回的 JSON 不是换点数组。模型输出片段：{preview}")


def json_for_prompt(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def parse_timestamp(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(round(float(value))))
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None

    match = re.match(r"^(\d{1,3}):(\d{2})(?:\.\d+)?$", text)
    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        return None if seconds >= 60 else minutes * 60 + seconds

    match = re.match(r"^(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)?$", text)
    if match:
        return max(0, int(round(float(match.group(1)))))

    match = re.match(
        r"^(?:(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes|分))\s*"
        r"(?:(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)?)?$",
        text,
    )
    if match:
        minutes = float(match.group(1) or 0)
        seconds = float(match.group(2) or 0)
        return max(0, int(round(minutes * 60 + seconds)))

    match = re.match(r"^(\d+)\s*分\s*(\d+(?:\.\d+)?)?\s*秒?$", text)
    if not match:
        return None
    return max(0, int(round(int(match.group(1)) * 60 + float(match.group(2) or 0))))


def parse_timestamp_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None

    match = re.match(r"^(\d{1,3}):(\d{2})(?:\.(\d+))?$", text)
    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        if seconds >= 60:
            return None
        fraction = float(f"0.{match.group(3)}") if match.group(3) else 0.0
        return minutes * 60 + seconds + fraction

    match = re.match(r"^(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)?$", text)
    if match:
        return max(0.0, float(match.group(1)))

    parsed = parse_timestamp(value)
    return float(parsed) if parsed is not None else None


def format_timestamp(total_seconds: int | float | None) -> str | None:
    if total_seconds is None:
        return None
    rounded = max(0, int(round(float(total_seconds))))
    return f"{rounded // 60:02d}:{rounded % 60:02d}"


def format_timestamp_precise(total_seconds: int | float | None) -> str | None:
    if total_seconds is None:
        return None
    seconds = max(0.0, float(total_seconds))
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes:02d}:{rest:05.2f}"


def normalize_timestamp(value: Any) -> str | None:
    return format_timestamp(parse_timestamp(value))


def normalize_timestamp_precise(value: Any) -> str | None:
    return format_timestamp_precise(parse_timestamp_float(value))


@lru_cache(maxsize=1)
def resolve_ffmpeg_exe() -> str:
    env_path = (os.environ.get("CHORDCRAFT_FFMPEG") or os.environ.get("MOSS_MUSIC_FFMPEG", "")).strip()
    if env_path:
        return env_path

    path_binary = shutil.which("ffmpeg")
    if path_binary:
        return path_binary

    for root in Path(__file__).resolve().parents:
        miniconda_dir = root / "miniconda3"
        if not miniconda_dir.exists():
            continue
        candidates = list(miniconda_dir.glob("envs/*/bin/ffmpeg"))
        candidates.extend(sorted(miniconda_dir.glob("pkgs/ffmpeg-*/bin/ffmpeg"), reverse=True))
        for candidate in candidates:
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)

    return "ffmpeg"


@lru_cache(maxsize=1)
def resolve_ffprobe_exe() -> str:
    env_path = (os.environ.get("CHORDCRAFT_FFPROBE") or os.environ.get("MOSS_MUSIC_FFPROBE", "")).strip()
    if env_path:
        return env_path

    ffmpeg_path = Path(resolve_ffmpeg_exe())
    if ffmpeg_path.name == "ffmpeg":
        sibling = ffmpeg_path.with_name("ffprobe")
        if sibling.exists() and os.access(sibling, os.X_OK):
            return str(sibling)

    path_binary = shutil.which("ffprobe")
    if path_binary:
        return path_binary

    return "ffprobe"


def slice_audio(audio_path: str, output_path: str, start_seconds: int | float, end_seconds: int | float) -> None:
    duration = max(1, end_seconds - start_seconds)
    command = [
        resolve_ffmpeg_exe(),
        "-y",
        "-ss",
        str(start_seconds),
        "-i",
        audio_path,
        "-t",
        str(duration),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        output_path,
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required for workflow section slicing.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Failed to slice section audio with ffmpeg.\n{exc.stderr}") from exc


def slice_audio_window(
    audio_path: str,
    output_path: str,
    duration_seconds: int | float | None,
    max_window_seconds: float = DEFAULT_OVERALL_AUDIO_WINDOW_SECONDS,
) -> tuple[str, dict[str, float | str]]:
    if isinstance(duration_seconds, (int, float)) and duration_seconds <= max_window_seconds:
        return audio_path, {"audio": "full", "start_seconds": 0.0, "end_seconds": float(duration_seconds)}

    window_end = (
        min(float(duration_seconds), float(max_window_seconds))
        if isinstance(duration_seconds, (int, float))
        else float(max_window_seconds)
    )
    slice_audio(audio_path, output_path, 0.0, window_end)
    return (
        output_path,
        {
            "audio": "sliced_window",
            "start_seconds": 0.0,
            "end_seconds": window_end,
            "reason": "avoid_llm_context_overflow",
        },
    )


def probe_audio_duration(audio_path: str) -> int | None:
    command = [
        resolve_ffprobe_exe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    try:
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    try:
        return max(1, int(float(result.stdout.strip())))
    except ValueError:
        return None
