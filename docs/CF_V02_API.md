# ComfyFlash v0.2 调用与媒体交付

## 发现能力与预检

在仓库目录运行以下命令，`python` 指注册报告列出的本机解释器。已经注册的项目可直接用返回的 `tone_list_argv`、`tone_info_argv` 和 `play_argv`。

```powershell
python -B engine/agent.py tone-list
python -B engine/agent.py tone-info t2
python -B engine/agent.py preflight --request D:/shots/shot02.json
python -B engine/agent.py play --request D:/shots/shot02.json
```

Python 入口为 `Flash.tone_list()`、`Flash.tone_info(tone_id)`、`Flash.preflight(tone, **request)` 和 `Flash.play(tone, **request)`。`preflight` 解析尺寸、时间与采样参数，检查输入、输出和本机依赖，然后返回 `resolved` 或结构化错误。注册与预检都无需启动推理。支持的能力来自 `engine/bank/t*.py` 中的 `TONE`。目录给出输入输出类型、角色、能力标签和 Schema 版本及哈希。详情给出请求 Schema、模型状态、预设和 `prompt_guide_ref`。在首次使用某种模式时打开相应指南。

请求 Schema 面向标准 JSON 值。`x-path`、`x-canvas`、`x-temporal` 和 `x-sampling-policy` 补充宿主文件系统、画布上限与对齐、帧网格和旧采样组合的语义，`preflight` 执行这些检查。旧 Python 调用仍可使用 `Path`、参考图元组及声明允许的数值文本。H3 参考图 Tone 只接收一张图。外部音频、参考视频和首尾帧双锚定留待底层模型提供能力。

## 请求示例

```json
{
  "tone": "t2",
  "request_id": "shot02",
  "prompt": "<Picture 1> 中的人物抬眼看向镜头，背景保留轻微街声。",
  "image_in": "D:/shots/character.png",
  "resolution": "768x448",
  "duration": 5,
  "seed": 2026,
  "inspection_frames": 6,
  "output": "D:/shots/shot02.mp4"
}
```

H3 视频固定 24 fps，向上对齐到 `17n+5` 帧。5 秒请求先换算为 120 帧，对齐为 124 帧，完整交付时约为 5.167 秒。`requested_duration_s`、`requested_frame_count`、`aligned_frame_count`、`decoded_frame_count`、`trimmed_frame_count`、`actual_frame_count` 和 `actual_duration_s` 追溯整个过程。原有 `frames`、`duration`、`fps`、`file`、`tone`、`seed` 和 `elapsed_s` 保留。`inspection_frames` 的范围为 1–24，缺省抽 6 帧。单帧模式选起始帧，尾帧始终单独输出。

## Artifact 与观察包

| 结果 | 工作路径 | 内容 |
| --- | --- | --- |
| 主媒体 | `artifact.path` | 图片或视频 |
| 旁车 | `artifact.inspection.metadata` | `<filename>.meta.json`，含实际参数、种子、输入来源、模型指纹、编解码环境和技术检查 |
| 联系表 | `artifact.inspection.contact_sheet` | `<filename>.contact.png`，附帧号对应的显示时间 |
| 尾帧 | `artifact.inspection.last_frame` | `<filename>.last.png`，对应最终交付的最后一帧，保留编码前 RGB |
| 音轨报告 | `artifact.inspection.audio_report` | `<filename>.audio.json`，包含编码、采样率、声道数、时长、RMS、峰值及静音或削波检查 |

权重指纹 `sha256-size-and-edges-64k-v1` 使用文件长度和首尾至多各 64 KiB。注册时核验官方下载的完整哈希走独立流程。RMS 和峰值来自实际送入 AAC 编码前的 PCM，时长优先读封装流。容器及 H.264、AAC 帧的实际解码验证在临时路径进行。合成联系表和尾帧直接复用生成帧。技术警告包含近静音、抽样画面黑场、长度偏差和裁剪，警告随成功媒体返回。

输出默认保护已有文件。需要覆盖时传 `"overwrite": true`。成功发布整套文件后才返回 `ok:true`。Windows 上目标被占用会返回 `recoverable_paths`，占用解除后可以取回已编码文件。旁车成功写入标志完整交付。异常中断后的恢复说明见测试报告。

图像的 `artifact` 可以直接传给下一个 Tone 的 `image_in`，也可以传图片路径。把视频尾帧作为下一步参考图时，传 `artifact.inspection.last_frame`。Agent 可根据观察包修改下一步或重跑局部。CLI 批量按输入顺序返回 `request_index` 和 `request_id`，未提供 ID 时使用零起始索引。依赖素材须出现在使用它的请求前面，依赖批次在同一 worker 执行。

## 设备与记录

显式 `device` 默认固定。使用 `register --auto-schedule` 或在模型配置中写 `"auto_schedule": true` 开启 CLI 自动选卡。开启后，以当前可用显存、RAM 和设备可用性做保守准入。多个相互独立且规格相近的请求可集中复用模型。批量返回 `scheduling` 和 `device_profiles`，可查看选卡、拒绝理由和置信度。Python `Flash` 仍在调用进程内执行。收到单卡的 `CUDA_VISIBLE_DEVICES` 时使用该卡，收到多卡列表时按可见序号映射。

`telemetry_path` 指向该次命令的 JSONL，记录成功、失败和可识别的复跑尝试。CLI 成功写入 JSONL 后默认只在返回值给出遥测回执，传 `"verbose": true` 可同时返回详细记录。原始记录可关联 Artifact 和固定执行策略 `engine-v0.2-p1`。设备负载和可用显存可能随其他进程变化。阶段采样、Torch allocator 峰值及无法获得的计数分别标注。历史查询、隐私和存储恢复见 [RSI 存储说明](CF_V02_RSI_STORAGE.md)。
