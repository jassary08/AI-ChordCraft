# Contributing to AI-ChordCraft

Thanks for your interest. AI-ChordCraft is an orchestration layer over
open-source music models — most contributions are about making that
orchestration more robust, not about training models.

## Before you start

The full demo needs a GPU and several external runtimes (LLM service,
SongFormer, chord-recognition runtime). If you don't have a GPU, the
**CPU-only** parts live in the companion project
[AI-Musician-Skills](https://github.com/jassary08/AI-Musician-Skills) — that is
usually the easier place to contribute.

## Ways to contribute

- **Improve the orchestration**: section alignment, chord-sheet rendering,
  prompt strategy for the music LLM, error handling around external services.
- **Frontend**: the UI is vanilla JS (no build step) in `frontend/`.
- **Report issues**: include the analysis mode (`core`/`full`), the engines
  used, and what went wrong. Do not attach copyrighted audio.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your service URLs
python app.py          # serves http://127.0.0.1:7862
```

External runtimes are configured through `.env`; see the README Quickstart.

## Conventions

- Configuration is read from `CHORDCRAFT_*` environment variables. (Legacy
  `MOSS_MUSIC_*` fallbacks still exist for backward compatibility and may be
  removed in a future cleanup — prefer `CHORDCRAFT_*` in new code.)
- Never commit `.env`, model weights, `third_party/` code, or copyrighted audio.
- Keep `third_party/` contents out of git (only its README is tracked).

## Licensing

Contributions are licensed under this repository's MIT License. Do not add code
or data whose license is incompatible, and never redistribute third-party
checkpoints or copyrighted songs.
