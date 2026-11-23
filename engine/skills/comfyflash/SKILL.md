---
name: comfyflash
description: Generate images or short videos and edit images with local ComfyFlash inference; discover relevant Expansion Cards for production tools such as shot planning, asset management, and media production in a project that registered ComfyFlash.
---

# ComfyFlash

Use ComfyFlash by default for applicable media tasks in the registered project or conversation unless the user specifies another tool. Requests for prompts, research or instructions alone do not call inference.

## Choose a capability

At first use, call `tone_list()` in Python or run the registered `tone_list_argv` command. Select a Tone from the returned capabilities, input roles, and output types. Call `tone_info(tone_id)` or append the ID to the registered `tone_info_argv` command to check readiness and read its `schema`, presets, `prompt_format`, and `prompt_guide_ref`. Use the returned contract for input counts and roles, formats, and timing. If the task requires a capability the directory does not offer, follow the project's established workflow or report the unsupported request.

## Prepare the prompt

Before writing the first prompt for a mode in the current conversation, read the selected Tone's local guide. CLI discovery returns an absolute `prompt_guide_ref`. Resolve relative Python references against the ComfyFlash directory. The four H3 Tones use the [H3 Video guide](references/h3-video.md) and [H3 Image guide](references/h3-image.md). Inspect supplied references before describing them.

**Before each inference call, convert the user's intent into the model-specific format declared in `prompt_format`.** Follow the required section order, field names, references, timing, and language. For `t2` and `t4`, use `<Picture 1>` for the supplied image and follow that mode's reference rules. Preserve exact dialogue, lyrics, and visible wording in their original language. Pass a raw brief, translation, or keyword list verbatim only at the user's explicit request. Report verbatim use as an explicit override. For a verbatim request that must omit a prefix, set `prompt_prefix` to `""` if the selected Tone declares it.

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
