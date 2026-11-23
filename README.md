# ComfyFlash

**简体中文** | [English](README.en.md)

![ComfyFlash 宣传概念图，展示 Agent 调用本地 H3/Pika 引擎生成图片和视频](docs/assets/comfyflash-home-hero.png)

**面向 AI Agent 的轻量化、模块化本地推理引擎。**

把验证成熟的模型推理方案封装成**可发现、可直接调用的独立功能**。Agent 不必搭建节点工作流，也不用启动网页或后台服务；只需查看可用功能、提供输入参数，就能在本地执行生成任务。

## 为什么需要 ComfyFlash？

在视频制作、批量生图等 **Agent 长任务** 中，传统 ComfyUI API 调用往往需要反复提交请求、查询进度、读取日志和获取结果。多轮工具交互会占用 Agent 的上下文与 Token，多级服务转发、队列和调度环节也可能增加等待时间。

**ComfyFlash 的目标，是减少这些不必要的交互与中间环节。** 将验证成熟的推理方案封装为本地可直接调用的功能，由 Python/命令行完成模型加载、推理与文件保存。Agent 一次调用即可拿到简洁结果，常规进度和冗长日志留在程序内部处理。

```text
传统调用：Agent → ComfyUI API → 服务/队列 → 推理 → 状态查询 → 结果读取
直接调用：Agent → ComfyFlash 本地代码 → 推理 → 文件与简洁结果
```

这里节省的 Token 主要来自**减少 Agent 参与的轮询、日志阅读和反复工具调用**，不是 HTTP 通信本身。ComfyFlash 也不会神奇缩短模型计算时间，重点在于降低长任务的调度成本、上下文负担和额外延迟。

## 主要特点

- **直接调用**：Python 函数或命令行执行，不启动 HTTP/WebSocket 服务。
- **按需扩展**：每个功能模块独立声明用途、输入、支持的参数和依赖；Agent 可自动发现。
- **统一接口**：图像、音频等输入输出及分辨率、时长、随机种子等常用参数由引擎统一处理。
- **复用资源**：共享现有模型权重，按需加载；同一 Python 进程可复用已加载的模型。
- **动态工作流方向**：不是取消工作流，而是取消预制工作流对 Agent 的束缚；目标是让 Agent 根据任务临时生成和调整 Plan，Tone 提供可复用的原子能力。当前版本仍由 Agent 通过直接调用组织多步任务，原生 Plan 执行层逐步演进。
- **RSI 本地经验记忆**：记录有效推理任务，并允许 AI Agent 作为实际使用者留下问题和优化点评，供后续检索与针对性改进。SQLite 按需读写，不启动数据库服务。

*ComfyFlash is a lightweight, modular local inference engine for AI agents. It exposes discoverable capabilities through a consistent Python interface, without a workflow editor or background API server. H3/Pika is the first supported model family, not a permanent product limitation.*

## 工作原理

```text
             AI Agent
                │
       查询功能与参数说明
     tone_list / tone_info
                │
      发起一次推理调用
           Flash.play
                │
     ┌──────────┼───────────┐
     │          │           │
 标准输入输出  通用参数    功能模块库
   文件/素材  尺寸/种子   t1 / t2 / ...
     └──────────┼───────────┘
                │
          本地推理核心
      模型加载 / 显存 / 解码
                │
       输出到调用者指定路径
```

| 组成 | 作用 |
|---|---|
| **推理核心（Engine）** | 管理模型加载、显存、执行及文件输出 |
| **功能模块库（Bank）** | 保存已经验证、可独立调用的推理功能 |
| **功能模块（Tone）** | 定义具体模型、推理配方和所需输入 |
| **通用参数（Controls）** | 统一管理分辨率、时长、随机/固定种子等参数 |
| **输入输出（Interface）** | 提供一致的文本、图像等输入及文件输出方式 |
| **AI Agent** | 发现功能、选择工具、决定如何组合和使用 |

**模块只负责自身的推理能力。** 通用参数和文件处理不必在每个模块里重复实现；任务如何拆分、组合哪些功能由 Agent 决定。预制工作流不应束缚 Agent：产品方向是让它按当前任务临时生成 Plan，在观察中间结果后修改步骤、局部重跑或继续。当前版本由 Agent 通过直接调用组织多步任务，原生 Plan 执行层尚待建设。Engine 未来负责校验和执行，不替 Agent 决定创作策略。增加新功能时，在模块库内声明接口和实现，Agent 就能通过相同的调用方式使用它。不同模型架构可能需要新增底层适配代码，但对外接口不必随之变化。

> 内部沿用 `Bank / Tone / Controls / Interface` 等命名，设计灵感来自电子琴的功能组织方式。这里的 `Tone` 指**独立推理功能**，不是音频音色，也不意味着项目只能处理音乐。

## 当前已实现：H3 / Pika（v0.1）

H3/Pika 是第一版用于验证上述设计的模型组合，**不是 ComfyFlash 的长期功能上限**。当前提供四个可直接调用的功能：

| 编号 | 功能 | 必需输入 | 输出 |
|---|---|---|---|
| `t1` | Pika 文生视频 | 提示词 | 含模型同期声的视频 |
| `t2` | Pika 参考生视频 | 提示词、单张参考图 | 含模型同期声的视频 |
| `t3` | H3 文生图 | 提示词 | 图片 |
| `t4` | H3 图片编辑 | 提示词、待编辑图 | 图片 |

当前视频基于 Pika Flex LoRA，使用本地已有的 H3/Pika 模型与 ComfyUI Python 推理环境。**不要求 ComfyUI 后台运行**，但仍直接依赖其中的 `comfy.*` 推理模块；它并非完全独立于 ComfyUI 安装。视频同期声由模型自身生成；`audio_in` 目前只是预留输入接口，四个功能均不支持外部音频输入。

### 三个统一调用方法

```python
from engine import Flash

with Flash() as flash:
    flash.tone_list()        # 列出可用功能
    flash.tone_info("t2")    # 查看功能说明、输入要求和参数

    result = flash.play(
        "t2",
        prompt="<Picture 1> 里的女孩转身微笑",
        image_in=["D:/MV/character.png"],
        resolution="768x448",
        duration=1.6,
        seed=1234,            # None = 随机种子
        output="D:/MV/shot01.mp4"
    )
```

不需要额外的输出目录：生成文件直接写入调用者指定的绝对路径。默认尽量减少日志输出，避免无关推理信息占用 Agent 上下文；同一 Python 会话支持模型复用。与完整 ComfyUI 工作流 JSON 不同，当前功能模块直接调用模型推理代码，不启动可视化节点执行器。

模型依赖、运行限制、性能基准与更完整的参数说明见 [引擎说明](engine/README.md)。

---

## 交给 Agent

把 **ComfyFlash 的本地目录绝对路径**发给有本机命令权限的 Agent。它可从目录里的 [AGENTS.md](AGENTS.md) 完成登记并回复「已注册功能」。如果客户端把裸路径当普通文本，可以发这句话：

> 读取「ComfyFlash 的绝对路径」中的 AGENTS.md，把 ComfyFlash 注册到当前项目并列出就绪功能。后续需要生成图片、视频或编辑图片时，按需调用。

| 你提供的范围 | 登记方式 |
|---|---|
| 当前对话 | 用注册返回的 `play_argv` 和技能说明默认调用 |
| 当前项目 | 安装项目技能，后续从该项目打开的对话可自动发现并默认调用 |

项目注册在 Codex 下写入 `.agents/skills/comfyflash/SKILL.md`，Claude Code 使用 `--agent claude-code`。登记后的技能包含本机路径，供该机器上的项目使用。Git 项目通过本地忽略规则保护此文件。若项目后来初始化 Git，可在初始化后重新注册一次。已打开的对话可立即使用本次返回的调用方式。新对话请从该项目打开。[Codex 项目技能文档](https://developers.openai.com/api/reference/cli)

注册通过后，除非你指定其他工具，当前对话及已注册项目默认使用 ComfyFlash 生图、生成视频和编辑图片。ComfyFlash 在本机生成，消耗本机算力，云端生成额度和 API token 消耗为零。Agent 的对话和工具调度仍按客户端计量。

Agent 在当前对话首次编写某种生成或编辑提示词前，会读取随包提供的对应指南。两份指南覆盖四种功能，每种都附一个完整示例。已有的项目技能可通过重新注册更新。

ComfyFlash 还会把有效推理记录写入本地 RSI 记忆库。Agent 在工作中发现引擎问题或可复用的优化机会时，可以通过 `python -B engine/agent.py rsi note ...` 留下点评，后续按类别、Tone、运行记录检索并针对性改进。RSI 使用按需打开的 SQLite，不启动后台服务；详细用法见 [RSI 说明](RSI/README.md)。[H3 视频提示词指南](engine/skills/comfyflash/references/h3-video.md) · [H3 图片提示词指南](engine/skills/comfyflash/references/h3-image.md)

## 首次配置

[ComfyFlash 安装视频（哔哩哔哩）](https://www.bilibili.com/video/BV1oysv6xE3j)

提供现有的 ComfyUI 安装、H3/Pika 权重和它的推理 Python。最新维护环境使用 ComfyUI 提交 [`0228a9d73`](https://github.com/Comfy-Org/ComfyUI/tree/0228a9d73)、Python 3.13、PyTorch 2.13、CUDA 13 及 NVIDIA 显卡。用普通 Python 3.10+ 执行注册即可，注册会查找已有的便携 Python 或虚拟环境。

从 ComfyFlash 根目录运行。注册会优先复用已配置的环境，或从常见位置选择已有的 ComfyUI 和权重。

```text
python -B engine/agent.py register
```

也可直接给出路径。模型、Python 和 CUDA 检查通过后，配置保存在本机的 `models/config.json`。

```text
python -B engine/agent.py register --comfyui-root "D:/ComfyUI/ComfyUI" --models-path "D:/ComfyUI/ComfyUI/models" --project "D:/MyProject"
```

额外模型目录可参照 [models/config.example.json](models/config.example.json)。用 `--python` 指定推理解释器，也可用同一组路径参数纠正本地配置。注册检查对应版本的权重尺寸和 safetensors 头部、Python 依赖能否导入、ComfyUI 是否有 H3 图像和视频支持，以及 CUDA 能否执行测试运算。它会报告每张可见显卡的总显存与剩余显存、系统内存和并发条件。

注册时会试跑已安装的 SageAttention 和 FP16 累加。测试通过的选项会写入配置，已有的显式设置优先。视频结果会回报实际采样步数、LoRA、默认注意力后端和各阶段耗时。切换精度或注意力后端可能改变同种子的画面。

缺权重时，报告提供文件、目标路径、下载总量和上游来源。Agent 在你确认后，使用报告的 `download_argv` 并追加 `--yes` 下载。下载只使用列出的上游文件，校验 SHA-256 后再写入目标路径。缺 Python 环境时，先确认再按官方方式安装或修复 ComfyUI 和 CUDA 版 PyTorch，完成后重新注册。模型许可见上游说明。[ComfyUI 安装指南](https://docs.comfy.org/installation) · [H3 视频权重](https://huggingface.co/Comfy-Org/MiniMax-H3) · [H3 图像权重](https://huggingface.co/Comfy-Org/MiniMax-H3-Image)

本项目用 16 GiB 级 NVIDIA 显卡验证四项功能，注册报告以 32 GiB 系统内存作为建议值。其他配置可参考检查结果试跑较小画布或较短视频。具体上限取决于任务规格与当时的可用内存。[引擎实测记录](engine/README.md#6-实测记录)

## 直接调用

注册返回的 `play_argv` 是执行参数数组。在它后面加 `--request` 和请求 JSON 的绝对路径。例如 t2 请求：

```json
{
  "tone": "t2",
  "prompt": "<Picture 1> in a locked-off shot, turning slightly toward the camera",
  "image_in": ["D:/MyProject/reference.png"],
  "resolution": "768x448",
  "duration": 1.6,
  "seed": 1234,
  "output": "D:/MyProject/shot01.mp4"
}
```

批量生成时，在同一次 `play` 命令中重复传入 `--request`，每个请求使用独立的输出路径。引擎在探测 GPU 和启动生成进程之前，先检查全部请求的参数、输入文件和权重。任一请求不合格时整批约一秒内返回 `code: "BATCH_PREFLIGHT_FAILED"`，出错项附带错误和请求文件路径，合格项标记为 `NOT_STARTED`。检测到多张合适的 GPU 且当时有足够的空闲显存与内存时，每张卡处理独立的素材。资源不足时回退到单卡顺序生成。单个素材使用一张卡，所有生成进程随此次调用结束。

批量调度会核对 CUDA 和 NVIDIA 设备报告的逐卡剩余显存。Windows 上使用动态显存的批量进程默认关闭锁页内存。遇到 AIMDO 权重缓冲区读写失败时，保留已完成的文件，并在本批进程退出后，用更保守的设置按原参数单卡重试一次。

已在推理 Python 中的应用可直接使用 `Flash.tone_list()`、`Flash.tone_info()` 和 `Flash.play()`。同一个 `with Flash()` 适合连续生成并复用模型。运行限制、模型列表、基准和 Python 示例见[引擎说明](engine/README.md)。

## 许可

引擎源码使用 GPL-3.0。上游与移植说明见 [NOTICE.md](NOTICE.md)，许可全文见 [LICENSE](LICENSE)。模型、LoRA 与 Python 依赖按各自的许可单独取得和使用。
