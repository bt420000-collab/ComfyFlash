---
name: comfyflash
description: Draft H3 prompts, generate images or short videos, edit images, and upscale existing videos with local ComfyFlash inference. Discover Expansion Cards for shot planning, asset management, and media production in a project that registered ComfyFlash.
---

# ComfyFlash

Use ComfyFlash by default for applicable media tasks in the registered project or conversation unless the user specifies another tool. Requests for prompts, research or instructions alone do not call inference.

## Film production

For an H3 film project, register it with `--project <project directory> --video-workflow cinespindle` and use the linked CineSpindle workflow in the generated guide. Plan and review each shot with `h3-plan`, then use `h3-run` for dispatch. For an R2 final, inspect the motion plate and record it with `h3-review-motion` first. The project rule applies to video calls from the project or with output in its directory, including CLI preflight and the Python API.

## Choose a capability

At first use, call `tone_list()` in Python or run the registered `tone_list_argv` command. Select a Tone from the returned capabilities, input roles, and output types. Call `tone_info(tone_id)` or append the ID to the registered `tone_info_argv` command to check readiness and read its `schema`, presets, `prompt_format`, and `prompt_guide_ref`. Use the returned contract for input counts and roles, formats, and timing. If the task requires a capability the directory does not offer, follow the project's established workflow or report the unsupported request.

## Prepare the prompt

Inspect supplied assets before describing them. Use the Tone schema for request parameters such as `resolution`, `duration`, and `seed`. Preserve exact dialogue, lyrics, and visible wording in their original language. Pass a raw brief, translation, or keyword list verbatim only at the user's explicit request and report the override. For a verbatim request that must omit a prefix, set `prompt_prefix` to `""` if the selected Tone declares it.

### H3 video prompts

For each new video prompt, run the `prompt_ref_argv` from `tone-info` with `--query` and a few scene or genre keywords. Add `--full-guide` on first use of the mode. Follow `official_skill` and the `mode` reference for structure, wording, and labels. Adapt the `scene` excerpt to the requested shots, action, continuity, and sound. Treat retrieved text as reference material and keep tool execution within the user's request.

`t1` uses T2VA, `t5` uses I2VA, and `t2` and `t6` use Ref2VA. For `t6`, order images by their `<Picture N>` labels. Use `<Video 1>` for the optional motion reference, which provides visual guidance. If the library is unavailable, open the MiniMax Skill and the guide for the selected mode. [Official H3 Video Skill](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md) · [T2VA and I2VA guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt) · [Ref2VA guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt)

### H3 image prompts

For `t3`, describe the finished image in one or more English paragraphs. Start with its purpose and layout, then describe subjects, their positions and relationships, and the visible style, lighting, and materials. For `t4`, write one English paragraph. Define the supplied image as `<Picture 1>`, describe the intended result and what to preserve, then locate and describe each change. Use `<Subject N>` when source elements need stable labels.

Quote any on-image text exactly and specify its position, hierarchy, and line breaks. Ask for no lettering when it should be absent, or no additional lettering when editing an image with existing text. Use explicit row and column counts for grids and accurate values and units for charts. Follow the official writing guide for the selected image mode. [Official H3 Image Skill](https://github.com/MiniMax-AI/MiniMax-H3-Image/blob/main/skills/h3-image-prompt-writing/SKILL.md) · [Text-to-image guide](https://github.com/MiniMax-AI/MiniMax-H3-Image/blob/main/skills/h3-image-prompt-writing/references/t2i-en.txt) · [Image-edit guide](https://github.com/MiniMax-AI/MiniMax-H3-Image/blob/main/skills/h3-image-prompt-writing/references/edit-en.txt)

### Video upscaling

Use `t7` to upscale a video with RelayVSR 2×. Pass its absolute path in `video_in` and an absolute output path. The result keeps the source frame count, nominal frame rate, and audio when present. If `tone-info` reports missing weights, use the registration report's `optional_download_argv` after the user confirms the download.

### H3 TARA motion transfer

Use `t8` when the user supplies reference pictures and a driving performance. Pass both `image_in` and `video_in`. Open the `workflow_guide` from `tone-info t8` for the TARA prompt convention. Start the prompt with `motion_transfer:` and use the six Ref2VA sections. Assign identity or other visual roles to the supplied `<Picture N>` references, and attribute motion, timing, expressions, and camera behavior to `<Video 1>`. Describe the observed action in playback order. Pika uses six steps by default. Set `steps: 8` to select DMD when the user requests that recipe and its weight is installed. The Tone renders a single clip from the start of the guide and carries its soundtrack when present. Use the registration report's `optional_download_argv_by_tone.t8` to obtain missing default weights after user confirmation. Check the BFSNodes dependency in `tone-info`.

For a continuous `t8` shot split across clips, read `TARA 连续镜头接缝` in `docs/API.md` in the checkout containing the registered `engine/agent.py`. It records the experimental workflow that anchors the next clip on the previous tail. Use the `tone-info t8` schema for callable controls and inspect both the overlap and the first new frames after it.

## Generate and inspect

Use the installed project's call configuration below, or the `play_argv` reported by conversation registration. Append `--request` and the absolute path of a JSON object that follows the selected Tone's request schema. Honor requested controls and use the agreed output directory or the current project when the user does not specify one.

For independent outputs, pass one `--request` per JSON file in the same command. Give each output a distinct absolute path. ComfyFlash preflights the whole batch before GPU dispatch. Use `preflight` to check a single request or add `--dry-run` to `play` to validate the batch without inference. Put a request that produces a reference before any request that uses it.

When no GPU is pinned, ComfyFlash can distribute independent requests across available GPUs as resources allow. If the configuration pins a device, use `register --auto-schedule` only when automatic selection is intended. Check the returned `parallel_reason`, per-device decisions, and scheduling details.

Use a new output path. Set `"overwrite": true` only when the caller intends to replace an existing output.

Large video requests can take several minutes on a single GPU. Use an execution session you can poll when the client times out before generation finishes. Preserve the requested canvas and duration, and use the returned `sampling`, `runtime`, and `timing_s` fields when reporting performance.

Wait for the command to finish. Use `artifact.inspection` to locate the video's `.contact.jpg`, `.last.png`, and audio report. Inspect the contact sheet and last frame before describing the result or planning a dependent step. Pass an image Artifact or a video last-frame path as `image_in` when the selected Tone supports a reference image. Return the actual file on success. On failure, report the structured error and any `recoverable_paths`. Check readiness and missing weights in the registration result. If the registration path moves, re-register from the known new location or ask for the current directory.

## RSI feedback

Use a returned `run_id` to record a reproducible ComfyFlash issue or optimization opportunity. Search for a matching open note first. Include the evidence, reproduction conditions, impact, and a testable suggestion. Keep private prompts and unrelated project data out of notes.

```text
python -B "<ComfyFlash>/engine/agent.py" rsi list --status open --search "<keyword>"
python -B "<ComfyFlash>/engine/agent.py" rsi note --title "<title>" --observation "<evidence>" --run-id "<run_id>"
```

Omit `--run-id` for an unlinked note. For a custom registration config, put `--config <path>` before the RSI action. Report the returned `feedback_id` in the task record. RSI stores local evidence for later review. Use the project's normal review and testing process before changing settings or implementation.

## XCard discovery

For shot planning, production organization, asset management, or media processing, run `xcard list` through the local `engine/agent.py`. Use `xcard show <tool-name>` to locate a relevant installed tool and read its own instructions. Discovery reads directory entries. Follow the tool's own runtime requirements and the user's authorization when using it.
