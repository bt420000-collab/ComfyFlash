# Comfy Flash H3 v0.1

> 一句话验收：ComfyUI 关着，Agent 调 `flash.play("t2", ...)`，真实视频直接出现在指定目录。

只装四个"音色"，不装 UI、不装服务、不装工作流运行器。

```
ComfyFlash/
├── engine/
│   ├── __init__.py       # 对外只暴露 Flash
│   ├── core.py           # 配置、参数校验、模型热复用、执行生命周期
│   ├── runtime.py        # 把本机 ComfyUI 的 comfy.* 推理模块当底层库绑定（零服务）
│   ├── h3.py             # 移植的 H3 AV 潜在 / 条件 / 画布逻辑
│   ├── media.py          # 图片读写 + mp4 封装（H.264 + AAC）
│   ├── README.md         # 本文件
│   └── bank/
│       ├── __init__.py   # 自动发现 Tone（tone_list 由此生成，不维护第二份清单）
│       ├── t1.py         # Pika 文生视频
│       ├── t2.py         # Pika 参考生视频
│       ├── t3.py         # H3 文生图
│       └── t4.py         # H3 图片编辑
└── models/config.example.json  # 本机设置保存在 models/config.json
```

## 1. 运行环境

注册命令需要 Python 3.10+。生成使用已有的 ComfyUI 推理环境。
`engine/agent.py` 从绝对路径启动时会加载同一份 ComfyFlash 源码。Python API 调用方只需让
ComfyFlash 根目录出现在当前解释器的模块搜索路径。设置方法见[项目 README](../README.md#首次配置)。

实测环境为 Windows、Python 3.13.14、PyTorch 2.13.0+cu130、CUDA 13 和 RTX 5060 Ti 16 GiB。
主要运行组件有 PyTorch、Pillow、NumPy、PyAV、safetensors、comfy-aimdo／comfy-kitchen，以及 ComfyUI
推理模块的传递依赖。基准环境另含 SageAttention、Triton。四种 Tone 共引用 9 个本地模型文件
（约 81.1 GiB），由既有模型目录提供。

## 2. Agent 只需要学三个方法

```python
from engine import Flash

with Flash() as flash:
    print(flash.tone_list())          # 四个音色 + 一行用途
    print(flash.tone_info("t2"))      # 输入、旋钮、范围、默认值、依赖模型是否存在

    result = flash.play(
        "t2",
        prompt="<Picture 1> 里的女孩转身微笑",
        image_in=["D:/MV/character.png"],
        resolution="768x448",         # 单卡短视频对照测试使用此画布
        duration=1.6,                 # 24fps 吸附到 39 帧 = 1.625 秒
        seed=1234,                    # None = 随机，结果里回传实际种子
        output="D:/MV/shot01.mp4",    # 必须是完整绝对路径
    )
    print(result)
```

> `resolution` 支持 `16:9 / 9:16 / 1:1 / 4:3 / 3:4 / 21:9 / 720p / WxH`（视频）
> 与 `1k / 2k / max / match / 16:9 / 9:16 / 1:1 / 4:3 / 3:4 / WxH`（图片）。图片比例预设以 1024×1024 的面积为目标，宽高对齐到 32 像素。`720p` 就是视频的 `16:9`（1344×768），
> 也可直接指定通过校验的 `WxH`。

成功：

```python
{"ok": True, "file": "D:\\MV\\shot01.mp4", "tone": "t2", "seed": 1234,
 "elapsed_s": 33.3, "width": 768, "height": 448, "frames": 39, "duration": 1.625, "fps": 24}
```

失败（不抛异常，返回可读错误）：

```python
{"ok": False, "file": None, "tone": "t2", "seed": 1234,
 "error": {"code": "MISSING_INPUT", "message": "Pika 参考生视频 需要 image_in（参考图/待编辑图）"},
 "elapsed_s": 0.0}
```

错误码：`UNKNOWN_TONE` / `MISSING_INPUT` / `UNSUPPORTED_INPUT` / `UNSUPPORTED_CONTROL` /
`MISSING_OUTPUT` / `BAD_OUTPUT` / `BAD_RESOLUTION` / `BAD_DURATION` / `BAD_SEED` / `BAD_PARAM` /
`MISSING_MODEL` / `OOM` / `RUNTIME_ERROR` / `CLOSED`。

`verbose=True`（构造参数或 `play` 参数）时成功结果会多带 `log` 字段；默认**安静**，
TensorRT/tqdm/加载日志全部被吞掉，失败时才随 `error.detail` 返回最后 4000 字符。

## 3. 四个音色

| Tone | 名称 | 必需输入 | 产物 | 底盘 / LoRA |
|---|---|---|---|---|
| `t1` | Pika 文生视频 | `prompt` | mp4（带同期声） | H3 `fl2va` int8 + `pika_h3_flex_fl2va_v1` |
| `t2` | Pika 参考生视频 | `prompt`, `image_in` | mp4（带同期声） | H3 `ref2va` int8 + `pika_h3_flex_ref2va_v1` |
| `t3` | H3 文生图 | `prompt` | png/jpg/webp | H3-Image int8（PDD 六头） |
| `t4` | H3 图片编辑 | `prompt`, `image_in` | png/jpg/webp | 同 t3，带 image_1 参考 |

公共控制由 Engine 统一处理：`prompt` / `image_in` / `audio_in` / `resolution` / `duration` / `seed` / `output`。
不适用的控制会明确报错，例如 t3 传 `duration` → `UNSUPPORTED_CONTROL`；
四个 Tone 都不接受 `audio_in`（H3 的同期声是模型自己生成的，v0.1 不做虚假音频输入）。

视频默认跟随 Pika 工作流，使用 6 步 LCM、固定 sigma 和 CFG 1。复现 v0.1 的采样配方时，
给 `play()` 传 `steps=20, sampler="res_multistep", scheduler="simple"`。显式传入旧版采样参数时，
省略的参数沿用 v0.1 默认值。可以用 `sampler="lcm"` 或 `scheduler="pika"` 明确选择六步配方。

## 4. 为什么是"零服务"

ComfyUI 的 `comfy/` 是 **namespace package**（目录里没有 `__init__.py`），
所以 `import comfy.sd` / `comfy.samplers` 只加载推理代码，不会执行 `main.py`、
不会拉起 `server.py` / PromptServer / WebSocket / 任务队列。

`Flash()` 保持轻量。首个有效请求才选定设备、加载 ComfyUI 推理模块，并在可用时启用 AIMDO
动态显存。条件编码、采样和解码结束后，沿用 ComfyUI 的缓冲区清理逻辑。同一会话复用模型、
VAE 和已装配的 LoRA，由显存管理器按需调度权重。

**不 import**：`main.py`、`server.py`、`nodes.py`、`comfy_execution.*`、`comfy_api_nodes.*`、
任何自定义节点包（那些是实验室，不是生产推理）。

实测：推理过程中该 Python 进程的 TCP 监听端口数 = **0**（`Get-NetTCPConnection -State Listen`）。

### 移植了什么

`engine/h3.py` 把 ComfyUI `comfy_extras/nodes_minimax_h3.py` 里三个条件节点的 execute 主体
与几何辅助函数搬了过来（只保留本机四个 Tone 走到的分支）：

- `MiniMaxH3ImageToVideo` → `cond_text_av`（t2va，纯文本）
- `MiniMaxH3ReferenceToVideo` → `cond_ref_av`（ref2va，单张参考图，`ref_image_size="match"`）
- `MiniMaxH3ImageConditioning` → `cond_image`（H3-Image 文生图 / 编辑）
- 以及 `adapt_canvas` / `size_within_area` / `size_short_edge` / `align_frame_count` /
  `temporal_shape` / `empty_av_latent`

模型数学、采样器、文本编码器、VAE、权重加载全部直接调用 ComfyUI 的推理模块，
不重写、不抄一份会漂移的副本。

## 5. 模型路径

`models/config.json` 指向现有的 ComfyUI 目录，不复制、不移动、不修改权重：

```json
{
  "comfyui_root": "D:/ComfyUI/ComfyUI",
  "comfyui_models_path": "D:/ComfyUI/ComfyUI/models",
  "model_dirs": { "diffusion_models": "...", "text_encoders": "...", "vae": "...", "loras": "..." },
  "device": "cuda:0"
}
```

- 每个 Tone 在 `TONE["models"]` 里声明自己真正依赖的文件；`tone_info()` 会逐个检查 `exists`。
- 找不到模型时报 `MISSING_MODEL` 并给出完整路径与目录，不静默下载、不替换模型。
- `device` 支持 `cuda:0` / `cuda:1` / `cpu`；`cuda:1` 通过 `CUDA_VISIBLE_DEVICES` 生效。
- `dynamic_vram` 默认启用。支持当前 NVIDIA 环境与完整 AIMDO 接口时走动态显存，其他情况使用旧式显存管理。显存后端在进程首次初始化时确定。
- `vram_headroom_gb` 是动态显存的额外余量，默认 0.1 GiB。旧式路径可通过 `reserve_vram_gb` 调整余量。
- `disable_pinned_memory` 和 `disable_comfy_compiler` 对应原版 ComfyUI 的同名启动选项。Windows 动态显存批量进程默认关闭锁页内存。配置中的显式值用于首次运行。AIMDO 权重缓冲区故障触发的重试会在新进程关闭锁页内存、模型编译器和 CUDA Graph。
- Agent 注册会在本机显卡上试跑已安装的 SageAttention 和 FP16 累加，通过后为未设置的选项启用加速。显式的 `true` 或 `false` 优先。直接使用手写配置时可按现有依赖和精度需求设置。
- 视频结果包含 `sampling`、`runtime` 和 `timing_s`，可核对实际步数、采样器、LoRA、默认注意力后端、显存管理和各阶段的耗时。

## 6. 实测记录

同机批量验收使用两张 RTX 5060 Ti 16 GiB 和约 48 GiB 系统内存。两个 1024×1024 的 t3 请求，单卡顺序执行 40.3 秒，双卡各负责一张图用时 35.4 秒，均为包含 Python 启动的整批时间。单张图与单条视频仍由一张卡执行。两组测试都由注册返回的 CLI 调用完成。

长视频记录使用单张 RTX 5060 Ti 16 GiB 和同一个 t2 请求，画布 768×1344、192 帧、8 秒。原配置的运行记录为 864 秒。开启 SageAttention 和 FP16 累加后，引擎用时 525.0 秒，其中六步采样 475.9 秒、视频解码 28.7 秒。两次均使用 Pika LoRA 与六步 LCM，输出规格一致。

同规格的原版 ComfyUI（`0228a9d73`）使用相同参考图、提示词、种子、权重和加速设置，生成用时 526.1 秒，另有 9.0 秒服务启动。原版采样约 470 秒。两个输出均为 768×1344、192 帧、24 fps，带 32 kHz AAC 音轨。解码 RGB 帧的 PSNR 为 39.6 dB。

同机把这组 t2 对照改为 768×448、39 帧，连续生成时改变提示词和种子。ComfyFlash 首次 / 热生成用时 39.6 / 26.2 秒，原版用时 33.8 / 26.5 秒，原版服务另有 9.0 秒启动时间。ComfyFlash 用同一个批量进程在单卡上顺序生成，两条均完成。

Windows 双卡复测使用两个独立的 1024×576、90 帧 t2 请求。两条都完成时，ComfyFlash 开启锁页内存用时 114.5 秒，期间最低系统可用内存 3.5 GiB。只关闭锁页内存后用时 121.3 秒，最低可用内存 24.9 GiB。原版 ComfyUI 双端口同时关闭锁页内存和模型编译器，从提交任务起用时 111.8 秒，另有 10.9 秒启动时间，最低可用内存 24.0 GiB。

2026-11-16 在一张 RTX 5060 Ti 16GB 上顺序测试。原 ComfyUI 使用本机提交 `0228a9d73`、
原生节点、SageAttention、FP16 累加和 0.1 GiB 动态显存余量。视频统一为 768×448、39 帧、
24 fps、6 步 LCM，t2 使用同一张参考图。图像统一为 1024×1024、6 步和同一套权重，
直接输入提示词。重复生成时改变提示词和种子。

| Tone | ComfyFlash 首次 / 热生成 | 原 ComfyUI 首次 / 热生成 | 原 ComfyUI 服务启动 |
|---|---:|---:|---:|
| t1 文生视频 | 32.2 秒 / 21.4 秒 | 32.3 秒 / 22.5 秒 | 12.6 秒 |
| t2 参考生视频 | 33.3 秒 / 24.0 秒 | 30.7 秒 / 24.4 秒 | 6.2 秒 |
| t3 文生图 | 17.8 秒 / 8.8 秒 | 12.6 秒 / 8.8 秒 | 6.1 秒 |
| t4 图片编辑 | 26.8 秒 / 16.6 秒 | 21.9 秒 / 16.4 秒 | 6.7 秒 |
| t1 默认画布与时长，1344×768 / 124 帧 | 254.8 秒 / — | 253.3 秒 / — | 5.7 秒 |

首次生成从新进程首次请求开始计时。ComfyFlash 在请求内初始化推理库，ComfyUI 的服务启动时间
单列。热生成沿用各自的同一进程。短 t1 基线的服务启动包含原版资源初始化。双卡 H3VM ExactSplit
属于另一套运行配置。首次指新进程首次生成，操作系统的文件缓存随实际测试顺序变化。
最终代码复测 t2 为 33.9 / 24.0 秒。一次在另一套底盘运行后首次启动 t2 耗时 55.8 秒。

在 ComfyFlash 的同一套六步 t1 配方下，旧式显存路径跑出 77.2 / 90.3 秒。启用本机推理优化后，
全程用时降到 32.2 / 21.4 秒。采样分别从 49.7 / 58.3 秒降到 18.6 / 14.8 秒。

四种 Tone 均产出真实文件。两组 t1 和两组 t2 的视频均解码得到 39 帧、24 fps、H.264 和
32 kHz AAC。与相同种子的原工作流产物相比，解码后的 RGB 帧 PSNR 为 42.2–43.6 dB。
默认画布对照的解码帧 PSNR 为 43.7 dB，双方均观测到 15,302 MiB 的显存峰值。
ComfyFlash 的 t2 热生成观测到 15,424 MiB。显存以 `nvidia-smi` 每 400 ms 采样。
同一 ComfyFlash 会话在生成 124 帧 t1 后继续生成 t2 成功，用时 27.8 秒。
默认 t2 在 768×448、124 帧下实测 71.1 秒。t4 复测峰值为 ComfyFlash 15,272 MiB、
ComfyUI 15,494 MiB。上述峰值是本次测试观测值。

## 7. 运行范围

1. 视频画布两轴按 32 对齐，面积上限约 768×1344，支持 768×448 等自定义画布。24 fps 的帧数向上吸附到 `17k+5`。
2. t2 每个采样步都处理参考图的 latent。可用画布、时长和显存取决于参考内容与设备负载。
3. 视频使用当前已验证的 INT8 VAE。旧式显存路径继续在视频解码前主动卸载采样模型。
4. 同一进程内的生成串行运行，多块显卡可分别交给独立进程。四种 Tone 继续通过 `Flash.play()` 直接产出文件。
5. NVIDIA 批量调度按显卡 UUID 核对 CUDA 和设备级剩余显存，取较小值。多卡批次遇到显存错误时，保留已完成的结果，等本批所有进程退出后，用最空闲的一张卡按原参数重试失败项一次。AIMDO 权重缓冲区读取或截断失败会结束出错进程，失败和未启动的请求也会按原参数在新进程中重试一次，包括只分配到一张 GPU 的批次。
6. `Flash.play()` 遇到 CUDA OOM 会返回 `OOM`，并清理已加载模型和引擎缓存。调用方可在同一个会话继续发起下一条请求。

## 8. 许可与出处

| 来源 | 许可 | 用法 |
|---|---|---|
| [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0 | 绑定推理模块，从 `nodes_minimax_h3.py` 移植条件逻辑，参照 `main.py` 与 `execution.py` 初始化和清理动态显存 |
| MiniMax-H3 权重 | 见 `MiniMax-H3-LICENSE.txt`（随权重分发） | 只读复用，未修改 |
| Pika LoRA | 见发布方的许可文件与 LoRA 元数据 | 只读加载 |

移植代码保留了原文件路径与函数名对应关系（见 `engine/h3.py` 文件头），
以便上游更新时对照。分发本引擎时需同时满足 GPL-3.0 的义务。

## 9. 修改记录

- 2026-11-16 v0.1.0：首版。四个 Tone 打通，零服务，单卡串行，模型热复用。

- 2026-11-16 性能优化。对齐 Pika 六步配方，接入原生动态显存，复用装配后的 LoRA，并建立单卡 ComfyUI 对照。

- 2026-11-16 添加 Agent 对话与项目注册、直接调用命令和开源交付文件。
