from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.arrangement import analyze_arrangement_workflow
from src.chat_agent import run_chat_agent
from src.song_analysis import (
    analyze_song,
    parse_timestamp_float,
    render_chord_sheet,
)


APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = APP_DIR / "frontend"
DEFAULT_AI_MUSICIAN_SKILLS_DIR = APP_DIR.parent / "AI-Musician-Skills"
GUITAR_SKILL_DIR = Path(
    os.environ.get(
        "CHORDCRAFT_GUITAR_SKILL_DIR",
        DEFAULT_AI_MUSICIAN_SKILLS_DIR / "guitar-arrange-skill",
    )
).expanduser()
VOICING_DB_DIR = GUITAR_SKILL_DIR / "resources" / "voicing_db"
VOICING_DB_PATH = VOICING_DB_DIR / "source" / "chords_db_voicings.json"
VOICING_ANNOTATIONS_PATH = VOICING_DB_DIR / "overlays" / "commonness_annotations.json"
TITLE = "AI-ChordCraft 和弦工作台"
DEFAULT_BACKEND = os.environ.get("CHORDCRAFT_BACKEND") or os.environ.get("MOSS_MUSIC_BACKEND", "sglang")
DEFAULT_SGLANG_BASE_URL = (
    os.environ.get("CHORDCRAFT_SGLANG_BASE_URL")
    or os.environ.get("MOSS_MUSIC_SGLANG_BASE_URL")
    or "http://127.0.0.1:30000"
)
DEFAULT_SGLANG_THINKING_BASE_URL = os.environ.get(
    "CHORDCRAFT_SGLANG_THINKING_BASE_URL",
    os.environ.get("MOSS_MUSIC_SGLANG_THINKING_BASE_URL", DEFAULT_SGLANG_BASE_URL),
)
DEFAULT_SGLANG_INSTRUCT_BASE_URL = os.environ.get(
    "CHORDCRAFT_SGLANG_INSTRUCT_BASE_URL",
    os.environ.get("MOSS_MUSIC_SGLANG_INSTRUCT_BASE_URL", DEFAULT_SGLANG_BASE_URL),
)
DEFAULT_SGLANG_MODEL_NAME = (
    os.environ.get("CHORDCRAFT_SGLANG_MODEL_NAME")
    or os.environ.get("MOSS_MUSIC_SGLANG_MODEL_NAME")
    or ""
)
SUPPORTED_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac"}
SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
SUPPORTED_MEDIA_SUFFIXES = SUPPORTED_AUDIO_SUFFIXES | SUPPORTED_VIDEO_SUFFIXES


class AnalyzeRequest(BaseModel):
    filename: str
    audio_base64: str
    backend: str = DEFAULT_BACKEND
    base_url: str | None = DEFAULT_SGLANG_BASE_URL
    thinking_base_url: str | None = DEFAULT_SGLANG_THINKING_BASE_URL
    instruct_base_url: str | None = DEFAULT_SGLANG_INSTRUCT_BASE_URL
    model_path: str | None = DEFAULT_SGLANG_MODEL_NAME
    max_new_tokens: int = 4096
    temperature: float = 0.0
    workflow: bool = True
    max_sections: int = 12
    chord_engine: str = "plkd-btc"
    structure_engine: str = "songformer"


class ArrangeRequest(BaseModel):
    filename: str
    audio_base64: str
    analysis: dict
    instruments: list[str] = Field(default_factory=lambda: ["guitar", "piano", "bass"])
    style: str = "原曲风格"
    difficulty: str = "intermediate"
    density: str = "medium"
    purpose: str = "伴奏"
    backend: str = DEFAULT_BACKEND
    base_url: str | None = DEFAULT_SGLANG_BASE_URL
    model_path: str | None = DEFAULT_SGLANG_MODEL_NAME
    max_new_tokens: int = 3072
    temperature: float = 0.2
    top_p: float = 0.9
    top_k: int = 50
    max_sections: int | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    filename: str
    audio_base64: str | None = None
    analysis: dict
    arrangement: dict | None = None
    selected_sections: list[dict] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)
    model_mode: str = "instruct"
    thinking_base_url: str | None = DEFAULT_SGLANG_THINKING_BASE_URL
    instruct_base_url: str | None = DEFAULT_SGLANG_BASE_URL
    max_new_tokens: int = 2048
    temperature: float = 0.2
    top_p: float = 0.9
    top_k: int = 50


class VoicingAnnotation(BaseModel):
    symbol: str
    shape: str
    frets: list[int]
    commonness: int = Field(ge=1, le=5)
    status: str = "usable"
    styles: list[str] = Field(default_factory=list)
    contexts: list[str] = Field(default_factory=list)
    canonical_rank: int | None = None
    notes: str = ""


class SaveVoicingAnnotationsRequest(BaseModel):
    progression: str = ""
    key: str = ""
    annotations: list[VoicingAnnotation] = Field(default_factory=list)


app = FastAPI(title=TITLE)
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")


@lru_cache(maxsize=1)
def resolve_ffmpeg_exe() -> str:
    env_path = (os.environ.get("CHORDCRAFT_FFMPEG") or os.environ.get("MOSS_MUSIC_FFMPEG", "")).strip()
    if env_path:
        return env_path

    path_binary = shutil.which("ffmpeg")
    if path_binary:
        return path_binary

    for root in APP_DIR.parents:
        miniconda_dir = root / "miniconda3"
        if not miniconda_dir.exists():
            continue
        candidates = list(miniconda_dir.glob("envs/*/bin/ffmpeg"))
        candidates.extend(sorted(miniconda_dir.glob("pkgs/ffmpeg-*/bin/ffmpeg"), reverse=True))
        for candidate in candidates:
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)

    return "ffmpeg"


def normalize_chord_symbol(symbol: Any) -> str:
    text = str(symbol or "").strip().replace("♯", "#").replace("♭", "b")
    if not text:
        return ""
    return text[0].upper() + text[1:]


def frets_to_shape(frets: Any) -> str:
    if not isinstance(frets, list):
        return ""
    return "".join("x" if not isinstance(fret, int) or fret < 0 else str(fret) for fret in frets[:6])


def annotation_key(symbol: str, frets: list[int] | tuple[int, ...]) -> str:
    return f"{normalize_chord_symbol(symbol)}|{','.join(str(fret) for fret in frets[:6])}"


def parse_progression(text: str) -> list[str]:
    parts = re.split(r"[\s,|>]+|(?:\s*-\s*)", text.strip())
    chords = []
    seen = set()
    for part in parts:
        chord = normalize_chord_symbol(part)
        if not chord or chord in seen:
            continue
        seen.add(chord)
        chords.append(chord)
    return chords


@lru_cache(maxsize=1)
def load_voicing_database() -> dict[str, list[dict[str, Any]]]:
    if not VOICING_DB_PATH.exists():
        return {}
    payload = json.loads(VOICING_DB_PATH.read_text(encoding="utf-8"))
    voicings = payload.get("voicings", []) if isinstance(payload, dict) else payload
    index: dict[str, list[dict[str, Any]]] = {}
    for item in voicings:
        if not isinstance(item, dict):
            continue
        symbol = normalize_chord_symbol(item.get("symbol"))
        frets = item.get("frets")
        if not symbol or not isinstance(frets, list) or len(frets) != 6:
            continue
        normalized = {
            "symbol": symbol,
            "shape": item.get("shape") or frets_to_shape(frets),
            "frets": frets,
            "fingers": item.get("fingers") or [],
            "position": item.get("position") or 1,
            "barres": item.get("barres") or [],
            "difficulty": item.get("difficulty"),
            "tags": item.get("tags") or [],
            "source_id": item.get("source_id"),
            "review_status": item.get("review_status"),
        }
        index.setdefault(symbol, []).append(normalized)
    return index


def load_voicing_annotations() -> dict[str, Any]:
    if not VOICING_ANNOTATIONS_PATH.exists():
        return {"version": "0.1.0", "annotations": {}}
    payload = json.loads(VOICING_ANNOTATIONS_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"version": "0.1.0", "annotations": {}}
    payload.setdefault("version", "0.1.0")
    payload.setdefault("annotations", {})
    return payload


def save_voicing_annotations(payload: dict[str, Any]) -> None:
    VOICING_ANNOTATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    VOICING_ANNOTATIONS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def candidate_sort_key(item: dict[str, Any], annotation: dict[str, Any] | None) -> tuple[float, float, float, float]:
    tags = set(item.get("tags") or [])
    commonness = float((annotation or {}).get("commonness") or 0)
    approved = 1.0 if (annotation or {}).get("status") == "preferred" else 0.0
    open_bonus = 1.0 if "open" in tags else 0.0
    common_tag = 1.0 if "common" in tags or "beginner" in tags else 0.0
    difficulty = float(item.get("difficulty") or 5)
    return (approved, commonness, open_bonus + common_tag, -difficulty)


def transcode_media_to_wav(input_path: str, output_path: str) -> None:
    command = [
        resolve_ffmpeg_exe(),
        "-y",
        "-i",
        input_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-f",
        "wav",
        output_path,
    ]
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="ffmpeg is required to prepare uploaded media.") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=400, detail=f"Failed to prepare uploaded media: {exc.stderr}") from exc


def decode_media_to_audio_tempfile(request: AnalyzeRequest, temp_dir: str) -> str:
    filename = Path(request.filename or "upload.wav").name
    suffix = Path(filename).suffix.lower() or ".wav"
    if suffix not in SUPPORTED_MEDIA_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported media file type.")

    raw = request.audio_base64
    if "," in raw:
        raw = raw.split(",", 1)[1]

    try:
        data = base64.b64decode(raw, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid media payload.") from exc
    if not data:
        raise HTTPException(status_code=400, detail="Media payload is empty.")

    input_path = Path(temp_dir) / f"input{suffix}"
    input_path.write_bytes(data)
    audio_path = str(Path(temp_dir) / "prepared.wav")
    transcode_media_to_wav(str(input_path), audio_path)
    return audio_path


def decode_chat_audio_to_tempfile(request: ChatRequest, temp_dir: str) -> str | None:
    if not request.audio_base64:
        return None
    media_request = AnalyzeRequest(
        filename=request.filename,
        audio_base64=request.audio_base64,
    )
    return decode_media_to_audio_tempfile(media_request, temp_dir)


def create_silent_chat_audio(temp_dir: str) -> str:
    audio_path = str(Path(temp_dir) / "silent.wav")
    command = [
        resolve_ffmpeg_exe(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=mono:sample_rate=16000",
        "-t",
        "0.25",
        audio_path,
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create chat audio placeholder: {exc}") from exc
    return audio_path


def selected_audio_bounds(sections: Any) -> list[tuple[float, float]]:
    if not isinstance(sections, list):
        return []
    bounds: list[tuple[float, float]] = []
    total_duration = 0.0
    for section in sections[:6]:
        if not isinstance(section, dict):
            continue
        start = parse_timestamp_float(section.get("start_seconds"))
        if start is None:
            start = parse_timestamp_float(section.get("start"))
        end = parse_timestamp_float(section.get("end_seconds"))
        if end is None:
            end = parse_timestamp_float(section.get("end"))
        if start is None or end is None or end <= start:
            continue

        duration = min(end - start, 90.0)
        if total_duration + duration > 180.0:
            duration = max(0.0, 180.0 - total_duration)
        if duration <= 0:
            break
        bounds.append((start, start + duration))
        total_duration += duration
    return bounds


def slice_selected_chat_audio(source_audio_path: str, sections: Any, temp_dir: str) -> str | None:
    bounds = selected_audio_bounds(sections)
    if not bounds:
        return None

    part_paths: list[Path] = []
    for index, (start, end) in enumerate(bounds, start=1):
        part_path = Path(temp_dir) / f"selected-{index:02d}.wav"
        command = [
            resolve_ffmpeg_exe(),
            "-y",
            "-ss",
            str(start),
            "-i",
            source_audio_path,
            "-t",
            str(max(0.2, end - start)),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(part_path),
        ]
        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        except subprocess.CalledProcessError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to slice selected chat audio: {exc.stderr}") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail="ffmpeg is required to slice selected chat audio.") from exc
        part_paths.append(part_path)

    if len(part_paths) == 1:
        return str(part_paths[0])

    concat_list = Path(temp_dir) / "selected-audio.txt"
    concat_list.write_text(
        "\n".join(f"file '{path.as_posix()}'" for path in part_paths),
        encoding="utf-8",
    )
    output_path = Path(temp_dir) / "selected-audio.wav"
    command = [
        resolve_ffmpeg_exe(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to concatenate selected chat audio: {exc.stderr}") from exc
    return str(output_path)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/annotator")
def annotator() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "annotator.html")


@app.get("/api/voicing-candidates")
def voicing_candidates(progression: str = "C Am F G", limit: int = 24) -> JSONResponse:
    chords = parse_progression(progression)
    voicing_index = load_voicing_database()
    annotation_payload = load_voicing_annotations()
    annotations = annotation_payload.get("annotations") or {}
    limit = max(1, min(int(limit or 24), 80))
    result: dict[str, list[dict[str, Any]]] = {}
    for chord in chords:
        candidates = []
        for item in voicing_index.get(chord, []):
            key = annotation_key(chord, item["frets"])
            annotation = annotations.get(key)
            candidates.append({**item, "annotation_key": key, "annotation": annotation})
        candidates.sort(key=lambda item: candidate_sort_key(item, item.get("annotation")), reverse=True)
        result[chord] = candidates[:limit]
    return JSONResponse(
        {
            "progression": progression,
            "chords": chords,
            "candidate_limit": limit,
            "candidates": result,
            "annotation_count": len(annotations),
            "annotation_file": str(VOICING_ANNOTATIONS_PATH.relative_to(APP_DIR)),
        }
    )


@app.get("/api/voicing-annotations")
def voicing_annotations() -> JSONResponse:
    return JSONResponse(load_voicing_annotations())


@app.post("/api/voicing-annotations")
def save_voicing_annotation_batch(request: SaveVoicingAnnotationsRequest) -> JSONResponse:
    payload = load_voicing_annotations()
    annotations = payload.setdefault("annotations", {})
    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    for item in request.annotations:
        symbol = normalize_chord_symbol(item.symbol)
        frets = item.frets[:6]
        if not symbol or len(frets) != 6:
            continue
        key = annotation_key(symbol, frets)
        previous = annotations.get(key) or {}
        annotations[key] = {
            **previous,
            "symbol": symbol,
            "shape": item.shape or frets_to_shape(frets),
            "frets": frets,
            "commonness": item.commonness,
            "status": item.status,
            "styles": item.styles,
            "contexts": item.contexts,
            "canonical_rank": item.canonical_rank,
            "notes": item.notes,
            "progression": request.progression,
            "key": request.key,
            "updated_at": now,
        }
        saved += 1
    payload["updated_at"] = now
    save_voicing_annotations(payload)
    return JSONResponse({"ok": True, "saved": saved, "annotation_count": len(annotations), "path": str(VOICING_ANNOTATIONS_PATH)})


@app.post("/api/analyze")
def analyze(request: AnalyzeRequest) -> JSONResponse:
    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="moss-web-chords-") as temp_dir:
        audio_path = decode_media_to_audio_tempfile(request, temp_dir)
        try:
            analysis, raw_text, elapsed_seconds = analyze_song(
                audio_path=audio_path,
                backend=request.backend,
                base_url=(request.base_url or "").strip() or None,
                thinking_base_url=(request.thinking_base_url or "").strip() or None,
                instruct_base_url=(request.instruct_base_url or "").strip() or None,
                model_name_or_path=(request.model_path or "").strip() or None,
                max_new_tokens=request.max_new_tokens,
                temperature=request.temperature,
                workflow=request.workflow,
                max_sections=request.max_sections,
                chord_engine=request.chord_engine,
                structure_engine=request.structure_engine,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    markdown = render_chord_sheet(analysis, source_name=request.filename)
    return JSONResponse(
        {
            "analysis": analysis,
            "markdown": markdown,
            "raw": raw_text,
            "elapsed_seconds": elapsed_seconds,
            "total_seconds": time.perf_counter() - started_at,
        }
    )


@app.post("/api/arrange")
def arrange(request: ArrangeRequest) -> JSONResponse:
    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="moss-web-arrange-") as temp_dir:
        audio_path = decode_media_to_audio_tempfile(request, temp_dir)
        try:
            arrangement, raw_text, elapsed_seconds = analyze_arrangement_workflow(
                audio_path=audio_path,
                analysis=request.analysis,
                instruments=request.instruments,
                style=request.style,
                difficulty=request.difficulty,
                density=request.density,
                purpose=request.purpose,
                backend=request.backend,
                base_url=(request.base_url or "").strip() or None,
                model_name_or_path=(request.model_path or "").strip() or None,
                max_new_tokens=request.max_new_tokens,
                temperature=request.temperature,
                top_p=request.top_p,
                top_k=request.top_k,
                max_sections=request.max_sections,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse(
        {
            "arrangement": arrangement,
            "raw": raw_text,
            "elapsed_seconds": elapsed_seconds,
            "total_seconds": time.perf_counter() - started_at,
        }
    )


@app.post("/api/chat")
def chat(request: ChatRequest) -> JSONResponse:
    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="moss-web-chat-") as temp_dir:
        source_audio_path = decode_chat_audio_to_tempfile(request, temp_dir)
        audio_path = (
            slice_selected_chat_audio(source_audio_path, request.selected_sections, temp_dir)
            if source_audio_path and request.selected_sections
            else None
        )
        if audio_path is None:
            audio_path = create_silent_chat_audio(temp_dir)
        try:
            result = run_chat_agent(
                audio_path=audio_path,
                filename=request.filename,
                analysis=request.analysis,
                arrangement=request.arrangement,
                selected_sections=request.selected_sections,
                messages=request.messages,
                model_mode=request.model_mode,
                base_url=request.instruct_base_url
                or request.thinking_base_url
                or DEFAULT_SGLANG_BASE_URL,
                max_new_tokens=request.max_new_tokens,
                temperature=request.temperature,
                top_p=request.top_p,
                top_k=request.top_k,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse(
        {
            "answer": result.answer,
            "raw": result.raw,
            "model_mode": result.model_mode,
            "elapsed_seconds": time.perf_counter() - started_at,
            "agent_elapsed_seconds": result.elapsed_seconds,
        }
    )


if __name__ == "__main__":
    host = os.environ.get("CHORDCRAFT_WEB_HOST") or os.environ.get("MOSS_MUSIC_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("CHORDCRAFT_WEB_PORT") or os.environ.get("MOSS_MUSIC_WEB_PORT", "7862"))
    uvicorn.run(app, host=host, port=port)
