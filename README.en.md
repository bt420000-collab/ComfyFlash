# ComfyFlash

**English** | [简体中文](README.md)

![ComfyFlash concept artwork showing an AI agent directly generating images and videos through a local inference engine](docs/assets/comfyflash-home-hero.png)

**A lightweight, modular local inference engine built for AI agents.**

ComfyFlash turns proven model-inference recipes into **discoverable, directly callable capabilities**. Instead of building node-based workflows or keeping a web server running, an agent can inspect the available capabilities, supply inputs and parameters, and generate media locally.

The v0.2 development branch adds capability discovery, preflight validation, media inspection, and local RSI history. See [the API guide](docs/CF_V02_API.md), [RSI storage](docs/CF_V02_RSI_STORAGE.md), and [the test report](docs/CF_V02_TEST_REPORT.md) in Simplified Chinese.

## Why ComfyFlash?

Long-running agent tasks, such as video production and batch image generation, may repeatedly submit jobs to the ComfyUI API, poll for progress, inspect logs, and retrieve results. Those additional agent-tool interactions consume context and tokens. Service forwarding, queues, and orchestration layers can also introduce waiting time.

**ComfyFlash aims to remove unnecessary interactions and intermediate layers.** A verified inference recipe becomes a locally callable Python or CLI capability. Model loading, inference, and file saving happen inside the program. The agent receives a compact result instead of handling routine progress updates or verbose logs.

~~~text
Typical flow: Agent → ComfyUI API → Server/Queue → Inference → Status polling → Results
Direct flow:  Agent → ComfyFlash local code → Inference → Output file + concise result
~~~

The potential **token savings come from fewer agent-driven polls, log reads, and repeated tool calls**, not from HTTP traffic itself. ComfyFlash does not magically speed up model computation; its main goal is to reduce orchestration overhead, context usage, and avoidable delays during long tasks.

## Key features

- **Direct execution:** Call Python functions or CLI commands without starting an HTTP/WebSocket service.
- **Modular capabilities:** Each module declares its purpose, inputs, supported controls, and dependencies so agents can discover it automatically.
- **Consistent interfaces:** Common controls such as resolution, duration, and random/fixed seed, as well as media I/O, are handled by the engine.
- **Resource reuse:** Reuse existing model weights, load models on demand, and keep models available within a single Python process when appropriate.
- **Inference only:** The agent handles shot planning, asset coordination, and production logic instead of embedding an entire production workflow into the inference module.

## How it works

~~~text
                 AI Agent
                    |
           Discover capabilities
          tone_list / tone_info
                    |
               Run inference
                Flash.play
                    |
       +------------+-------------+
       |            |             |
    Standard      Shared       Capability
      I/O         controls      modules
   files/media  size / seed   t1 / t2 / ...
       +------------+-------------+
                    |
             Local engine
       Model load / VRAM / decode
                    |
          Caller-chosen output path
~~~

| Component | Responsibility |
|---|---|
| **Engine** | Model loading, VRAM management, execution, and file output |
| **Bank** | A library of verified, independently callable capabilities |
| **Tone** | An individual capability: model, inference recipe, and required inputs |
| **Controls** | Shared parameters such as resolution, duration, and random or fixed seed |
| **Interface** | Consistent text/image inputs and file-based outputs, with room for future media types |
| **AI Agent** | Discovers capabilities, chooses tools, and decides how to combine them |

**Each module is responsible for one inference capability.** The engine handles common controls and file operations rather than duplicating them in every module. Agents decide how to decompose and combine tasks. Adding support for a different model family may require a new low-level adapter, while the public calling convention can remain consistent.

> The internal names `Bank`, `Tone`, `Controls`, and `Interface` were inspired by the organization of an electronic keyboard. A `Tone` means an **independent inference capability**, not necessarily an audio sound.

## Available today: H3 / Pika (v0.1)

H3/Pika is the **first model family used to validate the architecture**, not a permanent restriction on ComfyFlash. The current version implements four capabilities:

| ID | Capability | Required input | Output |
|---|---|---|---|
| `t1` | Pika text-to-video | Prompt | Video with model-generated synchronized audio |
| `t2` | Pika reference-to-video | Prompt + one reference image | Video with model-generated synchronized audio |
| `t3` | H3 text-to-image | Prompt | Image |
| `t4` | H3 image editing | Prompt + image to edit | Image |

Video generation currently uses Pika Flex LoRA, existing H3/Pika weights, and the local ComfyUI Python inference environment. **The ComfyUI server does not need to be running**, but the current implementation still imports its `comfy.*` inference modules. It is *not* yet a standalone replacement for the ComfyUI installation.

Video audio is generated by the model itself. `audio_in` is reserved for future use and is **not supported** by any of these four v0.1 capabilities.

### Three methods an agent needs to know

~~~python
from engine import Flash

with Flash() as flash:
    flash.tone_list()       # Discover available capabilities
    flash.tone_info("t2")   # Inspect required inputs and supported controls

    result = flash.play(
        "t2",
        prompt="<Picture 1> shows a girl turning and smiling",
        image_in=["D:/MV/character.png"],
        resolution="768x448",
        duration=1.6,
        seed=1234,           # None = random seed
        output="D:/MV/shot01.mp4"
    )
~~~

No dedicated `out/` directory is created: generated files go to the absolute output path supplied by the caller. Logging is quiet by default so that routine inference details do not fill the agent's context. The same Python session can reuse loaded models. Capabilities call inference code directly instead of executing a ComfyUI workflow JSON or starting the visual node executor.

See the [engine documentation](engine/README.md) for model dependencies, limits, inference presets, and benchmarks (currently in Chinese).

---

## Connect ComfyFlash to your agent

Give an agent with permission to run local shell commands the **absolute path of your local ComfyFlash directory**. The agent can follow [AGENTS.md](AGENTS.md) to register ComfyFlash and report its available capabilities.

If the client treats a bare path as ordinary text, send an instruction like:

> Read AGENTS.md in "<absolute path to ComfyFlash>", register ComfyFlash with the current project, and list ready-to-use capabilities. Use it when image generation, video generation, or image editing is needed.

| Scope | Registration behavior |
|---|---|
| **Current conversation** | Use the returned `play_argv` command and capability guide |
| **Current project** | Install a project skill so future agent sessions opened from that project can discover and invoke ComfyFlash |

For Codex, project registration writes `.agents/skills/comfyflash/SKILL.md`. For Claude Code, use `--agent claude-code`. The generated skill contains machine-local paths; local Git ignore rules keep that file out of commits. If the project is later initialized as a Git repository, register again after initialization to set up the ignore rules. An existing conversation can immediately use the invocation method returned by registration; start a new conversation from the registered project to discover its project skill. See the [Codex CLI documentation](https://developers.openai.com/api/reference/cli).

After successful registration, the current session and registered projects can use ComfyFlash for applicable image/video tasks by default, unless you choose another tool. Inference runs on your local hardware, requiring no cloud media-generation credits or remote model API tokens. **Your agent's conversation and tool use may still consume tokens under your client or provider's billing model.**

Before writing a prompt for a mode for the first time in a conversation, the agent reads the matching bundled guide. The guides include a complete example for each of the four modes. Register again to refresh a previously installed project skill. [H3 Video prompt guide](engine/skills/comfyflash/references/h3-video.md) · [H3 Image prompt guide](engine/skills/comfyflash/references/h3-image.md)

## Initial setup

[ComfyFlash installation walkthrough on Bilibili (Chinese)](https://www.bilibili.com/video/BV1oysv6xE3j)

You need an existing ComfyUI installation, the H3/Pika weights, and the Python interpreter used for inference.

The latest maintained environment uses ComfyUI commit [`0228a9d73`](https://github.com/Comfy-Org/ComfyUI/tree/0228a9d73), Python 3.13, PyTorch 2.13, CUDA 13, and NVIDIA GPUs. Registration can be started with a regular Python 3.10+ interpreter; it will attempt to locate an existing portable Python environment or virtual environment for inference.

From the ComfyFlash repository root:

~~~bash
python -B engine/agent.py register
~~~

Registration first attempts to reuse an existing configuration or find a suitable local ComfyUI installation and model weights. You can provide paths explicitly:

~~~bash
python -B engine/agent.py register --comfyui-root "D:/ComfyUI/ComfyUI" --models-path "D:/ComfyUI/ComfyUI/models" --project "D:/MyProject"
~~~

Successful configuration is saved in your local `models/config.json`. For additional model locations, see [models/config.example.json](models/config.example.json). Use `--python` to specify the inference interpreter; you may also rerun registration with explicit paths to correct an existing configuration.

The registration checks model file sizes and safetensors headers, Python import compatibility, H3 image/video support in the configured ComfyUI installation, and whether CUDA can execute a test operation. It reports available GPUs and per-device total/free VRAM, system memory, and potential concurrency constraints.

Registration tests the installed SageAttention and FP16 accumulation on the local GPU. It enables options that pass and respects explicit configuration choices. Video results report the sampling steps, LoRA, default attention backend, and time spent in each stage. Changing the precision or attention backend can change the output for the same seed.

If weights are missing, the report lists the needed files, target paths, total download size, and upstream sources. With your approval, the agent can use the reported `download_argv` with `--yes` to download them, checking SHA-256 before writing files. If the Python environment is missing, install or repair ComfyUI and CUDA-compatible PyTorch using the official instructions, then register again. Model licenses are governed by their respective upstream projects.

References: [ComfyUI installation guide](https://docs.comfy.org/installation) · [H3 video weights](https://huggingface.co/Comfy-Org/MiniMax-H3) · [H3 image weights](https://huggingface.co/Comfy-Org/MiniMax-H3-Image)

The four v0.1 capabilities have been tested on NVIDIA GPUs with approximately 16 GiB VRAM. The registration report recommends 32 GiB system RAM. On other hardware, start with smaller images or shorter videos; actual limits depend on the requested output and available memory. See the [engine benchmark records](engine/README.md#6-实测记录) (Chinese).

## Direct invocation

Registration returns a `play_argv` array containing the executable and command arguments. Append `--request` and the absolute path to a request JSON file. Example for `t2`:

~~~json
{
  "tone": "t2",
  "prompt": "<Picture 1> in a locked-off shot, turning slightly toward the camera",
  "image_in": ["D:/MyProject/reference.png"],
  "resolution": "768x448",
  "duration": 1.6,
  "seed": 1234,
  "output": "D:/MyProject/shot01.mp4"
}
~~~

For batch generation, repeat `--request` within one `play` invocation, using a distinct output path for each request. If multiple suitable GPUs are available and sufficient VRAM/system RAM is free, separate assets may run on different GPUs. Otherwise the engine falls back to sequential execution on one GPU. A single asset is handled by one GPU, and generation processes exit when the invocation completes.

Batch scheduling checks free VRAM reported by CUDA and the NVIDIA device for each GPU. On Windows, batch workers using dynamic VRAM disable pinned host memory by default. After an AIMDO weight-buffer failure, ComfyFlash preserves completed files, waits for the batch's workers to exit, then retries outstanding requests once on one GPU with more conservative settings and the original generation parameters.

Applications already running inside the inference Python environment may call `Flash.tone_list()`, `Flash.tone_info()`, and `Flash.play()` directly. Reusing one `with Flash()` session is useful for generating multiple assets without repeatedly loading the same models. See [engine/README.md](engine/README.md) for detailed options, model mappings, and benchmarking data.

## License

The engine source is released under **GPL-3.0**. See [NOTICE.md](NOTICE.md) for upstream attribution and porting notes and [LICENSE](LICENSE) for the full license text. Model weights, LoRAs, and Python dependencies are obtained and used under their respective licenses.
