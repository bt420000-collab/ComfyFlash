# ComfyFlash v0.2.0 引擎开发

调用示例、请求 Schema、媒体交付与批量结果见 [API 指南](../docs/API.md)。登记和环境配置见[快速开始](../README.md#快速开始)。

## 代码入口

| 模块 | 职责 |
| --- | --- |
| `engine/agent.py` | 注册本地环境，提供命令行能力发现、预检、调度与生成 |
| `engine/core.py` | 实现公开的 `Flash`，解析配置，管理模型缓存和请求生命周期 |
| `engine/bank/` | 发现 `t*.py` 中的 `TONE` 与 `play(run)`，按能力声明生成目录与请求契约 |
| `engine/contracts.py`、`engine/bank/h3_contract.py` | 构建请求 Schema，解析画布、帧数和采样参数 |
| `engine/runtime.py`、`engine/h3.py` | 绑定本机 ComfyUI 推理模块，组装 H3 条件和 latent |
| `engine/media.py`、`engine/artifact.py` | 编码、回读验证、观察包与文件发布 |
| `engine/device_profile.py`、`engine/telemetry.py` | 采集设备画像，记录尝试与分阶段资源指标 |
| `RSI/`、`xcard/` | 本地 SQLite 运行记录与点评，独立制作工具目录发现 |

`Flash()` 读取配置，首个有效生成请求才初始化推理环境。`runtime.py` 在当前 Python 进程内直接调用 ComfyUI 的 `comfy.*` 模块，工作进程只需加载推理依赖。连续请求复用模型、VAE 和已装配的 LoRA。CLI 批量可把独立请求分配给不同的 GPU 进程，单条素材由一张 GPU 计算。

`engine/h3.py` 从 ComfyUI 的 `comfy_extras/nodes_minimax_h3.py` 移植了三种条件构造和对应的画布、时间、latent 辅助函数。`cond_text_av` 源自 `MiniMaxH3ImageToVideo`，`cond_ref_av` 源自 `MiniMaxH3ReferenceToVideo`，`cond_image` 源自 `MiniMaxH3ImageConditioning`。模型和采样器的实现由本机 ComfyUI 提供。移植与授权见 [NOTICE](../NOTICE.md) 和 [GPL-3.0 许可](../LICENSE)。

## 环境与模型

注册入口使用 Python 3.10+。推理使用带 H3 支持的 ComfyUI 环境和它的 Python，注册报告会检查 PyTorch、Pillow、NumPy、PyAV、safetensors、comfy-kitchen、psutil 及 CUDA。动态显存依赖当前 PyTorch、NVIDIA 与 AIMDO 接口。基准使用 Windows、Python 3.13.14 和 PyTorch 2.13.0+cu130，详细版本与硬件见[测试报告](../docs/TEST_REPORT.md)。

将本机路径写入 `models/config.json`，可参照[示例配置](../models/config.example.json)。`comfyui_root` 指向包含 `comfy/` 的目录，`comfyui_models_path` 可指定共享模型目录，`model_dirs` 可分别指定 `diffusion_models`、`text_encoders`、`vae` 和 `loras`。四个 Tone 共用或分别引用 9 个模型文件，完整集合约 81.1 GiB。

| 目录 | 权重文件 | Tone |
| --- | --- | --- |
| `diffusion_models` | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `t1` |
| `diffusion_models` | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `t2` |
| `diffusion_models` | `minimax_h3_image_pruned_int8_convrot.safetensors` | `t3`、`t4` |
| `text_encoders` | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `t1`–`t4` |
| `vae` | `minimax_h3_video_vae_int8_convrot.safetensors`、`minimax_h3_audio_vae_fp32.safetensors` | `t1`、`t2` |
| `vae` | `minimax_h3_image_vae_fp16.safetensors` | `t3`、`t4` |
| `loras` | `pika_h3_flex_fl2va_v1_bf16.safetensors` | `t1` |
| `loras` | `pika_h3_flex_ref2va_v1_bf16.safetensors` | `t2` |

`engine/bank/` 中的 Tone 声明是权重映射的来源。`tone-info` 返回每个模型的实际路径和文件状态，注册报告核查支持的模型文件，并提供补齐缺失权重所需的大小、空间和下载命令。权重与 LoRA 按各自许可单独取得，已有模型目录可供引擎只读使用。

## 调试与验证

- `dynamic_vram` 默认开启，满足环境条件时启用 AIMDO。动态显存的额外余量 `vram_headroom_gb` 默认为 0.1 GiB，旧式显存路径使用 `reserve_vram_gb`。显存后端在当前进程首次初始化时决定。
- `video_decode_offload` 可在动态显存模式下开启视频解码前卸载采样模型。旧式显存路径会主动卸载。Windows 动态显存批量工作进程默认关闭锁页内存，显式的 `disable_pinned_memory` 值决定首次运行行为。AIMDO 权重缓冲区故障触发安全重试时，新进程会关闭锁页内存、模型编译器和 CUDA Graph。
- 注册在兼容 GPU 上试跑已安装的 SageAttention 和 FP16 累加，验证通过后为尚未设置的选项启用加速。显式 `true` 或 `false` 优先。用视频结果的 `sampling`、`runtime` 和 `timing_s` 核对生效参数、显存路径和各阶段耗时。

在仓库根目录运行回归测试。

```powershell
python -B -m unittest discover -s engine/tests
```

实机对照、媒体回读和故障恢复见[测试报告](../docs/TEST_REPORT.md)。运行记录与点评见 [RSI 指南](../docs/RSI.md)。发布历史见[版本记录](../CHANGELOG.md)。
