# ComfyFlash

在本地 ComfyUI Python 环境中运行 MiniMax H3 推理，视频使用 Pika Flex LoRA。支持文生视频、单图参考生视频、文生图和图片编辑。生成在调用者的本地 Python 中同步执行，视频含同期声。

*ComfyFlash is a local Python runner for MiniMax H3 with Pika Flex LoRA for video. It uses an existing ComfyUI installation and weights. See the commands below to register the tool with a shell-capable agent or invoke it directly.*

## 交给 Agent

把 **ComfyFlash 的本地目录绝对路径**发给有本机命令权限的 Agent。它可从目录里的 [AGENTS.md](AGENTS.md) 完成登记并回复「已注册功能」。如果客户端把裸路径当普通文本，可以发这句话：

> 读取「ComfyFlash 的绝对路径」中的 AGENTS.md，把 ComfyFlash 注册到当前项目并列出就绪功能。后续需要生成图片、视频或编辑图片时，按需调用。

| 你提供的范围 | 登记方式 |
|---|---|
| 当前对话 | 用注册返回的 `play_argv` 和技能说明接着调用 |
| 当前项目 | 安装项目技能，后续项目对话可自动发现并按需选择 |

项目注册在 Codex 下写入 `.agents/skills/comfyflash/SKILL.md`，Claude Code 使用 `--agent claude-code`。登记后的技能包含本机路径，供该机器上的项目使用。Git 项目通过本地忽略规则保护此文件。若项目后来初始化 Git，可在初始化后重新注册一次。已打开的对话可立即使用本次返回的调用方式。新对话请从该项目打开。[Codex 项目技能文档](https://developers.openai.com/api/reference/cli)

## 首次配置

提供现有的 ComfyUI 安装、H3/Pika 权重和它的推理 Python。最新维护环境使用 ComfyUI 提交 [`0228a9d73`](https://github.com/Comfy-Org/ComfyUI/tree/0228a9d73)、Python 3.13、PyTorch 2.13、CUDA 13 及 NVIDIA 显卡。用普通 Python 3.10+ 执行注册即可，注册会查找已有的便携 Python 或虚拟环境。

从 ComfyFlash 根目录运行。附近只有一套常见安装时可以直接注册：

```text
python -B engine/agent.py register
```

首次设置时，也可以直接给出路径。配置保存在本机的 `models/config.json`：

```text
python -B engine/agent.py register --comfyui-root "D:/ComfyUI/ComfyUI" --models-path "D:/ComfyUI/ComfyUI/models" --project "D:/MyProject"
```

额外模型目录可参照 [models/config.example.json](models/config.example.json)。可用同一组路径参数纠正本地配置，其他设置会保留。多个 Python 环境用 `--python` 指定，选定的路径保存在本地配置。注册输出报告每个功能所需权重的文件存在情况，并检查推理 Python 的主要依赖可发现性。实际 CUDA 和可用规格以生成结果为准。

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

已在推理 Python 中的应用可直接使用 `Flash.tone_list()`、`Flash.tone_info()` 和 `Flash.play()`。同一个 `with Flash()` 适合连续生成并复用模型。运行限制、模型列表、基准和 Python 示例见[引擎说明](engine/README.md)。

## 许可

引擎源码使用 GPL-3.0。上游与移植说明见 [NOTICE.md](NOTICE.md)，许可全文见 [LICENSE](LICENSE)。模型、LoRA 与 Python 依赖按各自的许可单独取得和使用。
