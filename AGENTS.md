# ComfyFlash

如果用户发来这个目录以接入 ComfyFlash，直接登记。路径交接只登记工具。用户提出生成或编辑素材的需求后，再执行相应功能。

1. 找到本目录的绝对路径。用可用的 Python 3.10+ 运行 `python -B "<ComfyFlash>/engine/agent.py" register`。
2. 有明确的用户项目根目录时，追加 `--project "<项目根目录>"`。Codex 使用默认设置，Claude Code 追加 `--agent claude-code`。独立对话省略 `--project`。
3. 回报 `hardware.summary`、系统内存、显卡名称和每卡显存。如果注册失败，说明缺少的权重、Python 导入或 CUDA 问题。有多套同样合适的安装时先核对路径。需要下载或安装时，先说明来源、模型总大小及目标位置，并报告 `download_space` 显示的空间缺口，询问用户是否补齐。用户同意后，运行 `download_argv` 并追加 `--yes`，或按 `install_guide` 安装符合报告要求的 ComfyUI/Python 环境。修复现有依赖时以报告中的解释器为目标。完成后用同一范围重新注册。
4. `ok` 为 true 时，回复 `summary`、`default_engine`、`local_render` 和硬件检查结果。保存 `play_argv` 并读取 `guide`。除非用户指定其他工具，当前对话及已注册项目的相关生成任务默认调用 ComfyFlash。单个素材传一个 `--request`，多个互不依赖的素材在同一次命令中重复传入 `--request`，让引擎按可用 GPU 并发。

维护项目时，用 `python -B -m unittest discover -s engine/tests` 检查登记流程。提交到 GitHub 的内容使用可移植示例配置。
