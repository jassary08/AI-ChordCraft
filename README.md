<p align="center">
  <img src="./assets/ai-chordcraft-title.png" width="680" alt="AI-ChordCraft" />
</p>

<div align="center">

<img src="https://img.shields.io/badge/Task-Automatic_Chord_Transcription-red">
<img src="https://img.shields.io/badge/LLM-Compatible_API-blue">
<img src="https://img.shields.io/badge/Structure-SongFormer-purple">
<img src="https://img.shields.io/badge/Chord_ACR-Pseudo--Labeling_%2B_KD-orange">
<img src="https://img.shields.io/badge/App-FastAPI-009688">
<img src="https://img.shields.io/badge/Frontend-Vanilla_JS-f7df1e">

</div>

<p align="center">
  <a href="./README.md">English</a> | <a href="./README_zh.md">简体中文</a>
</p>

**AI-ChordCraft** is an **LLM-enhanced automatic chord-transcription and lead-sheet workspace** for musicians and music-understanding research. It accepts an audio or video upload, extracts the audio, combines song-structure segmentation, key / tempo estimation, chord recognition, lyrics ASR, and large-model music understanding, then renders an interactive chord sheet that can be played, inspected, selected by section, and used as context for follow-up music QA.

<p align="center">
  <img src="./assets/ai-chordcraft-overview.png" width="96%" alt="AI-ChordCraft automatic chord transcription overview" />
</p>

### 📰 News

- 🎉 2026.06: Released AI-ChordCraft, an LLM-enhanced workspace for automatic chord transcription and interactive chord-sheet generation.

### 📚 Contents

- [Introduction](#introduction)
- [Features](#features)
- [Workflow](#workflow)
- [External Runtimes](#external-runtimes)
- [Quickstart](#quickstart)
- [Project Layout](#project-layout)
- [More Information](#more-information)
- [License Notes](#license-notes)
- [Citation](#citation)

### 🎼 Introduction

AI-ChordCraft is designed to use LLMs to strengthen the traditional automatic transcription workflow. It produces more than a flat chord list: structure, harmony, lyrics, key, tempo, section boundaries, and playable timestamps are organized into a practical working document for performance, review, arrangement, and discussion. Users can inspect sections, play local audio regions, ask follow-up questions about transcription decisions, and pass the result to downstream arrangement tools.

Compared with single-purpose MIR recognition tools, AI-ChordCraft focuses on a full **recognition + explanation + interaction + arrangement** loop:

- 🎧 **Recognition**: extract structure, chords, key, tempo, and lyrics from audio or video.
- 🧠 **Explanation**: use an external LLM to describe sections, harmonic motion, overall style, and uncertain points in natural language.
- 💬 **Interaction**: select any section and ask why a chord appears, how a chorus can be reharmonized, or how to adapt it for guitar.

The current automatic transcription pipeline integrates three groups of capabilities:

- 🧱 **Music structure analysis**: SongFormer segments a full track into section labels such as intro, verse, chorus, bridge, and outro with time boundaries.
- 🎹 **Automatic chord recognition**: a high-accuracy chord-recognition model outputs timestamped chord events, which are aligned to song sections.
- 🧠 **LLM music understanding and reasoning**: an external compatible LLM service adds lyrics ASR, full-track description, section-level explanation, music QA, and arrangement-oriented reasoning, turning raw recognition outputs into editable musical material. MOSS-Music is recommended, but other compatible services can also be used.

### ✨ Features

- 📤 **Browser upload for audio and video**: common audio and video formats are supported; video files are converted to audio before analysis.
- ✨ **LLM-enhanced automatic transcription**: audio / video is converted into a usable lead sheet with structure, chords, lyrics, key, tempo, and explanatory context.
- 🎼 **Section-aware chord-sheet generation**: sections display chords, timestamps, lyrics, key, tempo, and overall metadata.
- ▶️ **Timeline and section playback**: users can play the full track or jump to individual sections for manual review.
- 💬 **Section selection and music QA**: selected sections can be used as context for questions about harmony, transcription rationale, arrangement, practice, and adaptation.
- ⚙️ **Two analysis modes**: `core` runs the structure and chord pipeline; `full` also runs lyrics ASR and overall song description.

### 🔄 Workflow

AI-ChordCraft connects audio-analysis modules and LLM reasoning modules into a transcription-oriented workflow:

```text
Audio / Video Upload
        |
        v
Audio Extraction and Normalization
        |
        +--> SongFormer Structure Segmentation
        |
        +--> Pseudo-Labeling + KD ACR Chord Recognition
        |
        +--> External LLM Lyrics ASR and Song Description (full mode)
        |
        v
Section Alignment and Chord-Sheet Rendering
        |
        v
Interactive Browser UI + Music QA + Arrangement Tools
```

Default web workflow:

- `structure_engine=songformer`
- `chord_engine=plkd-btc`
- `analysis_mode=core`
- `backend=sglang`

`core` mode skips lyrics ASR and the overall song description for faster chord-sheet generation. `full` mode requires an external LLM service and is intended for lyrics, full-track description, and richer chat context.

### 🧩 External Runtimes

This repository does not include:

- External LLM / audio-language-model service and model weights.
- SongFormer model weights or structure-analysis service.
- Automatic chord-recognition model weights or runtime.

Keep these components outside this repository and connect them through environment variables.

### 🚀 Quickstart

#### ⚙️ Environment Setup

```bash
cd AI-ChordCraft
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

#### 🧠 LLM Inference Service

AI-ChordCraft usually connects to an existing LLM service by `base_url`, `api_key`, and `model_name`. MOSS-Music is recommended for music understanding, but any compatible service exposing `/generate` can be used:

```env
CHORDCRAFT_SGLANG_BASE_URL=http://127.0.0.1:30000
CHORDCRAFT_SGLANG_THINKING_BASE_URL=http://127.0.0.1:30000
CHORDCRAFT_SGLANG_INSTRUCT_BASE_URL=http://127.0.0.1:30001
CHORDCRAFT_SGLANG_API_KEY=your-api-key
CHORDCRAFT_SGLANG_MODEL_NAME=your-model-name
```

`CHORDCRAFT_SGLANG_BASE_URL` is the default endpoint. `THINKING` and `INSTRUCT` can point to separate reasoning and instruction-following services; if you deploy only one model, set all three URLs to the same address. `CHORDCRAFT_SGLANG_API_KEY` is sent as a Bearer token and can be left empty for local services without authentication. `CHORDCRAFT_SGLANG_MODEL_NAME` is sent as the request `model` field for OpenAI-compatible or router-style services; leave it empty if your local `/generate` endpoint already binds to a fixed model.

#### 🧱 SongFormer Structure Service

Song structure segmentation uses SongFormer by default. Start SongFormer separately and configure the service address in `.env`:

```env
CHORDCRAFT_SONGFORMER_BASE_URL=http://127.0.0.1:8080
CHORDCRAFT_SONGFORMER_TIMEOUT=900
```

AI-ChordCraft uploads the audio file to:

```text
POST ${CHORDCRAFT_SONGFORMER_BASE_URL}/api/songformer/segment
```

The service should return JSON containing `segments`, `data.segments`, or `rawSegments`. Each segment should include a start time, end time, and section label; AI-ChordCraft normalizes common labels such as intro, verse, chorus, bridge, interlude, solo, and outro. If the service runs on another host or port, only replace `CHORDCRAFT_SONGFORMER_BASE_URL`. Increase `CHORDCRAFT_SONGFORMER_TIMEOUT` for long audio or slower GPU queues.

You can also use a local SongFormer runtime by setting `structure_engine=songformer-local` and pointing AI-ChordCraft to the SongFormer root and model files:

```env
CHORDCRAFT_SONGFORMER_ROOT=/path/to/SongFormer
SONGFORMER_MODEL_NAME=SongFormer
SONGFORMER_CHECKPOINT=SongFormer.safetensors
SONGFORMER_CONFIG=SongFormer.yaml
```

#### 🎹 Chord-Recognition Runtime

Chord recognition requires a local automatic chord-recognition runtime. By default, this project connects to the method implementation associated with **Enhancing Automatic Chord Recognition via Pseudo-Labeling and Knowledge Distillation**. Point the runtime/model directory to:

```env
CHORDCRAFT_ACR_MODEL_DIR=/path/to/pseudo-label-kd-acr-runtime
```

#### 🌐 Run the Web App

```bash
bash scripts/run_demo.sh
```

Open:

```text
http://127.0.0.1:7862
```

### 🗂️ Project Layout

```text
AI-ChordCraft/
  app.py                  # FastAPI web server
  frontend/               # Browser UI
  src/
    song_analysis.py      # Main analysis workflow and chord-sheet rendering
    chat_agent.py         # Follow-up music QA and prompt strategy
    chord_recognition.py  # Automatic chord recognition, Essentia-style helpers, postprocessing
    structure_recognition.py
    arrangement.py        # Arrangement-agent workflow
  scripts/
    run_demo.sh
  requirements.txt
```

### 🔗 More Information

- **MOSS-Music**: [https://github.com/OpenMOSS/MOSS-Music](https://github.com/OpenMOSS/MOSS-Music)
- **Automatic Chord Recognition paper**: [https://arxiv.org/abs/2602.19778](https://arxiv.org/abs/2602.19778)
- **SongFormer paper**: [https://arxiv.org/abs/2510.02797](https://arxiv.org/abs/2510.02797)

### 📄 License Notes

Before public release or redistribution, check the licenses of this repository, external model weights, external runtimes, dependencies, and example music separately. Do not redistribute third-party checkpoints or copyrighted songs unless their licenses explicitly allow it.

### 📝 Citation

If you use AI-ChordCraft in your research or application, please cite this project and the upstream models or methods you use:

```bibtex
@misc{aichordcraft2026,
      title={AI-ChordCraft: An LLM-Enhanced Workspace for Automatic Chord Transcription and Music QA},
      author={AI-ChordCraft Contributors},
      year={2026},
      howpublished={GitHub repository},
      note={Web application}
}
```

```bibtex
@misc{mossmusic2026,
      title={MOSS-Music Technical Report},
      author={OpenMOSS Team},
      year={2026},
      howpublished={\url{https://github.com/OpenMOSS/MOSS-Music}},
      note={GitHub repository}
}
```

```bibtex
@misc{hao2026songformerscalingmusicstructure,
      title={SongFormer: Scaling Music Structure Analysis with Heterogeneous Supervision},
      author={Chunbo Hao and Ruibin Yuan and Jixun Yao and Qixin Deng and Xinyi Bai and Yanbo Wang and Wei Xue and Lei Xie},
      year={2026},
      eprint={2510.02797},
      archivePrefix={arXiv},
      primaryClass={eess.AS},
      url={https://arxiv.org/abs/2510.02797},
}
```

```bibtex
@misc{phan2026enhancingautomaticchordrecognition,
      title={Enhancing Automatic Chord Recognition via Pseudo-Labeling and Knowledge Distillation},
      author={Nghia Phan and Rong Jin and Gang Liu and Xiao Dong},
      year={2026},
      eprint={2602.19778},
      archivePrefix={arXiv},
      primaryClass={cs.SD},
      url={https://arxiv.org/abs/2602.19778},
}
```
