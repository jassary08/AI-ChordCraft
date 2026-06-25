"""Song structure recognition engines."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import requests


DEFAULT_SONGFORMER_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_SONGFORMER_TIMEOUT = 900


class StructureRecognitionError(RuntimeError):
    """Raised when an automatic structure-recognition engine fails."""


_LOCAL_SONGFORMER_LOCK = threading.Lock()
_LOCAL_SONGFORMER_MODULE: Any = None
_LOCAL_SONGFORMER_INITIALIZED = False


def read_songformer_base_url() -> str:
    return (
        os.environ.get("CHORDCRAFT_SONGFORMER_BASE_URL")
        or os.environ.get("MOSS_MUSIC_SONGFORMER_BASE_URL")
        or os.environ.get("LOCAL_SONGFORMER_API_URL")
        or os.environ.get("SONGFORMER_API_URL")
        or DEFAULT_SONGFORMER_BASE_URL
    ).rstrip("/")


def read_songformer_timeout() -> int:
    return int(
        os.environ.get("CHORDCRAFT_SONGFORMER_TIMEOUT")
        or os.environ.get("MOSS_MUSIC_SONGFORMER_TIMEOUT")
        or str(DEFAULT_SONGFORMER_TIMEOUT)
    )


def _format_seconds(seconds: float | int | None) -> str | None:
    if not isinstance(seconds, (int, float)):
        return None
    bounded = max(0.0, float(seconds))
    minutes = int(bounded // 60)
    remaining = bounded - minutes * 60
    if abs(remaining - round(remaining)) < 0.01:
        return f"{minutes:02d}:{int(round(remaining)):02d}"
    return f"{minutes:02d}:{remaining:05.2f}"


def _coerce_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().lower()
    if not text:
        return None
    text = text.removesuffix("s").strip()
    if ":" not in text:
        try:
            return float(text)
        except ValueError:
            return None
    parts = text.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    return None


def _songformer_label_to_section_name(label: Any) -> str:
    text = str(label or "").strip().lower()
    text = text.replace("_", "-").replace(" ", "-")
    if not text or text in {"end", "no-label", "none", "silence"}:
        return "Other"

    exact = {
        "intro": "Intro",
        "opening": "Intro",
        "fadein": "Intro",
        "no-vocal-intro": "Intro",
        "verse": "Verse",
        "verse-slow": "Verse",
        "verse1a": "Verse",
        "versepart": "Verse",
        "slowverse": "Verse",
        "miniverse": "Verse",
        "instrumentalverse": "Verse",
        "verseinst": "Verse",
        "prechorus": "Pre-Chorus",
        "pre-chorus": "Pre-Chorus",
        "chorus": "Chorus",
        "chorushalf": "Chorus",
        "choruspart": "Chorus",
        "quietchorus": "Chorus",
        "altchorus": "Chorus",
        "intchorus": "Chorus",
        "instchorus": "Chorus",
        "chorusinst": "Chorus",
        "chorus-instrumental": "Chorus",
        "postchorus": "Chorus",
        "refrain": "Chorus",
        "bridge": "Bridge",
        "instbridge": "Bridge",
        "bre": "Bridge",
        "break": "Bridge",
        "breakdown": "Bridge",
        "inst": "Interlude",
        "instrumental": "Interlude",
        "interlude": "Interlude",
        "no-vocal-interlude": "Interlude",
        "transition": "Interlude",
        "transition2a": "Interlude",
        "mainriff": "Interlude",
        "gtrbreak": "Solo",
        "guitarsolo": "Solo",
        "solo": "Solo",
        "gtr": "Solo",
        "guitar": "Solo",
        "outro": "Outro",
        "outroa": "Outro",
        "bigoutro": "Outro",
        "ending": "Outro",
        "vocaloutro": "Outro",
        "no-vocal-outro": "Outro",
    }
    if text in exact:
        return exact[text]
    if "pre" in text and "chorus" in text:
        return "Pre-Chorus"
    if "chorus" in text:
        return "Chorus"
    if "verse" in text:
        return "Verse"
    if "intro" in text or "opening" in text:
        return "Intro"
    if "outro" in text or "ending" in text:
        return "Outro"
    if "bridge" in text or "break" in text:
        return "Bridge"
    if "solo" in text or "guitar" in text or "gtr" in text:
        return "Solo"
    if "inst" in text or "riff" in text or "transition" in text:
        return "Interlude"
    return "Other"


def normalize_songformer_segments(
    raw_segments: Any,
    *,
    duration_seconds: float | int | None = None,
    max_sections: int = 12,
) -> list[dict[str, Any]]:
    if not isinstance(raw_segments, list):
        return []

    sections: list[dict[str, Any]] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        start_seconds = _coerce_seconds(item.get("start"))
        end_seconds = _coerce_seconds(item.get("end"))
        if start_seconds is None:
            continue
        if end_seconds is None and isinstance(duration_seconds, (int, float)):
            end_seconds = float(duration_seconds)
        if end_seconds is None or end_seconds <= start_seconds:
            continue
        if isinstance(duration_seconds, (int, float)):
            start_seconds = min(max(0.0, start_seconds), float(duration_seconds))
            end_seconds = min(max(0.0, end_seconds), float(duration_seconds))
            if end_seconds <= start_seconds:
                continue
        label = item.get("label") or item.get("name") or item.get("section") or item.get("type")
        sections.append(
            {
                "name": _songformer_label_to_section_name(label),
                "start": _format_seconds(start_seconds),
                "end": _format_seconds(end_seconds),
                "start_seconds": round(start_seconds, 3),
                "end_seconds": round(end_seconds, 3),
                "source_label": label,
            }
        )

    sections.sort(key=lambda section: float(section.get("start_seconds") or 0.0))
    return sections[:max_sections]


def _extract_segments(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("segments"), list):
        return payload["segments"]
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("segments"), list):
        return data["segments"]
    if isinstance(payload.get("rawSegments"), list):
        return payload["rawSegments"]
    return []


def recognize_structure_with_songformer_service(
    audio_path: str,
    *,
    base_url: str | None = None,
    timeout: int | None = None,
    duration_seconds: float | int | None = None,
    max_sections: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    endpoint = f"{(base_url or read_songformer_base_url()).rstrip('/')}/api/songformer/segment"
    try:
        with open(audio_path, "rb") as audio_file:
            response = requests.post(
                endpoint,
                files={"file": (Path(audio_path).name, audio_file, "application/octet-stream")},
                timeout=timeout or read_songformer_timeout(),
            )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            preview = response.text.replace("\n", " ").strip()[:240]
            raise StructureRecognitionError(
                f"SongFormer service returned non-JSON response from {endpoint}: "
                f"HTTP {response.status_code}, content-type={content_type or 'unknown'}, body={preview}"
            )
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        raise StructureRecognitionError(f"SongFormer service request failed: {exc}") from exc

    raw_segments = _extract_segments(payload)
    sections = normalize_songformer_segments(
        raw_segments,
        duration_seconds=duration_seconds,
        max_sections=max_sections,
    )
    if not sections:
        raise StructureRecognitionError("SongFormer service returned no usable segments.")
    return sections, {
        "mode": "service",
        "endpoint": endpoint,
        "payload": payload,
        "raw_segments": raw_segments,
    }


def _default_songformer_root() -> Path:
    return Path(__file__).resolve().parents[1] / "third_party" / "SongFormer"


@contextmanager
def _temporary_cwd(path: Path):
    previous_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous_cwd)


def _load_local_songformer_module() -> Any:
    global _LOCAL_SONGFORMER_MODULE
    if _LOCAL_SONGFORMER_MODULE is not None:
        return _LOCAL_SONGFORMER_MODULE

    songformer_root = Path(
        os.environ.get("CHORDCRAFT_SONGFORMER_ROOT")
        or os.environ.get("MOSS_MUSIC_SONGFORMER_ROOT")
        or str(_default_songformer_root())
    ).resolve()
    app_path = songformer_root / "app.py"
    if not app_path.exists():
        raise StructureRecognitionError(f"Local SongFormer runtime not found: {app_path}")

    with _LOCAL_SONGFORMER_LOCK:
        if _LOCAL_SONGFORMER_MODULE is not None:
            return _LOCAL_SONGFORMER_MODULE
        if str(songformer_root) not in sys.path:
            sys.path.insert(0, str(songformer_root))
        spec = importlib.util.spec_from_file_location("moss_music_songformer_runtime", app_path)
        if spec is None or spec.loader is None:
            raise StructureRecognitionError(f"Unable to load local SongFormer runtime: {app_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["moss_music_songformer_runtime"] = module
        try:
            with _temporary_cwd(songformer_root):
                _patch_gradio_textbox_compat()
                spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001
            raise StructureRecognitionError(f"Failed to import local SongFormer runtime: {exc}") from exc
        _LOCAL_SONGFORMER_MODULE = module
        return module


def _patch_gradio_textbox_compat() -> None:
    """Allow importing SongFormer's Gradio app with older Gradio releases."""
    try:
        import inspect
        import gradio as gr  # type: ignore
    except Exception:
        return

    textbox = getattr(gr, "Textbox", None)
    if textbox is None:
        return
    try:
        signature = inspect.signature(textbox)
    except (TypeError, ValueError):
        return
    if "show_copy_button" in signature.parameters or getattr(textbox, "_chordcraft_patched", False):
        return
from gradio.events import Dependency

    class CompatibleTextbox(textbox):  # type: ignore[misc, valid-type]
        _chordcraft_patched = True

        def __init__(self, *args: Any, show_copy_button: bool | None = None, **kwargs: Any) -> None:
            _ = show_copy_button
            super().__init__(*args, **kwargs)

    gr.Textbox = CompatibleTextbox


def recognize_structure_with_songformer_local(
    audio_path: str,
    *,
    duration_seconds: float | int | None = None,
    max_sections: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    global _LOCAL_SONGFORMER_INITIALIZED
    try:
        module = _load_local_songformer_module()
        songformer_root = Path(getattr(module, "ROOT_DIR", _default_songformer_root()))
        with _LOCAL_SONGFORMER_LOCK:
            if not _LOCAL_SONGFORMER_INITIALIZED:
                with _temporary_cwd(songformer_root):
                    module.initialize_models(
                        os.environ.get("SONGFORMER_MODEL_NAME", "SongFormer"),
                        checkpoint=os.environ.get("SONGFORMER_CHECKPOINT", "SongFormer.safetensors"),
                        config_path=os.environ.get("SONGFORMER_CONFIG", "SongFormer.yaml"),
                    )
                _LOCAL_SONGFORMER_INITIALIZED = True
        with _temporary_cwd(songformer_root):
            if hasattr(module, "segment_audio_file"):
                result = module.segment_audio_file(audio_path)
            else:
                logits, msa_output = module.process_audio(audio_path)
                if hasattr(module, "rule_post_processing"):
                    msa_output = module.rule_post_processing(msa_output)
                result = {
                    "segments": module.format_as_segments(msa_output),
                    "msa_output": msa_output,
                    "logits": {
                        "function_shape": list(logits["function_logits"].shape),
                        "boundary_shape": list(logits["boundary_logits"].shape),
                    },
                }
    except Exception as exc:  # noqa: BLE001
        raise StructureRecognitionError(f"Local SongFormer inference failed: {exc}") from exc

    raw_segments = _extract_segments(result)
    sections = normalize_songformer_segments(
        raw_segments,
        duration_seconds=duration_seconds,
        max_sections=max_sections,
    )
    if not sections:
        raise StructureRecognitionError("Local SongFormer returned no usable segments.")
    return sections, {
        "mode": "local",
        "root": str(songformer_root),
        "payload": result,
        "raw_segments": raw_segments,
    }


def recognize_structure_with_songformer(
    audio_path: str,
    *,
    mode: str = "service",
    base_url: str | None = None,
    timeout: int | None = None,
    duration_seconds: float | int | None = None,
    max_sections: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved_mode = (mode or "service").strip().lower()
    if resolved_mode in {"service", "http", "api"}:
        return recognize_structure_with_songformer_service(
            audio_path,
            base_url=base_url,
            timeout=timeout,
            duration_seconds=duration_seconds,
            max_sections=max_sections,
        )
    if resolved_mode in {"local", "embedded"}:
        return recognize_structure_with_songformer_local(
            audio_path,
            duration_seconds=duration_seconds,
            max_sections=max_sections,
        )
    raise StructureRecognitionError(f"Unsupported SongFormer mode: {mode}")


def sections_to_outline_json(sections: list[dict[str, Any]]) -> str:
    payload = {
        "sections": [
            {
                "name": section.get("name"),
                "start": section.get("start"),
                "end": section.get("end"),
            }
            for section in sections
        ]
    }
    return json.dumps(payload, ensure_ascii=False)