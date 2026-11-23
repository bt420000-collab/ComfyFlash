# ComfyFlash RSI

RSI 是 ComfyFlash 的独立本地经验记忆模块。它记录推理任务结果，也允许正在使用 ComfyFlash 的 AI Agent 对引擎留下点评，供后续针对性优化。

## 设计原则

- 独立于 `engine/`，不把复盘、点评和递归改进逻辑塞进推理核心。
- SQLite 使用 Python 标准库，无额外数据库依赖。
- 不启动数据库服务、守护进程、后台线程或定时任务。
- 每次 API 操作打开短连接、完成短事务后立即关闭。
- WAL + busy timeout 支持多个 GPU Worker 进程在任务结束时追加记录。
- RSI 写入失败不得影响已经完成或正在进行的生成。
- 提示词正文不自动入库，仅保存 SHA-256 和字符数。
- 点评是观察证据，不代表系统自动修改代码、模型配方或生产策略。

## 数据库

默认路径：`RSI/data/comfyflash.sqlite3`。首次发生有效推理记录或主动查询/写入时创建。

当前表：

- `runs`：每次有效执行的成功/失败记录、Tone、设备标签、请求规格、耗时、输出路径、错误信息和参数/元数据摘要。
- `agent_feedback`：Agent/Human/System 点评，含观察、影响、建议、分类、严重程度、状态、关联 `run_id`、素材和额外上下文。

数据库版本通过 SQLite `PRAGMA user_version` 管理。运行数据文件被 `.gitignore` 忽略，RSI 源码与文档仍正常进入 Git。

## Agent 点评

一个独立、可复用的问题或改进机会记一条。建议包含：

1. 标题：简短、可检索。
2. observation：实际观察与复现条件。
3. impact：影响的耗时、稳定性、质量或 Agent 使用体验。
4. suggestion：值得验证的改进方向。
5. run_id / tone / artifact：可关联时尽量提供。

类别：`bug`、`optimization`、`performance`、`hardware`、`quality`、`workflow`、`api`、`compatibility`、`idea`、`other`。
严重程度：`info`、`low`、`medium`、`high`、`critical`。
状态：`open`、`triaged`、`planned`、`in_progress`、`resolved`、`wont_fix`、`duplicate`。

## CLI

```text
python -B engine/agent.py rsi note \
  --title "显存调度有优化空间" \
  --observation "同类任务在 GPU B 上的失败率明显更高，需核对当时空闲显存" \
  --category hardware --severity medium --author codex \
  --tone t2 --run-id "<run_id>" \
  --impact "可能导致视频任务重试和额外耗时" \
  --suggestion "按模型、帧数和设备建立历史 profile"

python -B engine/agent.py rsi list --status open --category hardware
python -B engine/agent.py rsi show <feedback_id>
python -B engine/agent.py rsi update <feedback_id> --status planned --resolution "安排后续验证"
python -B engine/agent.py rsi runs --tone t2
python -B engine/agent.py rsi summary
```

可用 `--db <path>` 指定另一份数据库，便于测试、项目隔离或备份。

## Python API

```python
from RSI import RSIStore

store = RSIStore()  # 初始化 SQLite schema，不保留连接
note = store.add_feedback(
    title="Agent 发现的优化点",
    observation="事实、复现条件和证据",
    author="codex",
    category="optimization",
    severity="medium",
    run_id="<run_id>",
    tone="t2",
    suggestion="下一步值得验证的方向",
)
```

也可通过 `with Flash() as flash:` 的 `flash.rsi` 属性访问相同 API。每次有效 `Flash.play()` 会自动写入一条运行记录，并在返回值中附加 `run_id`。

## 配置

在 `models/config.json` 可选设置：

```json
{
  "rsi_enabled": true,
  "rsi_db_path": "RSI/data/comfyflash.sqlite3"
}
```

`rsi_enabled: false` 只会关闭 `Flash.play()` 自动写入运行记录，不会关闭 Agent 手动点评；独立 RSI CLI 和 `flash.rsi` 仍可按需访问数据库。`rsi_db_path` 相对 ComfyFlash 项目根目录解析，也可以使用绝对路径。

## 当前边界

当前实现提供运行记录与 Agent 点评的存取、筛选和状态更新。自动分析、改进提案、对照实验、策略版本采纳/回滚属于后续 RSI 阶段，不能仅凭点评自动改动生产代码。
