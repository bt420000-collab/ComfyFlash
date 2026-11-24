# ComfyFlash API 与媒体交付

## 能力发现

登记完成后，在仓库根目录用报告指定的推理 Python 运行以下命令。Agent 可直接使用返回的 `tone_list_argv`、`tone_info_argv` 和 `play_argv`。 [登记步骤](../README.md#快速开始)

```powershell
python -B engine/agent.py tone-list
python -B engine/agent.py tone-info t2
```

`tone-list` 和 `Flash.tone_list()` 返回 Tone ID、输入输出角色、能力标签、`schema_version` 和 `schema_hash`。`tone-info <Tone ID>` 和 `Flash.tone_info(tone_id)` 返回对应的请求 Schema、预设、模型路径和文件就绪状态，以及 `prompt_format` 和 `prompt_guide_ref`。Schema 和 Tone 的版本字段均为 `0.2`。CLI 返回绝对指南路径，Python API 返回相对于 ComfyFlash 根目录的指南路径。

| Tone | 必需输入 | 产物 | 提示词格式 |
| --- | --- | --- | --- |
| `t1` Pika 文生视频 | `prompt` | 带同期声的视频 | T2VA 三段，`integrated_multimodal_description` → `overall_soundscape` → `non_diegetic_music` |
| `t2` Pika 参考生视频 | `prompt`、`image_in` | 带同期声的视频 | Ref2VA 六段，`subject_definitions` → `summary` → `retention_analysis` → `detailed_description` → `overall_soundscape` → `non_diegetic_music` |
| `t3` H3 文生图 | `prompt` | 图片 | 完整英文画面描述，先写用途和布局，再写主体和视觉细节 |
| `t4` H3 图片编辑 | `prompt`、`image_in` | 图片 | 单段英文描述，依次写 `<Picture 1>` 源图、目标画面和具体改动 |
| `t5` Pika 首帧生视频 | `prompt`、`image_in` | 带同期声的视频 | I2VA 首帧对齐句与 T2VA 三段结构 |
| `t6` Pika 多参考生视频 | `prompt`、`image_in` | 带同期声的视频 | Ref2VA 六段，图片按输入顺序编号，可选动作视频用 `<Video 1>` |

首次使用一种模式前查看 `prompt_guide_ref` 中对应的图像或视频小节。只有用户明确要求逐字传入时才直接使用原文。逐字传入 `t4` 时可设置 `"prompt_prefix": ""` 关闭自动添加的编辑前缀。 [H3 提示词指南](../engine/skills/comfyflash/SKILL.md#prepare-the-prompt)

## H3 视频提示词参考

`prompt-ref` 从本机 `engine/skills/h3vmaster.sqlite` 只读提取 MiniMax 官方写作 Skill、模式指南和场景参考，支持 `t1`、`t2`、`t5` 和 `t6`。写新提示词前运行 `tone-info` 给出的 `prompt_ref_argv`，附上场景关键词。首次为该模式写提示词时加 `--full-guide`。

将另行取得的兼容 SQLite 库放到上述路径，或用 `--db` 指向现有库。库需提供 `skills` 和 `text_files`，并保留 `h3-prompt-writing/SKILL.md`、`h3-prompt-writing/references/base-en.txt` 与 `h3-prompt-writing/references/ref-en.txt`。视频场景查询只使用 `medium = 'video'` 的已分类条目，也兼容早期纯视频参考库。

```text
python -B engine/agent.py prompt-ref t1 --full-guide
python -B engine/agent.py prompt-ref t2 --query "product advertising"
```

`official_skill` 返回官方写作流程。`references` 中的 `mode` 返回官方模式指南，默认提供节选，`--full-guide` 返回全文。`--query` 可用空格分隔的场景关键词查找最多三个技能，并从首选技能取一段 `scene` 参考。也可把返回的 `entry_path` 传给 `--entry` 精确选择。截断的条目会标记 `truncated`。`--db` 可指定本机的另一份库。缺库时返回 `available: false`，可从 `prompt_guide_ref` 打开官方来源。场景参考用于借鉴镜头、动作和声景，写法以官方模式指南为准，请求参数以 Tone 契约为准。

## 请求参数

- `tone`、`prompt` 和绝对输出路径 `output` 必填。图片可写 `.png`、`.jpg`、`.jpeg`、`.webp`、`.bmp`，视频可写 `.mp4`、`.mov`、`.mkv`。视频使用 H.264 与 AAC。
- `t2`、`t4` 和 `t5` 各接收一张图片，`t6` 接收 1–9 张有序图片。`image_in` 支持绝对路径、图像 Artifact 或符合数量要求的数组。`t6` 的可选 `video_in` 为单个本地视频绝对路径，按 24 fps 取样后至少需 5 帧。所有 Tone 的 `audio_in` 可省略或设为 `null`。视频同期声由模型生成。
- `seed` 可传整数以复用种子，省略或传 `null` 时随机生成。以实际生成结果中的 `seed` 为准。
- 视频 `resolution` 支持 `16:9`、`9:16`、`1:1`、`4:3`、`3:4`、`21:9`、`720p` 与自定义 `WxH`。`720p` 等于 `16:9` 的 `1344x768`。自定义画布宽高均以 32 像素为步长，短边至少 256 像素，面积至多为 768 × 1344 像素。
- 图片 `resolution` 支持 `1k`、`2k`、`max`、`16:9`、`9:16`、`1:1`、`4:3`、`3:4` 与自定义 `WxH`。比例预设以 1024 × 1024 像素为目标面积，并将宽高对齐 32 像素。自定义画布的宽高均为 32 像素的正整数倍，面积至多为 3104 × 3104 像素。`t4` 的 `match` 保持输入比例，将短边限制在 1024–2048 像素并对齐 32 像素。需要指定像素尺寸时用 `WxH`。
- 视频 `duration` 的请求范围为 1–15 秒，缺省为 5 秒，帧率固定为 24 fps。引擎先将 `duration × 24` 舍入为最接近的整数帧，恰逢半帧时取偶数，再向上对齐到 `17n+5`。5 秒请求从 120 帧对齐到 124 帧，交付 124 帧时约为 5.167 秒。图片的 `duration` 和 `fps` 均可省略或填 `null`。
- 视频默认使用 Pika 固定 sigma、6 步 LCM 和 CFG 1。可用 `sampler="lcm"` 或 `scheduler="pika"` 选择六步配方。旧版采样组合可传 `steps=20, sampler="res_multistep", scheduler="simple"`，选择旧预设时省略的采样器和步数按旧版默认值补齐。图像使用 PDD 六步配方。各 Tone 的有效范围以 `tone-info` 为准。

请求 Schema 描述标准 JSON 值。`x-path`、`x-canvas`、`x-temporal` 和 `x-sampling-policy` 补充宿主文件系统、画布限制、帧网格和采样组合约束，由预检执行。Python 调用也接受 `Path`、单元素参考图元组和契约中声明允许的数值文本。

## 命令行与 Python

将下面的示例保存为 `D:/shots/shot02.json`，把图片和输出换成本机的绝对路径。

```json
{
  "tone": "t2",
  "request_id": "shot02",
  "prompt": "subject_definitions:\n<Subject 1> is the woman in <Picture 1>, with her hair tied back and wearing the same gray jacket.\n\nsummary:\n[reference generation] <Subject 1> turns toward the camera and smiles in a single continuous shot.\n\nretention_analysis:\n<Subject 1> (appears in [Shot 1]): fully_preserved - Her face, hair and jacket match <Picture 1>.\n\ndetailed_description:\n[Shot 1] Live-action, an eye-level medium shot frames <Subject 1> beside a softly lit window. She slowly turns toward the camera, makes eye contact and smiles. The camera holds its position as daylight catches the folds of her gray jacket.\n\noverall_soundscape: Soft room ambience continues throughout the shot.\n\nnon_diegetic_music: N/A",
  "image_in": "D:/shots/character.png",
  "resolution": "768x448",
  "duration": 5,
  "seed": 2026,
  "inspection_frames": 6,
  "output": "D:/shots/shot02.mp4"
}
```

```powershell
python -B engine/agent.py preflight --request D:/shots/shot02.json
python -B engine/agent.py play --dry-run --request D:/shots/shot02.json
python -B engine/agent.py play --request D:/shots/shot02.json
```

`preflight` 校验单个请求的参数、输入、输出和本地模型文件，成功时在 `resolved` 返回解析结果。`play --dry-run` 用同一批处理校验规则检查一个或多个请求，成功时返回 `validations`。预检在加载推理模型前完成。CLI 可用 `--config` 指定配置文件，默认读取 `models/config.json`。

Python 调用方应让仓库根目录位于推理解释器的模块搜索路径。同一个 `with Flash()` 会话可连续生成并复用模型。`Flash(config=...)` 接受配置路径或配置字典。

```python
import json
from pathlib import Path

from engine import Flash

request = json.loads(Path("D:/shots/shot02.json").read_text(encoding="utf-8"))
request["output"] = "D:/shots/shot02-python.mp4"

with Flash() as flash:
    checked = flash.preflight(**request)
    if checked["ok"]:
        result = flash.play(**request)
        if result["ok"]:
            print(result["artifact"]["path"])
        else:
            print(result["error"])
    else:
        print(checked["error"])
```

`Flash.validate_request()` 是兼容入口，成功时返回验证摘要，错误时通过异常报告。`Flash.play()` 和 `Flash.preflight()` 的请求失败返回 `ok: false` 与 `error.code`、`error.message`。常见错误包括缺少输入 `MISSING_INPUT`、图片不可读 `BAD_INPUT`、输入数量错误 `BAD_INPUT_COUNT`、控制项不适用 `UNSUPPORTED_CONTROL`、缺少权重 `MISSING_MODEL`，以及显存不足 `OOM`。`verbose=True` 可在 Python 成功结果附带 `log`，推理错误的 `error.detail` 可包含尾部日志。遇到 `OOM` 时，引擎会清理模型和会话缓存，之后可继续使用同一会话。

## 批量执行与设备

在同一个 `play` 命令重复传递 `--request` 以提交批次。各请求须使用独立输出路径和唯一的显式 `request_id`，主文件和旁车路径均须避免冲突。调度前整批预检。任一请求未通过时，其他有效请求返回 `BATCH_ABORTED`。实际批量结果按输入顺序放在 `results` 中，每项附带从零起算的 `request_index`，缺省 `request_id` 使用同一索引。

依赖前序图片或视频尾帧的请求可引用相应的输出路径。先提交产生素材的请求，再提交使用素材的请求。依赖批次在单个工作进程顺序执行。前序交付失败时，其下游项返回 `DEPENDENCY_FAILED`。跨批次也可把图像结果的 `artifact` 传给下一步的 `image_in`，或者把 `artifact.inspection.last_frame` 用作视频尾帧参考。

显式设置 `device` 时 CLI 默认固定设备。用 `register --auto-schedule` 或配置 `"auto_schedule": true` 开启自动选卡。多请求批次在省略 `device` 时也可按资源调度。调度检查可见 GPU、可用显存和系统 RAM，可把规格相近的独立请求安排到同一设备以复用模型。查看结果中的 `parallel_reason`、`device_decisions`、`scheduling` 和 `device_profiles` 可追踪适用的分配决策。调度遇到可恢复的多卡显存故障或 AIMDO 权重缓冲区错误时，可让符合条件的失败项在新进程中重试一次。Python `Flash` 在调用进程中串行执行，GPU 受 `CUDA_VISIBLE_DEVICES` 的可见范围和顺序约束。

## Artifact 与观察包

成功结果的 `artifact` 包含主媒体路径、输入来源、实际采样参数、种子、模型指纹、运行环境与技术检查。Artifact 的 `schema_version` 为 `0.2`。

| 交付项 | 结果字段 | 文件与内容 |
| --- | --- | --- |
| 主媒体 | `artifact.path` | 生成的图片或视频 |
| 元数据旁车 | `artifact.inspection.metadata` | `<output>.meta.json`，包含提示词正文、输入及模型路径、参数和检查结果 |
| 视频联系表 | `artifact.inspection.contact_sheet` | `<output>.contact.jpg`，标注抽样帧的显示时间 |
| 视频尾帧 | `artifact.inspection.last_frame` | `<output>.last.png`，来自最后一帧的编码前 RGB |
| 音轨报告 | `artifact.inspection.audio_report` | `<output>.audio.json`，记录编码、采样率、声道、时长、RMS、峰值及静音或削波检查 |

`inspection_frames` 可指定 1–24 个联系表抽样帧，缺省为 6。单帧模式选取起始帧，尾帧始终写入独立文件。视频结果使用 `requested_duration_s`、`requested_frame_count`、`aligned_frame_count`、`decoded_frame_count`、`trimmed_frame_count`、`actual_frame_count` 和 `actual_duration_s` 记录请求到交付的过程。兼容字段 `file`、`tone`、`seed`、`elapsed_s`、`frames`、`duration`、`fps` 以及视频 `inspection` 仍可读取，其中 `duration` 是实际时长。

模型指纹 `sha256-size-and-edges-64k-v1` 对文件长度和首尾采样字节计算哈希。官方下载按发布方记录校验完整 SHA-256。视频在临时路径验证容器流及实际解码的 H.264、AAC 帧，音轨 RMS 和峰值来自 AAC 编码前的 PCM，时长优先读取封装流。近静音、削波、抽样黑场、时长偏差和帧裁剪等警告在 `artifact.validation` 中随结果交付。

已有输出默认受保护，冲突返回 `OUTPUT_EXISTS`。明确替换时使用 `"overwrite": true` 或 Python 的 `overwrite=True`。引擎先在输出目录写入并验证临时文件，再提交整套文件，最后发布元数据旁车作为完成标志。Windows 使用拒绝覆盖的重命名，POSIX 使用同卷硬链接来保护既有文件。若发布时遇到文件占用等故障，`recoverable_paths` 可列出用于恢复的已编码文件或备份。进程中断后的核验见[测试报告](TEST_REPORT.md)。

## 遥测与 RSI

`telemetry_path` 指向本次执行的 JSONL，记录成功、失败与可识别的重试。CLI 写入 JSONL 后默认返回简要遥测回执，可在请求中传 `"verbose": true` 同时查看详细记录。当前遥测记录的 `schema_version` 为 `1`，策略标识为 `engine-v0.2-p1`。设备指标按阶段采样，PyTorch 分配器峰值单独记录，不可用的指标以空值表示。

RSI 使用本地 SQLite，写入成功时可通过结果的 `run_id` 关联推理记录、Agent 点评和遥测。媒体交付状态独立于遥测或 RSI 写入结果。 [RSI 查询、配置与恢复](RSI.md) · [引擎开发说明](../engine/README.md) · [版本记录](../CHANGELOG.md)
