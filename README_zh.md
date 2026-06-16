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

**AI-ChordCraft** 是一个 **LLM 赋能的自动扒谱工作台**。它接收一段音频或视频，自动抽取音频，并将歌曲结构划分、调性 / 速度估计、和弦识别、歌词 ASR 与大模型音乐理解整合到同一条流程中，最终在网页中生成可演奏、可检查、可继续追问的和弦谱。

<p align="center">
  <img src="./assets/ai-chordcraft-overview.png" width="96%" alt="AI-ChordCraft automatic chord transcription overview" />
</p>

### 新闻

- 2026.06: 发布 AI-ChordCraft，一个 LLM 赋能的自动扒谱与交互式和弦谱生成工作台。

### 目录

- [介绍](#介绍)
- [主要功能](#主要功能)
- [系统流程](#系统流程)
- [外部运行时](#外部运行时)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [更多信息](#更多信息)
- [许可证说明](#许可证说明)
- [引用](#引用)
- [English](./README.md)

### 介绍

AI-ChordCraft 的核心目标是用 LLM 增强传统自动扒谱流程：系统不只输出一串和弦，而是把结构、和声、歌词、调性、速度、段落边界和可播放时间轴组织成一份面向演奏和讨论的工作材料。用户可以从一首歌直接得到结构化和弦谱，检查每个段落，播放局部片段，选择段落继续向大模型追问，或把结果交给后续编配流程。

相比只做单点 MIR 识别的工具，AI-ChordCraft 更强调 **识别 + 解释 + 交互 + 编配** 的闭环：

- **识别**：从音频或视频中提取结构、和弦、调性、速度和歌词等核心扒谱信息。
- **解释**：用 MOSS-Music 对段落、和声走向、整体风格和可疑点进行自然语言解释。
- **交互**：用户可以选中任意段落，继续询问“这里为什么是这个和弦”“副歌能否改成更适合吉他的进行”等问题。
- **编配**：外部 `AI-Musician-Skills` 项目提供吉他指法选择、capo 建议、ChordPro 输出和和声图表能力。

当前自动扒谱流程整合了三类能力：

- **音乐结构分析**：调用 SongFormer 对完整歌曲进行段落切分，输出 intro / verse / chorus / bridge / outro 等结构标签和时间边界。
- **自动和弦识别**：默认使用基于伪标签与选择性知识蒸馏的 BTC 系列 ACR 方法，输出带时间戳的和弦事件，再映射到歌曲段落中。
- **LLM 音乐理解与推理**：通过 MOSS-Music 的 SGLang 服务补充歌词 ASR、整体音乐描述、段落级解释、音乐问答与编配相关推理，让扒谱结果从“识别结果”升级为可交流、可修改的音乐材料。

### 主要功能

- **浏览器上传音频 / 视频**：支持常见音频和视频格式，视频会先抽取音频再进入分析流程。
- **LLM 赋能自动扒谱**：从音频 / 视频自动生成包含结构、和弦、歌词、调性、速度和说明的可用谱面。
- **结构化和弦谱生成**：按歌曲段落展示和弦、时间点、歌词、调性、速度和整体信息。
- **时间轴播放与段落播放**：可以播放整首音频，也可以跳转播放单个段落，便于人工复核。
- **段落选择与音乐问答**：用户可以选择若干段落，继续询问和声走向、扒谱依据、编配、练习或改编问题。
- **双模式分析**：`core` 模式只运行结构和和弦核心流程；`full` 模式额外运行歌词 ASR 和整体音乐描述。
- **多端点任务路由**：支持把不同任务路由到 MOSS-Music Instruct / Thinking SGLang 服务。
- **吉他编配辅助**：包含和弦指法候选、常用度 / 风格标签标注、吉他和弦图渲染和编配 skill。
- **和声图表 skill**：可以从已有和弦或音频识别结果生成罗马数字、功能标签、终止式和小节网格。

### 系统流程

AI-ChordCraft 将传统音频分析模块与 LLM 推理模块串成一条面向扒谱的工作流：

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

默认 Web 工作流：

- `structure_engine=songformer`
- `chord_engine=plkd-btc`
- `analysis_mode=core`
- `backend=sglang`

`core` 模式会跳过歌词 ASR 和整体歌曲描述，适合快速生成和弦谱。`full` 模式需要 MOSS-Music Instruct 服务可用，适合需要歌词、整体描述和更丰富问答上下文的场景。

### 外部运行时

本仓库不包含以下组件：

- MOSS-Music 模型权重或 SGLang 服务代码。
- SongFormer 模型权重或结构分析服务。
- 伪标签 + 选择性知识蒸馏 ACR 模型权重或运行时。
- 受版权保护的示例音乐。

请将这些组件放在仓库外部，并通过环境变量接入。

### 快速开始

#### 环境配置

```bash
cd ChordCraft-Demo
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

安装并确认 `ffmpeg` 可用：

```bash
ffmpeg -version
```

如果 `ffmpeg` 不在系统路径中，可以在 `.env` 中设置：

```env
CHORDCRAFT_FFMPEG=/path/to/ffmpeg
CHORDCRAFT_FFPROBE=/path/to/ffprobe
```

#### MOSS-Music SGLang 服务

AI-ChordCraft 需要一个兼容 SGLang `/generate` 接口的音频语言模型服务：

```env
CHORDCRAFT_SGLANG_BASE_URL=http://127.0.0.1:30000
CHORDCRAFT_SGLANG_THINKING_BASE_URL=http://127.0.0.1:30000
CHORDCRAFT_SGLANG_INSTRUCT_BASE_URL=http://127.0.0.1:30001
```

如果本仓库与 `MOSS-Music` 文件夹并列放置，可以使用辅助脚本启动服务：

```bash
# 同时启动 Thinking 和 Instruct 模型，默认端口为 30000 / 30001。
bash scripts/start_moss_music_sglang.sh dual

# 或只启动 Instruct 模型。
bash scripts/start_moss_music_sglang.sh instruct
```

脚本会读取 `.env`，支持自定义模型路径、端口和 GPU：

```env
CHORDCRAFT_MOSS_THINKING_MODEL_PATH=../MOSS-Music/model/MOSS-Music-8B-Thinking
CHORDCRAFT_MOSS_INSTRUCT_MODEL_PATH=../MOSS-Music/model/MOSS-Music-8B-Instruct
CHORDCRAFT_MOSS_THINKING_CUDA_VISIBLE_DEVICES=0
CHORDCRAFT_MOSS_INSTRUCT_CUDA_VISIBLE_DEVICES=1
```

#### SongFormer 结构服务

结构切分默认使用 SongFormer。请单独启动 SongFormer 服务，并暴露地址：

```env
CHORDCRAFT_SONGFORMER_BASE_URL=http://127.0.0.1:8080
```

当前主流程不再在 SongFormer 不可用时回退到 LLM 结构 prompt。

#### 和弦识别运行时

和弦识别默认使用论文 **Enhancing Automatic Chord Recognition via Pseudo-Labeling and Knowledge Distillation** 中的伪标签 + 选择性知识蒸馏方法表述。请将本地运行时或模型目录设置为：

```env
CHORDCRAFT_ACR_MODEL_DIR=/path/to/pseudo-label-kd-acr-runtime
```

#### AI Musician Skills

吉他编配与和声图表 skill 作为独立项目发布，默认与本仓库并列放置：

```text
AI-musician/
  ChordCraft-Demo/
  AI-Musician-Skills/
```

如果 skill 项目放在其他位置，请设置：

```env
CHORDCRAFT_GUITAR_SKILL_DIR=/path/to/AI-Musician-Skills/guitar-arrange-skill
```

#### 启动 Web 应用

```bash
bash scripts/run_demo.sh
```

打开：

```text
http://127.0.0.1:7862
```

吉他指法标注工作台：

```text
http://127.0.0.1:7862/annotator
```

### 项目结构

```text
ChordCraft-Demo/
  app.py                  # FastAPI Web 服务
  frontend/               # 浏览器 UI
  src/
    song_analysis.py      # 主分析流程与和弦谱渲染
    chat_agent.py         # 后续音乐问答与 prompt 策略
    chord_recognition.py  # 伪标签/KD ACR、Essentia 风格辅助与后处理
    structure_recognition.py
    arrangement.py        # 编配 Agent 流程
  scripts/
    run_demo.sh
    start_moss_music_sglang.sh
  requirements.txt
```

独立 skill 项目建议与本仓库并列发布：

```text
AI-Musician-Skills/
  README.md
  guitar-arrange-skill/
  harmony-chart-skill/
```

### 更多信息

- **MOSS-Music**: [https://github.com/OpenMOSS/MOSS-Music](https://github.com/OpenMOSS/MOSS-Music)
- **MOSS-Audio**: [https://github.com/OpenMOSS/MOSS-Audio](https://github.com/OpenMOSS/MOSS-Audio)
- **MOSS-Music Data Pipeline**: [https://github.com/wx9songs/MOSS-Music-Data-Pipeline](https://github.com/wx9songs/MOSS-Music-Data-Pipeline)
- **Automatic Chord Recognition paper**: [https://arxiv.org/abs/2602.19778](https://arxiv.org/abs/2602.19778)
- **SongFormer paper**: [https://arxiv.org/abs/2510.02797](https://arxiv.org/abs/2510.02797)

### 许可证说明

发布或再分发前，请分别检查本仓库代码、外部模型权重、外部运行时、依赖库和示例音乐的许可证。不要随本仓库分发第三方 checkpoint 或受版权保护的音乐，除非其许可证明确允许。

### 引用

如果你在研究或应用中使用 AI-ChordCraft，请引用本项目以及实际使用到的上游模型和方法：

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
