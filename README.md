# ComfyFlash

**简体中文** | [English](README.en.md)

![ComfyFlash 概念图，展示 Agent 调用本地推理引擎生成图片和视频](docs/assets/comfyflash-home-hero.png)

**v0.2.2** · 面向 AI Agent 的本地图片与视频推理工具。

ComfyFlash 把经过验证的 ComfyUI 推理配方封装成可直接调用的 Tone。Agent 按任务选择能力和参数，图片与视频在本机生成，一次调用即可拿到成品和检查结果。生成无需启动 ComfyUI 服务或访问云端生成 API。Agent 的对话和工具调度仍按客户端计量。

## 快速开始

把 ComfyFlash 的**本地绝对路径**交给有本机命令权限的 Agent。它会按登记说明检查环境、模型和 GPU，回报已就绪的能力。指定项目时会安装该项目的 Agent 技能。完成后，适用任务可默认调用 ComfyFlash。[Agent 登记说明](AGENTS.md)

也可以从仓库根目录手动登记：

```text
python -B engine/agent.py register
```

登记需要已有的 ComfyUI 和可运行 H3 的 NVIDIA CUDA 推理环境。用 Python 3.10+ 启动登记即可，登记会查找可用的推理解释器。测试环境与硬件记录见[实测报告](docs/TEST_REPORT.md)。[安装视频（哔哩哔哩）](https://www.bilibili.com/video/BV1oysv6xE3j) · [ComfyUI 安装指南](https://docs.comfy.org/installation)

登记成功后，本机设置写入 `models/config.json`。可用 `--comfyui-root`、`--models-path`、`--python` 指定已有安装。缺权重时，登记报告给出下载来源、大小和目标路径，得到用户确认后才下载。独立的 XCard 工具可放在 `xcard/<工具名>/` 下供 Agent 发现。[配置示例](models/config.example.json) · [XCard 接口](xcard/README.md)

## 生成能力

| Tone | 功能 | 输入 | 输出 |
| --- | --- | --- | --- |
| `t1` | Pika 文生视频 | 提示词 | 带模型同期声的视频 |
| `t2` | Pika 参考生视频 | 提示词、单张参考图 | 带模型同期声的视频 |
| `t3` | H3 文生图 | 提示词 | 图片 |
| `t4` | H3 图片编辑 | 提示词、待编辑图 | 图片 |
| `t5` | Pika 首帧生视频 | 提示词、首帧图片 | 带模型同期声的视频 |
| `t6` | Pika 多参考生视频 | 提示词、1–9 张参考图、可选动作视频 | 带模型同期声的视频 |
| `t7` | 视频超分 | 本地源视频 | RelayVSR 2×，保留源帧率和音轨 |
| `t8` | H3 TARA 动作迁移 | 参考图、动作视频、提示词 | 按动作视频的表演和时序生成，沿用源音轨 |

```text
python -B engine/agent.py tone-list
python -B engine/agent.py tone-info t2
python -B engine/agent.py preflight --request "D:/MyProject/shot.json"
python -B engine/agent.py play --request "D:/MyProject/shot.json"
```

首次使用一种 Tone 时，打开 `tone-info` 返回的提示词指南并按该模式制作请求。运行多个请求时，可用 `play --dry-run` 预检整批。视频成功后会交付联系表、尾帧、音轨报告和元数据。Python 应用也可直接调用 `Flash`。[CLI、Python 与请求示例](docs/API.md) · [H3 提示词指南](engine/skills/comfyflash/SKILL.md#prepare-the-prompt)

配置本地 H3 导演数据集后，Agent 写视频提示词前会按场景查找官方写作指南和镜头参考。将另行取得的兼容 SQLite 库放在 `engine/skills/h3vmaster.sqlite`。源码下载包提供查库工具和官方指南链接。[数据集与调用说明](docs/API.md#h3-视频提示词参考)

## 运行记录与许可

本地 RSI 记录运行摘要和 Agent 点评，支持迁移、查询与备份。运行记录、模型和机器配置保存在本地。[RSI 使用和维护说明](docs/RSI.md)

ComfyFlash 源码采用 GPL-3.0。模型、LoRA 和依赖遵循各自许可。[LICENSE](LICENSE) · [来源与许可](NOTICE.md) · [版本记录](CHANGELOG.md)
