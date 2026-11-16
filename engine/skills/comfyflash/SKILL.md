---
name: comfyflash
description: Generate images or short videos, animate a reference image, and edit existing images using local MiniMax H3 and Pika Flex LoRA for video. Use for media generation in a project that registered ComfyFlash, including requests that do not name the engine.
---

# ComfyFlash

When the user requests an image or short video in a project or conversation that registered ComfyFlash, choose the matching function and run it directly, even if the user does not name the engine. Use `t1` for text to video, `t2` for video from one reference image, `t3` for text to image and `t4` for editing one image. Use user-specified tools and parameters first. Follow the project's existing prompt preparation instructions. For `t2` and `t4`, refer to the image as `<Picture 1>` in a newly written prompt. Each accepts one image, and video generates its own audio. When the task requires audio input, multiple references, or a reference video, use the project's established workflow for those inputs or surface the unsupported request. Requests for prompts, research or instructions alone do not call inference.

Use the installed project's call configuration below, or the `play_argv` reported by conversation registration. Append `--request` and the absolute path of a JSON object. The object uses `tone`, `prompt` and absolute `output` fields, plus absolute `image_in` for `t2` and `t4`. Honor requested `resolution`, `duration` and `seed`. Duration is for video. Use the project's agreed output directory or the current project when the user does not give one.

Wait for the command to finish. Return the actual file and report any structured error if generation fails. Use the registration result to check readiness and missing weight paths. When a registration path has moved, ask for the current ComfyFlash directory or re-register from its known new location.
