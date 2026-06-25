<p align="center">
  <img src="./assets/ai-chordcraft-title.png" width="680" alt="AI-ChordCraft" />
</p>

<div align="center">

<img src="https://img.shields.io/badge/Task-Automatic_Chord_Transcription-red">
<img src="https://img.shields.io/badge/LLM-Compatible_API-blue">
<img src="https://img.shields.io/badge/Structure-SongFormer-purple">
<img src="https://img.shields.io/badge/App-FastAPI-009688">

</div>

<p align="center">
  <a href="./README.md">简体中文</a> | <a href="./README_en.md">English</a>
</p>

**AI-ChordCraft** 是一个 **LLM 赋能的自动扒谱工作台**。它接收一段音频或视频，自动抽取音频，并将歌曲结构划分、调性速度估计、和弦识别、歌词 ASR 与大模型音乐理解整合到同一条流程中，最终在网页中生成和弦谱。

<p align="center">
  <img src="./assets/ai-chordcraft-overview.png" width="96%" alt="AI-ChordCraft automatic chord transcription overview" />
</p>



### 📰 新闻

- 🎉 **2026.06:** AI-ChordCraft 正式开源，一个 LLM 赋能的自动扒谱与交互式和弦谱生成工作台。

### 📚 目录

- [介绍](#intro)
- [主要功能](#features)
- [系统流程](#workflow)
- [快速开始](#quick-start)
- [项目结构](#project-structure)
- [更多信息](#more-info)
- [许可证说明](#license)
- [引用](#citation)
- [English](./README_en.md)

<a id="intro"></a>
### 🎼 介绍

AI-ChordCraft 的核心目标是用 LLM 增强传统自动扒谱流程：系统不只输出和弦进行，而是把结构、和声、歌词、调性、速度、段落边界和可播放时间轴组织成一份面向演奏和讨论的工作材料。用户可以从一首歌直接得到结构化和弦谱，检查每个段落，播放局部片段，选择段落继续跟大模型聊天，获得音乐编曲上的指导。

相比只做单点 MIR 识别的工具，AI-ChordCraft 更强调 **识别 + 解释 + 交互 + 编配** 的闭环：

- 🎧 **识别**：从音频或视频中提取结构、和弦、调性、速度和歌词等核心扒谱信息。
- 🧠 **解释**：使用外部 LLM 对段落、和声走向、整体风格和可疑点进行自然语言解释。
- 💬 **交互**：用户可以选中任意段落，继续询问“这里为什么是这个和弦”“副歌能否改成更适合吉他的进行”等问题。

当前自动扒谱流程整合了三类能力：

- 🧱 **音乐结构分析**：调用 SongFormer 对完整歌曲进行段落切分，输出 intro / verse / chorus / bridge / outro 等结构标签和时间边界。
- 🎹 **自动和弦识别**：使用高精度和弦识别模型输出带时间戳的和弦事件，并自动映射到歌曲段落中。
- 🧠 **LLM 音乐理解与推理**：通过兼容接口接入外部 LLM 服务，补充歌词 ASR、整体音乐描述、段落级解释、音乐问答与编配相关推理，让扒谱结果从“识别结果”升级为可交流、可修改的音乐材料。推荐使用 MOSS-Music，也可以接入其他兼容服务。

<a id="features"></a>
### ✨ 主要功能

- 📤 **Web 上传音频 / 视频**：支持常见音频和视频格式，视频会先抽取音频再进入分析流程。
- 🎼 **结构化和弦谱生成**：按歌曲段落展示和弦、时间点、歌词、调性、速度和整体信息。
- ▶️ **时间轴播放与段落播放**：可以播放整首音频，也可以跳转播放单个段落，便于人工复核。
- 💬 **LLM 赋能的音乐问答**：用户可以选择若干段落，继续询问和声走向、扒谱依据、编配、练习或改编问题。

<a id="workflow"></a>
### 🔄 系统流程

AI-ChordCraft 将传统音频分析模块与 LLM 推理模块串成一条面向扒谱的工作流：

```text
音频 / 视频上传
        |
        v
音频提取与标准化
        |
        +--> SongFormer 结构切分
        |
        +--> ACR 和弦识别
        |
        +--> 外部 LLM 歌词 ASR
        |
        v
段落对齐与和弦谱渲染
        |
        v
交互式浏览器界面 + 音乐问答 + 编配工具
```

<a id="quick-start"></a>
### 🚀 快速开始

#### ⚙️ 环境配置

```bash
cd AI-ChordCraft
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

准备本地第三方运行时目录：

```bash
bash scripts/prepare_third_party.sh
```

最终目录结构如下：

```text
third_party/
  MOSS-Music/
    model/
      MOSS-Music-8B-Thinking/
      MOSS-Music-8B-Instruct/
  SongFormer/
    src/SongFormer/ckpts/SongFormer.safetensors
    src/SongFormer/configs/SongFormer.yaml
  ChordMiniApp/                       # ACR 运行时来源
  acr_model/                          # AI-ChordCraft 使用的 ACR 运行时副本
    btc_chord_recognition.py          # （由 ChordMini 提供）
    config/btc_config.yaml            # （由 ChordMini 提供）
    checkpoints/
      btc/btc_combined_best.pth       # PL 权重
      SL/btc_model_large_voca.pt      # SL 权重
```


如果你把这些组件放在其他位置，不需要移动文件，直接修改 `.env` 中对应路径即可。

#### 🧠 LLM 推理服务

AI-ChordCraft 更常用的接入方式是准备一个兼容 `/generate` 的 LLM 服务地址、API key 和 model name。推荐使用 MOSS-Music 作为音乐理解模型，也可以接入其他兼容服务：

```env
CHORDCRAFT_SGLANG_BASE_URL=http://127.0.0.1:30000
CHORDCRAFT_SGLANG_API_KEY=your-api-key
CHORDCRAFT_SGLANG_MODEL_NAME=your-model-name
```

`CHORDCRAFT_SGLANG_BASE_URL` 是统一 LLM 地址，默认所有文本生成任务都走这一个端点。`CHORDCRAFT_SGLANG_API_KEY` 会以 Bearer Token 形式发送；本地无鉴权服务可以留空。`CHORDCRAFT_SGLANG_MODEL_NAME` 会作为请求中的 `model` 字段发送，适合 OpenAI-compatible 或路由型服务；如果本地 `/generate` 服务已经固定绑定模型，可以留空。

如果使用本地 MOSS-Music + SGLang，请先安装上游运行依赖，然后启动一个本地服务：

```bash
bash scripts/start_llm_sglang.sh
```

脚本默认启动 `CHORDCRAFT_SGLANG_INSTRUCT_MODEL_PATH` 指向的模型，端口默认 `30000`。双模型变量仍向后兼容，但不是默认部署方式。这个脚本是可选方案；如果使用托管的 OpenAI-compatible 服务，只需要配置上面的 URL、API key 和 model name。

#### 🧱 SongFormer 结构划分服务

结构切分默认使用 SongFormer。请单独启动 SongFormer 服务，并在 `.env` 中配置服务地址：

```env
CHORDCRAFT_SONGFORMER_BASE_URL=http://127.0.0.1:8080
CHORDCRAFT_SONGFORMER_TIMEOUT=900
```

AI-ChordCraft 会把音频文件上传到：

```text
POST ${CHORDCRAFT_SONGFORMER_BASE_URL}/api/songformer/segment
```

clone SongFormer 并放好 checkpoint/config 后，可以启动 AI-ChordCraft 期望的结构服务：

```bash
bash scripts/start_songformer_service.sh
```

也可以使用进程内 SongFormer 运行时，此时需要设置 `structure_engine=songformer-local`，并额外指定 SongFormer 根目录和模型文件：

```env
CHORDCRAFT_SONGFORMER_ROOT=./third_party/SongFormer
SONGFORMER_MODEL_NAME=SongFormer
SONGFORMER_CHECKPOINT=src/SongFormer/ckpts/SongFormer.safetensors
SONGFORMER_CONFIG=src/SongFormer/configs/SongFormer.yaml
```

#### 🌐 启动 Web 应用

```bash
bash scripts/run_demo.sh
```

打开：

```text
http://127.0.0.1:7862
```

<a id="project-structure"></a>
### 🗂️ 项目结构

```text
AI-ChordCraft/
  app.py                  # FastAPI Web 服务
  frontend/               # 浏览器 UI
  third_party/README.md   # 本地运行时结构说明；上游代码和 checkpoint 会被忽略
  src/
    song_analysis.py      # 主分析流程与和弦谱渲染
    chat_agent.py         # 后续音乐问答与 prompt 策略
    chord_recognition.py  # 自动和弦识别、Essentia 风格辅助与后处理
    structure_recognition.py
    arrangement.py        # 编配 Agent 流程
  scripts/
    prepare_third_party.sh
    start_llm_sglang.sh
    start_songformer_service.sh
    run_demo.sh
  requirements.txt
```

<a id="more-info"></a>
### 🔗 更多信息

- **AI-Musician-Skills**: [https://github.com/jassary08/AI-Musician-Skills](https://github.com/jassary08/AI-Musician-Skills)
- **MOSS-Music**: [https://github.com/OpenMOSS/MOSS-Music](https://github.com/OpenMOSS/MOSS-Music)
- **Automatic Chord Recognition paper**: [https://arxiv.org/abs/2602.19778](https://arxiv.org/abs/2602.19778)
- **SongFormer paper**: [https://arxiv.org/abs/2510.02797](https://arxiv.org/abs/2510.02797)

<a id="license"></a>
### 📄 许可证说明

发布或再分发前，请分别检查本仓库代码、外部模型权重、外部运行时、依赖库和示例音乐的许可证。不要随本仓库分发第三方 checkpoint 或受版权保护的音乐，除非其许可证明确允许。

<a id="citation"></a>
### 📝 引用

如果你在研究或应用中使用 AI-ChordCraft，请引用本项目以及实际使用到的上游模型和方法：

```bibtex
@misc{aichordcraft2026,
      title={AI-ChordCraft: An LLM-Enhanced Workspace for Automatic Chord Transcription and Music QA},
      author={jassary08},
      year={2026},
      howpublished={\url{https://github.com/jassary08/AI-ChordCraft}},
      note={Open-source software project; no associated paper}
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
