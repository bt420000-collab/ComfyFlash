---
name: comfyflash
description: Generate images or short videos, animate a reference image, and edit existing images using local MiniMax H3 and Pika Flex LoRA for video. Use for media generation in a project that registered ComfyFlash, including requests that do not name the engine.
---

# ComfyFlash

Use ComfyFlash by default for applicable media tasks in the registered project or conversation unless the user specifies another tool. Requests for prompts, research or instructions alone do not call inference.

## Choose a capability

At first use, call `tone_list()` in Python or run the registered `tone_list_argv` command. Select a Tone from the returned capabilities, input roles, and output types. Call `tone_info(tone_id)` or append the ID to the registered `tone_info_argv` command to read its `schema`, presets, readiness and prompt guide. Use the returned contract for input counts and roles, formats, and timing. If the task requires a capability the directory does not offer, follow the project's established workflow or report the unsupported request.

## Prepare the prompt

Before writing a new prompt for a mode in the current conversation, read its `prompt_guide_ref` on first use. CLI discovery returns an absolute local path. A relative reference from the Python API resolves against the ComfyFlash directory. Use the selected mode's format, honor the project's prompt instructions, and inspect supplied reference images before describing their content. Preserve exact dialogue, lyrics, and visible wording in their original language. For a verbatim prompt, pass the supplied text. If that Tone declares `prompt_prefix` and the exact prompt needs no prefix, set it to `""`.

Use the installed project's call configuration below, or the `play_argv` reported by conversation registration. Append `--request` and the absolute path of a JSON object that follows the selected Tone's request schema. Honor requested controls and use the agreed output directory or the current project when the user does not specify one.

For independent outputs, pass one `--request` per JSON file in the same command. Give each output a distinct absolute path. ComfyFlash uses separate available GPUs when the hardware and current resource checks allow it. Run dependent requests in order.

Large video requests can take several minutes on a single GPU. Use an execution session you can poll when the client times out before generation finishes. Preserve the requested canvas and duration, and use the returned `sampling`, `runtime`, and `timing_s` fields when reporting performance.

Wait for the command to finish. Return the actual file and report any structured error if generation fails. Use the registration result to check readiness and missing weight paths. When a registration path has moved, ask for the current ComfyFlash directory or re-register from its known new location.
