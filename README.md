# ComfyFlash

**ComfyUI 是音色实验室，ComfyFlash 是给 Agent 演奏的 AI 推理电子琴。** ⚡

在 ComfyUI 里探索模型、LoRA、节点组合与最佳参数；验证成熟后，将它们固化为 ComfyFlash 的 **Bank / Tone**。Agent 只要查看音色表、选择音色、调节通用旋钮、接入素材，就能直接 Play。**不需要画工作流，不启动 Web UI，不挂后台端口。**

**ComfyFlash 是通用的生产型推理架构，不是 H3 专用软件。** 当前 v0.1 以 **H3 / Pika 作为第一批实验和验证音色**，用来验证电子琴的设计能不能工作。以后可以装进别的图像、视频、音频乃至其他推理能力，无须把 Engine 改造成一张越来越大的工作流。

*ComfyFlash is an agent-native inference instrument. ComfyUI is the lab for designing and testing sounds; ComfyFlash turns verified inference recipes into playable tones. H3/Pika is the first bank used to validate this architecture, not a permanent feature limit. No workflow UI or background inference server is required.*

## 电子琴架构：琴身不变，音色自由增长

| 电子琴 | ComfyFlash | 谁负责什么 |
|---|---|---|
| **Engine（机芯）** | 通用推理运行时 | 模型加载、显存管理、执行、缓存与输出 |
| **Bank（音色库）** | `engine/bank/` | 收藏可独立调用的成品推理能力 |
| **Tone（音色）** | `t1`、`t2`、`t3`… | 各自的模型配方、推理算法及专属条件，不包办完整项目流程 |
| **Controls（旋钮）** | 标准生成参数 | 分辨率、时长、随机或固定种子等共用控制 |
| **Interface（插口）** | 标准输入输出 | Text In、Image In、预留 Audio In、File Out |
| **Player（演奏者）** | Agent | 发现音色、选择工具、组织任务、决定输出 |

```text
             Agent（演奏者）
                    │
         tone_list / tone_info
                    │
             Flash.play(...)
                    │
         ┌──────────┼──────────┐
         │          │          │
     Interface   Controls    Bank / Tone
      标准插口     通用旋钮     成品音色
         └──────────┼──────────┘
                    │
                 Engine
                    │
           共享/本地模型权重
                    │
            调用者的输出路径
```

**这不是「把 ComfyUI 工作流换成 Python 的壳」。** Tone 不执行 ComfyUI 工作流 JSON，而是调用真正的模型推理代码。Engine 管公共旋钮和接口，不让每个 Tone 反复实现同一套分辨率、种子、文件处理。Agent 不必阅读每个工作流，也不必手动学习每种模型的繁琐节点操作。

**加音色不换琴。** 新模型或更好的配方在实验室验证后，加入 Bank、声明输入与支持的旋钮，`tone_list()` 就会展示新能力。一个 Tone 做一件事；多 Tone 的业务编排交给 Agent，而不是塞进某个「万能工作流」。

### 首批已实现音色：H3 / Pika

| Tone | 名称 | 输入 | 输出 |
|---|---|---|---|
| `t1` | Pika 文生视频 | prompt | 含模型同期声的视频 |
| `t2` | Pika 参考生视频 | prompt + 一张 image_in | 含模型同期声的视频 |
| `t3` | H3 文生图 | prompt | 图片 |
| `t4` | H3 图片编辑 | prompt + 一张 image_in | 图片 |

这四个 Tone 是**首版样机的音色**，不代表未来只能支持四项功能或只能使用 H3。v0.1 使用本地 ComfyUI Python 环境及现有权重，视频基于 Pika Flex LoRA。当前 `audio_in` 是预留接口，四个 Tone 均未实现外部音频输入，视频同期声由模型自身生成。

### Agent 只需三个统一动作

```python
from engine import Flash

with Flash() as flash:
    flash.tone_list()       # 认识这台琴目前有什么音色
    flash.tone_info("t2")   # 查看该音色支持的插口和旋钮
    flash.play(
        "t2",
        prompt="<Picture 1> 里的女孩转身微笑",
        image_in=["D:/MV/character.png"],
        resolution="768x448",
        duration=1.6,
        seed=1234,          # None = 随机种子
        output="D:/MV/shot01.mp4",
    )
```

ComfyFlash 没有自己的 `out/` 目录，作品输出到调用者指定的位置。默认安静运行，不把大段进度日志塞入 Agent 上下文；同一 Python 会话内可复用模型。**无服务不等于无依赖**：现阶段仍直接复用已安装 ComfyUI 的 `comfy.*` 推理模块，只是不需要 ComfyUI 的网页、服务器、任务队列或工作流执行器。

更具体的兼容性、参数范围、采样配方、性能基准见 [引擎说明](engine/README.md)。

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

## 首次配置

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

| 功能 | 输入 | 输出 |
|---|---|---|
| t1 文生视频 | 提示词 | 带声视频 |
| t2 参考生视频 | 提示词和一张参考图 | 带声视频 |
| t3 文生图 | 提示词 | 图片 |
| t4 图片编辑 | 提示词和一张待编辑图 | 图片 |

批量生成时，在同一次 `play` 命令中重复传入 `--request`，每个请求使用独立的输出路径。检测到多张合适的 GPU 且当时有足够的空闲显存与内存时，每张卡处理独立的素材。资源不足时回退到单卡顺序生成。单个素材使用一张卡，所有生成进程随此次调用结束。

已在推理 Python 中的应用可直接使用 `Flash.tone_list()`、`Flash.tone_info()` 和 `Flash.play()`。同一个 `with Flash()` 适合连续生成并复用模型。运行限制、模型列表、基准和 Python 示例见[引擎说明](engine/README.md)。

## 许可

引擎源码使用 GPL-3.0。上游与移植说明见 [NOTICE.md](NOTICE.md)，许可全文见 [LICENSE](LICENSE)。模型、LoRA 与 Python 依赖按各自的许可单独取得和使用。
