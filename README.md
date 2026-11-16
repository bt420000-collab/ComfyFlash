# ComfyFlash

在本地 ComfyUI Python 环境中运行 MiniMax H3 推理，视频使用 Pika Flex LoRA。支持文生视频、单图参考生视频、文生图和图片编辑。生成在调用者的本地 Python 中同步执行，视频含同期声。

*ComfyFlash is a local Python runner for MiniMax H3 with Pika Flex LoRA for video. It uses an existing ComfyUI installation and weights. See the commands below to register the tool with a shell-capable agent or invoke it directly.*

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
