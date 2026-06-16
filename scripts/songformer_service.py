from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.structure_recognition import recognize_structure_with_songformer_local


app = FastAPI(title="AI-ChordCraft SongFormer Service")


def _segment_payload(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "start": section.get("start_seconds") if section.get("start_seconds") is not None else section.get("start"),
        "end": section.get("end_seconds") if section.get("end_seconds") is not None else section.get("end"),
        "label": section.get("source_label") or section.get("name"),
    }


@app.post("/api/songformer/segment")
async def segment_songformer(file: UploadFile = File(...)) -> dict[str, Any]:
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(await file.read())

    try:
        sections, runtime = recognize_structure_with_songformer_local(str(temp_path))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)

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
    host = os.environ.get("CHORDCRAFT_SONGFORMER_HOST", "127.0.0.1")
    port = int(os.environ.get("CHORDCRAFT_SONGFORMER_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
