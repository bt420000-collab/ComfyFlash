---
name: comfyflash
description: Generate images or short videos, animate a reference image, and edit existing images using local MiniMax H3 and Pika Flex LoRA for video. Use for media generation in a project that registered ComfyFlash, including requests that do not name the engine.
---

# ComfyFlash

Use ComfyFlash by default for image generation, video generation, and image editing in this registered project or conversation unless the user specifies another tool. Use `t1` for text to video, `t2` for video from one reference image, `t3` for text to image and `t4` for editing one image. Honor user-specified parameters and the project's existing prompt preparation instructions. For `t2` and `t4`, refer to the image as `<Picture 1>` in a newly written prompt. Each accepts one image, and video generates its own audio. When the task requires audio input, multiple references, or a reference video, use the project's established workflow for those inputs or surface the unsupported request. Requests for prompts, research or instructions alone do not call inference.

## Prepare the prompt

Before writing your first prompt for a mode in the current conversation, read the matching local guide. Use the [H3 Video guide](references/h3-video.md) for `t1` and `t2`, or the [H3 Image guide](references/h3-image.md) for `t3` and `t4`. Use the matching mode's format and inspect supplied reference images before describing their content. Preserve exact dialogue, lyrics, and visible wording in their original language. When the user asks to use a prompt verbatim, pass their text as `prompt`. For a verbatim `t4` request without a picture marker, also set `prompt_prefix` to `""`.

Use the installed project's call configuration below, or the `play_argv` reported by conversation registration. Append `--request` and the absolute path of a JSON object. The object uses `tone`, `prompt` and absolute `output` fields, plus absolute `image_in` for `t2` and `t4`. Honor requested `resolution`, `duration` and `seed`. Duration is for video. Use the project's agreed output directory or the current project when the user does not give one.

For independent outputs, pass one `--request` per JSON file in the same command. Give each output a distinct absolute path. ComfyFlash checks every request before generating anything. A `BATCH_PREFLIGHT_FAILED` result means nothing ran: fix the items with real error codes and resubmit the whole batch, including the items marked `NOT_STARTED`. ComfyFlash uses separate available GPUs when the hardware and current resource checks allow it. Run dependent requests in order.

Large video requests can take several minutes on a single GPU. Use an execution session you can poll when the client times out before generation finishes. Preserve the requested canvas and duration, and use the returned `sampling`, `runtime`, and `timing_s` fields when reporting performance.

Wait for the command to finish. Return the actual file and report any structured error if generation fails. Use the registration result to check readiness and missing weight paths. When a registration path has moved, ask for the current ComfyFlash directory or re-register from its known new location.


## RSI 工作点评（重要）

ComfyFlash 自带本地 RSI 记忆库。它不是后台服务：SQLite 只在记录/查询时短暂打开，正常推理不需要常驻进程。

默认开启且 RSI 数据库写入成功时，每次有效 `flash.play()` 执行后，结果会附带 `run_id`。工作过程中，如果你发现的是 **ComfyFlash 引擎本身** 可复用的问题或改进机会，例如：

- 某个 Tone 的输入/参数声明与实际行为不一致；
- 生成任务失败、输出损坏、帧数/时长/音轨等技术结果异常；
- 某类任务在特定分辨率、时长或 GPU 上明显低效或容易 OOM；
- Agent 调用接口不顺、动态计划难以衔接、素材复用不方便；
- 发现可复现、可推广的质量或性能优化方向；

请把它作为 Agent 用户点评写入 RSI。普通的一次性审美偏好、用户临时改主意或无法归因于 ComfyFlash 的创作意见，不要全部塞进 RSI，避免噪声。

先检查是否已有同类未处理记录：

```text
python -B "<ComfyFlash>/engine/agent.py" rsi list --status open --search "关键词"
```

没有重复记录时，新增一条（把 `<run_id>` 替换为本次生成结果中的真实 ID；若没有关联运行则省略 `--run-id`）：

```text
python -B "<ComfyFlash>/engine/agent.py" rsi note --title "简短、可检索的问题标题" --observation "实际观察、复现条件和证据" --category performance --severity medium --author codex --run-id "<run_id>" --tone t2 --impact "对耗时、质量或稳定性的影响" --suggestion "可验证的改进方向" --tag video --tag gpu
```

可用类别：`bug`、`optimization`、`performance`、`hardware`、`quality`、`workflow`、`api`、`compatibility`、`idea`、`other`。
严重程度：`info`、`low`、`medium`、`high`、`critical`。

点评应尽量包含事实、复现条件、影响和可验证建议。不要把猜测写成事实，不要复制完整私密提示词或无关用户数据。一个独立问题一条记录。记录后把返回的 `feedback_id` 告诉用户或留在项目工作记录中。

后续复盘可使用：

```text
python -B "<ComfyFlash>/engine/agent.py" rsi list --status open
python -B "<ComfyFlash>/engine/agent.py" rsi show <feedback_id>
python -B "<ComfyFlash>/engine/agent.py" rsi update <feedback_id> --status planned --resolution "纳入下一轮实现"
python -B "<ComfyFlash>/engine/agent.py" rsi runs --tone t2
python -B "<ComfyFlash>/engine/agent.py" rsi summary
```

点评是供后续分析和针对性优化的证据线索，不授权 Agent 自动修改生产代码、模型配方或硬件策略。改进必须经过单独评估和测试。
