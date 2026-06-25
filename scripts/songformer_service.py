from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.structure_recognition import recognize_structure_with_songformer_local
from src._runtime import resolve_ffmpeg_exe


app = FastAPI(title="AI-ChordCraft SongFormer Service")


def _songformer_runtime_path(*parts: str) -> Path:
    root = Path(os.environ.get("CHORDCRAFT_SONGFORMER_ROOT", PROJECT_ROOT / "third_party" / "SongFormer"))
    return root / "src" / "SongFormer" / Path(*parts)


def _assert_runtime_files() -> None:
    required = [
        _songformer_runtime_path("configs", os.environ.get("SONGFORMER_CONFIG", "SongFormer.yaml")),
        _songformer_runtime_path("ckpts", os.environ.get("SONGFORMER_CHECKPOINT", "SongFormer.safetensors")),
        _songformer_runtime_path("ckpts", "MuQ", "config.json"),
        _songformer_runtime_path("ckpts", "MuQ", "model.safetensors"),
        _songformer_runtime_path("ckpts", "MusicFM", "config.json"),
        _songformer_runtime_path("ckpts", "MusicFM", "msd_stats.json"),
        _songformer_runtime_path("ckpts", "MusicFM", "pretrained_msd.pt"),
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(
            "SongFormer runtime files are missing. Run scripts/prepare_third_party.sh "
            "or third_party/SongFormer/src/SongFormer/utils/fetch_pretrained.py first. "
            f"Missing: {missing}"
        )


def _segment_payload(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "start": section.get("start_seconds") if section.get("start_seconds") is not None else section.get("start"),
        "end": section.get("end_seconds") if section.get("end_seconds") is not None else section.get("end"),
        "label": section.get("source_label") or section.get("name"),
    }


def _transcode_to_songformer_wav(input_path: Path, output_path: Path) -> None:
    command = [
        resolve_ffmpeg_exe(),
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "24000",
        "-f",
        "wav",
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to prepare audio for SongFormer.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Failed to transcode uploaded audio for SongFormer: {exc.stderr}") from exc


@app.post("/api/songformer/segment")
async def segment_songformer(file: UploadFile = File(...)) -> dict[str, Any]:
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(await file.read())
    wav_path = temp_path.with_suffix(".songformer.wav")

    try:
        _transcode_to_songformer_wav(temp_path, wav_path)
        sections, runtime = recognize_structure_with_songformer_local(str(wav_path), max_sections=128)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)

    return {
        "segments": [_segment_payload(section) for section in sections],
        "normalized_sections": sections,
        "runtime": {
            "mode": runtime.get("mode"),
            "root": runtime.get("root"),
            "module": runtime.get("module"),
        },
    }


if __name__ == "__main__":
    _assert_runtime_files()
    host = os.environ.get("CHORDCRAFT_SONGFORMER_HOST", "127.0.0.1")
    port = int(os.environ.get("CHORDCRAFT_SONGFORMER_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
