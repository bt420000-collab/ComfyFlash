# ComfyFlash v0.2 P0/P1 测试报告

## 环境与命令

实机为 Windows WDDM、两块 RTX 5060 Ti 16 GiB、约 47.8 GiB 物理内存，驱动 `621.14`。推理运行在 ComfyUI `0228a9d73cd7c2daf8f61fe13d56b12efe307171`、Python 3.13.14、Torch 2.13.0+cu130、AIMDO 0.6.0 和 PyAV 18.0.0 上。引擎基线为 `86c9f70a67680fe6fcfd146327463d522738780e`。

```powershell
python -B -m unittest discover -s engine/tests
python -B engine/agent.py tone-list --config D:/ComfyFlash-local/config.json
python -B engine/agent.py preflight --config D:/ComfyFlash-local/config.json --request D:/shots/shot02.json
python -B engine/agent.py play --config D:/ComfyFlash-local/config.json --request D:/shots/shot02.json
python -B RSI/__main__.py --db D:/ComfyFlash-local/rsi.sqlite3 check
```

开发环境 Python 3.14 通过 88 项。推理环境通过 87 项，独立 JSON Schema 验证因未安装可选 `jsonschema` 跳过 1 项。对 Windows 嵌入式 Python 从仓库根目录执行下列命令，可显式加入测试导入路径。

```powershell
& $ComfyFlashPython -B -c 'import os,sys,unittest;sys.path.insert(0,os.getcwd());sys.exit(not unittest.TextTestRunner().run(unittest.defaultTestLoader.discover("engine/tests")).wasSuccessful())'
```

自动化覆盖动态 Tone、Schema 与运行预检、超量参考、5 秒对齐、裁剪、原生音轨、输出冲突、Windows 文件占用恢复、worker 失败和重试记录、设备固定及 RAM 准入、SQLite 初始化、迁移、忙锁和备份恢复。CPU 媒体测试会实际编码文件。MP4、MOV、MKV 中保留大小及 NAL 头的损坏视频尾包均被拦截。两线程严格视频验证在 192 帧、1344×768 的 CPU 样片耗时 0.285–0.319 秒，编码为 7.313 秒。抽帧预览直接使用编码前的 RGB 数据。后续加入 AAC 解码验证，CPU 用例证实 MP4 中等长的 AAC 坏包会拦截交付。AAC 指标仍来自编码前的 PCM。

## 实际 H3 产物与回读

同一个 CLI 批次用 GPU 0 顺序执行四种 Tone。`t4` 接收 `t3` 产物的 Artifact 引用。任务计时为调用本身，含输出交付。随后用当前严格读取器重新验证同一批媒体文件，并核对返回的 Artifact 与其旁车相等。

| Tone | 实际规格 | 耗时 | 验证 |
| --- | --- | ---: | --- |
| `t1` | 768×448，1.6 秒请求，38→39 帧 | 35.58 秒 | 39 帧可完整解码，AAC 双声道 32 kHz、1.625 秒 |
| `t2` | 768×448，5 秒请求，120→124 帧 | 67.57 秒 | 124 帧可完整解码，AAC 双声道 32 kHz、约 5.167 秒 |
| `t3` | 1024×576 | 16.89 秒 | PNG 尺寸、旁车一致 |
| `t4` | 1024×576 | 31.49 秒 | PNG 尺寸、旁车一致，保留 `t3` 输入旁车路径 |

视频尾帧的 PTS 与最后一个交付帧相符。H.264 解码尾帧与编码前尾帧 PNG 的 RGB PSNR 分别为 41.08 dB 和 36.69 dB。

遥测和 SQLite 接线后，另用 GPU 0 实跑 `t2` 的 768×448 / 1.6 秒请求，开启 `video_decode_offload`。43.26 秒交付 39 帧 H.264 和 AAC 音轨。回执、JSONL 与 SQLite 摘要的尝试 ID 和旁车路径一致，SQLite 可按设备 UUID、分辨率、时长和策略版本查回记录。驱动 `621.14` 从物理设备匹配，任务有 15 个资源阶段样本。帧网格造成的时长偏差按预期记录为警告。图片交付到历史查询另由实际 PNG 编码的 CPU 集成用例验证。

## H3 VAE 显存对照

同卡、同模型、同参考图、同 seed 1234、同 768×448 / 1.6 秒及 6 步 LCM Pika 参数，每轮独立进程。NVML 约 100 毫秒取样，报告观察到的峰。阶段边界同步。Windows CUDA 可分配空间和 NVML 整卡用量记录为两个指标。冷/热文件缓存跨轮变化，因此端到端只作观察。

| 路径 | 模型加载及 LoRA 应用 | 端到端 | 视频解码 | 解码入口 CUDA 空闲 | 解码 NVML 峰 | 整程 NVML 峰 | 主机已用峰 | GPU 平均占用 | 成功 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 动态默认 | 7.09 s | 79.43 s | 2.78 s | 1.61 GiB | 14.93 GiB | 15.32 GiB | 42.99 GiB | 25.6% | 1/1 |
| 动态加解码前卸载 | 3.31 s | 49.71 s | 2.65 s | 14.23 GiB | 4.00 GiB | 15.32 GiB | 42.51 GiB | 40.3% | 1/1 |
| 旧式显存 | 10.19 s | 113.99 s | 7.01 s | 14.01 GiB | 4.36 GiB | 12.23 GiB | 46.82 GiB | 32.7% | 1/1 |
| 动态默认复测 | 3.10 s | 42.14 s | 2.33 s | 1.61 GiB | 15.00 GiB | 15.32 GiB | 21.12 GiB | 48.3% | 1/1 |

三个动态结果的整段 RGB 和 PCM 哈希相同，采样后与解码前的 latent 哈希也相同。卸载约耗时 0.77 秒，解码峰值降低约 11 GiB，整程峰值仍出现在解码前。v0.2 提供 `video_decode_offload` 可选配置，默认关闭。旧式路径本轮最高进程 RSS 38.56 GiB，系统 RAM 最低可用 0.98 GiB。当前 `MiniMaxH3VideoVAE.decode_tiled` 委托给普通 `decode`，本轮未加入额外 tiled fallback。四次都成功，真实 OOM 恢复率没有样本。

## 两卡模型亲和与错峰实验

相同模型和固定 seed，两块卡按物理设备各启动一个进程。任务是 `t1,t1,t3,t3`，视频为 768×448、1.6 秒，图片为 1k。A/B 只改请求分配与第二个进程启动间隔。测试机的桌面负载会变化，实验按卡固定，生产 CLI 仍使用其显存/RAM 准入。

| 分配 | 批次总耗时 | 观察模型加载次数 / 总时间 | 开始/最低可用 RAM MiB | GPU 显存峰值 MiB | GPU 平均利用率 | 成功 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 轮转 | 65.52 s | 12 / 7.12 s | 41668 / 28548 | 15406 / 15512 | 35.5% / 47.2% | 4/4 |
| 模型亲和 | 71.81 s | 7 / 5.12 s | 41101 / 29945 | 15406 / 15409 | 46.1% / 23.0% | 4/4 |
| 模型亲和 + 错峰 5 秒 | 69.35 s | 7 / 5.24 s | 40127 / 29042 | 15406 / 15433 | 50.8% / 22.2% | 4/4 |
| 轮转复测 | 69.74 s | 12 / 6.13 s | 40875 / 28080 | 15406 / 15443 | 31.2% / 35.6% | 4/4 |

模型亲和减少了重复加载。混合工作负载把两条视频集中在同一卡后，批次没有提速，因此只为同类媒体和相近规格请求应用亲和。每 worker 12 GiB 的主机可用 RAM 预算和临近预算时的错峰由合成准入测试覆盖。当前证据不足以量化临近门槛时错峰带来的稳定性收益。最终 CLI 还实测了自动准入。两张设备可用显存分别为 15,172 / 741 MiB，报告拒绝低余量设备，并用单张设备处理两个预检请求。此次没有为验证调度额外执行推理。

## SQLite 与故障隔离

Windows 临时目录，Python 3.14.3 / SQLite 3.50.4。包含真实文件 I/O。100 次单摘要事务中位数 4.03 ms、P95 7.07 ms。每批 16 条的 30 次事务中位数 5.21 ms、P95 8.07 ms。按 Tone 和设备查询最多 10 条的中位数 0.92 ms、P95 1.32 ms。580 条含指标的摘要占 364,544 字节。四个独立进程各提交 10 批，每批 16 条，从释放起约 318 ms 完成，640/640 条唯一，单批 P95 129.94 ms。另一个四进程测试显式持有 `BEGIN IMMEDIATE`，短超时确实返回忙锁，释放后 120/120 条写入。备份和恢复使用不同路径，校验结构、外键及旧记录。坏库测试确认媒体成功结果照常返回。另用实际推理解释器执行完整 CLI，预检缺模型时未创建数据库，调用结束写入一条失败摘要，查询、备份和恢复后的摘要 ID 一致。

## 文件与已知边界

- P0 能力和预检涉及 `engine/contracts.py`、`engine/bank/h3_contract.py`、四个 Tone、`engine/core.py`、`engine/agent.py` 和 Agent 安装说明。
- P1 媒体与资源涉及 `engine/artifact.py`、`engine/media.py`、`engine/runtime.py`、`engine/device_profile.py`、`engine/setup_checks.py` 和 `models/config.example.json`。
- RSI 和遥测涉及 `RSI/`、`engine/telemetry.py`、`.gitignore`、相关测试及 API、存储和设计文档。测试在 `engine/tests/`。
- 多文件发布以元数据为最后的完成标志。进程被强制结束时应以旁车与文件一起核验，可检查同目录临时文件。发布异常的恢复路径覆盖了 Windows 文件占用，整机断电一致性未做验证。
- 任务遥测的系统 RAM 和显存值是阶段采样；PyTorch 峰只覆盖 allocator 自身。不可读指标返回空值。当前未证明用户原来 8 秒大画布任务能获得整程提速。
- P2/P3 预留历史画像、时长经济区间、实时进度、取消、Plan revision、提案分析和策略自动采纳。当前版本提供可追溯证据、保守准入和手工评审 API。
