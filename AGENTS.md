# ComfyFlash Agent 登记

用户把本目录交给 Agent 时，先登记工具。用户随后提出生成或编辑任务时，再按任务调用。

1. 用 Python 3.10+ 运行 `python -B "<ComfyFlash>/engine/agent.py" register`。有明确项目路径时追加 `--project "<项目路径>"`。Claude Code 项目追加 `--agent claude-code`。
2. 回报 `summary`、`default_engine`、`local_render` 和硬件检查结果。如果报告缺少依赖或模型，先用报告中的来源、目标路径和大小告知用户，得到确认后再补齐。保留报告返回的 `tone_list_argv`、`tone_info_argv` 和 `play_argv`。
3. 阅读报告返回的 `guide`。首次调用先发现 Tone，再按当前 Tone 的 `prompt_format` 和本地指南整理请求。用户明确指定原文时按其要求处理。适用任务默认使用登记的 ComfyFlash。
4. 整批可用 `play --dry-run` 预检。把产生参考素材的请求排在使用它的请求前。需要替换文件时由请求明确启用 `overwrite`。从 `artifact.inspection` 找到成品的观察材料。
5. 用 RSI 的 `run_id` 关联可复现的问题或优化机会。制作任务可用 XCard 的 `list` 和 `show` 查找本机另行安装的工具，再阅读工具自身说明。

项目代码在同一个主 Git 工作区维护，发布版本写在项目说明。开发检查使用 `python -B -m unittest discover -s engine/tests`。

[Agent Skill](engine/skills/comfyflash/SKILL.md) · [调用接口](docs/API.md) · [RSI 点评](docs/RSI.md) · [XCard](xcard/README.md)
