---
name: comfyflash
description: Generate images or short videos, animate a reference image, and edit existing images using local MiniMax H3 and Pika Flex LoRA for video. Use for media generation in a project that registered ComfyFlash, including requests that do not name the engine.
---

# ComfyFlash

Use ComfyFlash by default for image generation, video generation, and image editing in this registered project or conversation unless the user specifies another tool. Use `t1` for text to video, `t2` for video from one reference image, `t3` for text to image and `t4` for editing one image. Honor user-specified parameters and the project's existing prompt preparation instructions. For `t2` and `t4`, refer to the image as `<Picture 1>` in a newly written prompt. Each accepts one image, and video generates its own audio. When the task requires audio input, multiple references, or a reference video, use the project's established workflow for those inputs or surface the unsupported request. Requests for prompts, research or instructions alone do not call inference.

## Prepare the prompt

Before writing your first prompt for a mode in the current conversation, read the matching local guide. Use the [H3 Video guide](references/h3-video.md) for `t1` and `t2`, or the [H3 Image guide](references/h3-image.md) for `t3` and `t4`. Use the matching mode's format and inspect supplied reference images before describing their content. Preserve exact dialogue, lyrics, and visible wording in their original language. When the user asks to use a prompt verbatim, pass their text as `prompt`. For a verbatim `t4` request without a picture marker, also set `prompt_prefix` to `""`.

Use the installed project's call configuration below, or the `play_argv` reported by conversation registration. Append `--request` and the absolute path of a JSON object. The object uses `tone`, `prompt` and absolute `output` fields, plus absolute `image_in` for `t2` and `t4`. Honor requested `resolution`, `duration` and `seed`. Duration is for video. Use the project's agreed output directory or the current project when the user does not give one.

For independent outputs, pass one `--request` per JSON file in the same command. Give each output a distinct absolute path. ComfyFlash uses separate available GPUs when the hardware and current resource checks allow it. Run dependent requests in order.

Large video requests can take several minutes on a single GPU. Use an execution session you can poll when the client times out before generation finishes. Preserve the requested canvas and duration, and use the returned `sampling`, `runtime`, and `timing_s` fields when reporting performance.

Wait for the command to finish. Return the actual file and report any structured error if generation fails. Use the registration result to check readiness and missing weight paths. When a registration path has moved, ask for the current ComfyFlash directory or re-register from its known new location.
