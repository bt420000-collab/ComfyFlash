# ComfyFlash

如果用户发来这个目录以接入 ComfyFlash，直接登记，完成后回复注册结果里的 `summary` 和任何未就绪项。路径交接只登记工具。用户提出生成或编辑素材的需求后，再执行相应功能。

1. 找到本目录的绝对路径。用可用的 Python 3.10+ 运行 `python -B "<ComfyFlash>/engine/agent.py" register`。
2. 有明确的用户项目根目录时，追加 `--project "<项目根目录>"`。Codex 使用默认设置，Claude Code 追加 `--agent claude-code`。独立对话省略 `--project`。当前对话保存返回的 `play_argv`，读取 `guide`，后续处理本对话或同一项目的相关需求时直接选用已就绪的功能。
3. 注册报告由实际模型文件与 Python 检查生成。缺配置或有多套安装时，按 [README 的首次配置方式](README.md#首次配置)处理。生成时在 `play_argv` 末尾追加 `--request` 和请求 JSON 的绝对路径。

维护项目时，用 `python -B -m unittest discover -s engine/tests` 检查登记流程。提交到 GitHub 的内容使用可移植示例配置。
