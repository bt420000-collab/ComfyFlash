# ComfyFlash

**English** | [简体中文](README.md)

![ComfyFlash artwork showing an AI agent using a local image and video inference engine](docs/assets/comfyflash-home-hero.png)

**v0.2.1** · Local image and video inference for AI agents.

ComfyFlash exposes verified ComfyUI inference recipes as directly callable Tones. Agents select the capability and parameters for each task. One call runs inference on your machine and returns the generated media and its inspection files. The CLI starts on demand and exits when the job finishes. Your agent client may still meter conversation and tool use.

## Quick start

Give an agent with local command access the **absolute path to ComfyFlash**. It can register the tool, check the runtime, weights and GPUs, and report the available capabilities. Registering against a project installs its agent skill. [Agent registration instructions](AGENTS.md)

You can also register from the repository root:

```text
python -B engine/agent.py register
```

Use an existing ComfyUI installation and an NVIDIA CUDA environment that supports H3 inference. Start registration with Python 3.10 or newer. Registration finds a compatible inference interpreter. See the test report for the verified environment. [Test report (Chinese)](docs/TEST_REPORT.md) · [Installation video (Chinese)](https://www.bilibili.com/video/BV1oysv6xE3j) · [ComfyUI setup](https://docs.comfy.org/installation)

Registration saves local settings to `models/config.json`. Use `--comfyui-root`, `--models-path`, or `--python` to select an existing installation. The registration report lists sources, sizes, and paths for any missing weights, which can be downloaded after approval. Install independent XCard tools under `xcard/<tool-name>/` for local discovery. [Example configuration](models/config.example.json) · [XCard discovery (Chinese)](xcard/README.md)

## Capabilities

| Tone | Capability | Inputs | Output |
| --- | --- | --- | --- |
| `t1` | Pika text to video | Prompt | Video with generated audio |
| `t2` | Pika reference to video | Prompt and one reference image | Video with generated audio |
| `t3` | H3 text to image | Prompt | Image |
| `t4` | H3 image editing | Prompt and one source image | Image |
| `t5` | Pika first-frame video | Prompt and one first-frame image | Video with generated audio |
| `t6` | Pika multi-reference video | Prompt, 1–9 reference images, and an optional motion reference video | Video with generated audio |

```text
python -B engine/agent.py tone-list
python -B engine/agent.py tone-info t2
python -B engine/agent.py preflight --request "D:/MyProject/shot.json"
python -B engine/agent.py play --request "D:/MyProject/shot.json"
```

Read the selected Tone's section in the prompt guide returned by `tone-info`. Use `play --dry-run` to validate a batch before dispatch. Successful video jobs return a contact sheet, last frame, audio report, and metadata. Python applications can call `Flash` directly. [CLI, Python API and request examples (Chinese)](docs/API.md) · [H3 prompt guide](engine/skills/comfyflash/SKILL.md#prepare-the-prompt)

With a compatible H3 directing library at `engine/skills/h3vmaster.sqlite`, agents can look up official writing guidance and scene references before drafting a video prompt. The source package includes the lookup tool and links to the official guides. Obtain the optional SQLite library separately. [Prompt lookup and library setup (Chinese)](docs/API.md#h3-视频提示词参考)

## Local history and license

RSI stores run summaries and agent feedback in local SQLite and supports queries, migration, and backup. Runtime data, weights, and machine settings stay on your machine. [RSI guide (Chinese)](docs/RSI.md)

ComfyFlash source is GPL-3.0. Model weights, LoRAs, and dependencies follow their own licenses. [LICENSE](LICENSE) · [Source and license notices](NOTICE.md) · [Changelog](CHANGELOG.md)
