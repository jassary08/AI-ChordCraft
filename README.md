# AI-ChordCraft

<h1 align="center">
  <img src="./assets/ai-chordcraft-logo.png" width="44" alt="AI-ChordCraft logo" />
  AI-ChordCraft
</h1>

<div align="center">

<img src="https://img.shields.io/badge/Task-Automatic_Chord_Transcription-red">
<img src="https://img.shields.io/badge/LLM-MOSS--Music-blue">
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

This repository focuses on the application layer: the FastAPI server, browser UI, workflow orchestration, model adapters, guitar-arrangement helpers, and visualization tools. Large model weights and external inference services are not distributed with this repository; they should be deployed separately and connected through environment variables.

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
- 🧠 **Explanation**: use MOSS-Music to describe sections, harmonic motion, overall style, and uncertain points in natural language.
- 💬 **Interaction**: select any section and ask why a chord appears, how a chorus can be reharmonized, or how to adapt it for guitar.
- 🎸 **Arrangement**: the standalone `AI-Musician-Skills` project provides guitar voicing selection, capo suggestions, ChordPro export, and harmony charts.

The current automatic transcription pipeline integrates three groups of capabilities:

- 🧱 **Music structure analysis**: SongFormer segments a full track into section labels such as intro, verse, chorus, bridge, and outro with time boundaries.
- 🎹 **Automatic chord recognition**: a high-accuracy chord-recognition model outputs timestamped chord events, which are aligned to song sections.
- 🧠 **LLM music understanding and reasoning**: MOSS-Music served through SGLang adds lyrics ASR, full-track description, section-level explanation, music QA, and arrangement-oriented reasoning, turning raw recognition outputs into editable musical material.

### ✨ Features

- 📤 **Browser upload for audio and video**: common audio and video formats are supported; video files are converted to audio before analysis.
- ✨ **LLM-enhanced automatic transcription**: audio / video is converted into a usable lead sheet with structure, chords, lyrics, key, tempo, and explanatory context.
- 🎼 **Section-aware chord-sheet generation**: sections display chords, timestamps, lyrics, key, tempo, and overall metadata.
- ▶️ **Timeline and section playback**: users can play the full track or jump to individual sections for manual review.
- 💬 **Section selection and music QA**: selected sections can be used as context for questions about harmony, transcription rationale, arrangement, practice, and adaptation.
- ⚙️ **Two analysis modes**: `core` runs the structure and chord pipeline; `full` also runs lyrics ASR and overall song description.
- 🔀 **Task routing across endpoints**: different tasks can be routed to MOSS-Music Instruct / Thinking SGLang endpoints.
- 🎸 **Guitar arrangement support**: chord voicing candidates, commonness / style annotation, chord-diagram rendering, and guitar-arrangement skills are included.
- 📊 **Harmony chart skill**: existing chords or audio-derived chords can be converted into Roman numerals, harmonic functions, cadence notes, and measure grids.

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
        +--> MOSS-Music Lyrics ASR and Song Description (full mode)
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

`core` mode skips lyrics ASR and the overall song description for faster chord-sheet generation. `full` mode requires the MOSS-Music Instruct service and is intended for lyrics, full-track description, and richer chat context.

### 🧩 External Runtimes

This repository does not include:

- MOSS-Music model weights or SGLang server code.
- SongFormer model weights or structure-analysis service.
- Automatic chord-recognition model weights or runtime.
- Copyrighted example songs.

Keep these components outside this repository and connect them through environment variables.

### 🚀 Quickstart

#### ⚙️ Environment Setup

```bash
cd ChordCraft-Demo
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Install and verify `ffmpeg`:

```bash
ffmpeg -version
```

If `ffmpeg` is not on your system path, configure it in `.env`:

```env
CHORDCRAFT_FFMPEG=/path/to/ffmpeg
CHORDCRAFT_FFPROBE=/path/to/ffprobe
```

#### 🧠 MOSS-Music SGLang Serving

AI-ChordCraft needs an audio-language model service exposing an SGLang-compatible `/generate` endpoint:

```env
CHORDCRAFT_SGLANG_BASE_URL=http://127.0.0.1:30000
CHORDCRAFT_SGLANG_THINKING_BASE_URL=http://127.0.0.1:30000
CHORDCRAFT_SGLANG_INSTRUCT_BASE_URL=http://127.0.0.1:30001
```

If this repository is placed next to the original `MOSS-Music` folder, you can use the helper script:

```bash
# Start both Thinking and Instruct models on ports 30000 and 30001.
bash scripts/start_moss_music_sglang.sh dual

# Or start only the Instruct model.
bash scripts/start_moss_music_sglang.sh instruct
```

The script reads `.env` and supports custom model paths, ports, and GPU assignment:

```env
CHORDCRAFT_MOSS_THINKING_MODEL_PATH=../MOSS-Music/model/MOSS-Music-8B-Thinking
CHORDCRAFT_MOSS_INSTRUCT_MODEL_PATH=../MOSS-Music/model/MOSS-Music-8B-Instruct
CHORDCRAFT_MOSS_THINKING_CUDA_VISIBLE_DEVICES=0
CHORDCRAFT_MOSS_INSTRUCT_CUDA_VISIBLE_DEVICES=1
```

#### 🧱 SongFormer Structure Service

Song structure segmentation uses SongFormer by default. Start SongFormer separately and expose:

```env
CHORDCRAFT_SONGFORMER_BASE_URL=http://127.0.0.1:8080
```

The main workflow currently does not fall back to LLM-based structure prompts when SongFormer is unavailable.

#### 🎹 Chord-Recognition Runtime

Chord recognition requires a local automatic chord-recognition runtime. By default, this project connects to the method implementation associated with **Enhancing Automatic Chord Recognition via Pseudo-Labeling and Knowledge Distillation**. Point the runtime/model directory to:

```env
CHORDCRAFT_ACR_MODEL_DIR=/path/to/pseudo-label-kd-acr-runtime
```

#### 🎸 AI Musician Skills

The guitar-arrangement and harmony-chart skills are published as a standalone project. By default, AI-ChordCraft looks for the package next to this repository:

```text
AI-musician/
  ChordCraft-Demo/
  AI-Musician-Skills/
```

If the skill project lives elsewhere, set:

```env
CHORDCRAFT_GUITAR_SKILL_DIR=/path/to/AI-Musician-Skills/guitar-arrange-skill
```

#### 🌐 Run the Web App

```bash
bash scripts/run_demo.sh
```

Open:

```text
http://127.0.0.1:7862
```

Voicing annotation workspace:

```text
http://127.0.0.1:7862/annotator
```

### 🗂️ Project Layout

```text
ChordCraft-Demo/
  app.py                  # FastAPI web server
  frontend/               # Browser UI
  src/
    song_analysis.py      # Main analysis workflow and chord-sheet rendering
    chat_agent.py         # Follow-up music QA and prompt strategy
    chord_recognition.py  # Pseudo-labeling/KD ACR, Essentia-style helpers, postprocessing
    structure_recognition.py
    arrangement.py        # Arrangement-agent workflow
  scripts/
    run_demo.sh
    start_moss_music_sglang.sh
  requirements.txt
```

The standalone skill project should be released beside this repository:

```text
AI-Musician-Skills/
  README.md
  guitar-arrange-skill/
  harmony-chart-skill/
```

### 🔗 More Information

- **MOSS-Music**: [https://github.com/OpenMOSS/MOSS-Music](https://github.com/OpenMOSS/MOSS-Music)
- **MOSS-Audio**: [https://github.com/OpenMOSS/MOSS-Audio](https://github.com/OpenMOSS/MOSS-Audio)
- **MOSS-Music Data Pipeline**: [https://github.com/wx9songs/MOSS-Music-Data-Pipeline](https://github.com/wx9songs/MOSS-Music-Data-Pipeline)
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
