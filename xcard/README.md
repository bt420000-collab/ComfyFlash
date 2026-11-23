# XCard 本地扩展

XCard 让 Agent 发现独立的制作工具，用于镜头组织、素材管理或媒体处理。工具在各自的项目中开发、安装和发布，ComfyFlash 提供本地目录发现接口。

把已安装工具的目录放在 `xcard/<工具名>/`，然后运行：

```text
python -B engine/agent.py xcard list
python -B engine/agent.py xcard show MyTool
```

`list` 列出直接子目录，`show` 返回工具路径和一级条目。可用 `--directory` 指定其他本地扩展目录。发现过程只读目录结构。工具的说明、入口和依赖由工具自身提供，Agent 按需读取，并在用户授权的范围内运行工具。

`xcard/` 下的本地工具目录由 Git 忽略，ComfyFlash 发布只包含发现接口。工具可使用独立运行环境，也可通过公开的 API 调用 ComfyFlash。[调用接口](../docs/API.md)
