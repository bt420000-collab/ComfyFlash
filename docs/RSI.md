# RSI 本地运行历史与改进记录

RSI 在本机保存 ComfyFlash 的执行历史、Agent 点评和人工对照实验。在仓库根目录运行下文命令。媒体交付、设备选择和执行回执见 [调用与媒体交付](API.md)。

## 本机存储与配置

默认数据库是 `RSI/data/comfyflash.sqlite3`，详细遥测按次写到 `RSI/data/runs/<attempt_id>.jsonl`。SQLite 使用 Python 标准库，每次操作结束后关闭连接。同步 Python 调用在执行返回时提交。CLI 汇总结果后写一份 JSONL，并用一个事务提交 SQLite 摘要和可关联的运行记录。构造 `RSIStore`、注册、独立预检和 `play --dry-run` 都无需打开数据库。正常执行的 `play` 也会记录已经形成回执的失败，包括预检或设备准入失败。

在本机的 `models/config.json` 中可指定文件位置，这几个字段默认如下。

```json
{
  "rsi_enabled": true,
  "rsi_db_path": "RSI/data/comfyflash.sqlite3",
  "telemetry_enabled": true
}
```

引擎将相对的 `rsi_db_path` 按 ComfyFlash 根目录解析。可用环境变量 `COMFYFLASH_RSI_DB` 在配置未指定路径时切换数据库。自定义 JSONL 目录时可添加 `"telemetry_dir": "D:/ComfyFlash-local/runs"`，相对路径按调用进程的工作目录解析。RSI 按需读写这些本机文件，默认数据目录及 `*.sqlite3`、`*.sqlite3-*`、`*.sqlite3.*` 已由 Git 忽略。自定义 JSONL 路径也应放在 Git 工作区外或加入本地忽略规则。

将 `rsi_enabled` 设为 `false` 会跳过自动 SQLite 写入，返回 `RSI_DISABLED`，仍可手动访问历史库和写点评。将 `telemetry_enabled` 设为 `false` 会关闭 JSONL。两项均关闭时，回执仍可提供本次遥测。文件写入失败返回 `TELEMETRY_UNAVAILABLE`，数据库写入失败返回 `RSI_STORAGE_UNAVAILABLE`，媒体执行结果保持可用。自动写库的 SQLite 忙锁等待为 250 毫秒，手工访问默认 5 秒，使用 WAL。

## 记录与隐私

| 位置 | 保存内容 |
| --- | --- |
| JSONL | 每次尝试一行，保存设备信息、工作负载、模型指纹、阶段耗时、资源采样、文件引用、失败阶段、错误码及重试关系。显存和 RAM 为阶段采样，PyTorch 峰值覆盖自身分配器。 |
| `run_summaries` | 尝试 ID、请求 ID、Tone、扩散模型文件名、分辨率、请求时长、设备 UUID、工作负载与设备指纹、计时、资源峰值、验证状态、错误码、重试和文件引用。 |
| `runs` | 兼容既有查询和点评关联。保存 `run_id`、Tone、设备标签、输入图数量、种子、尺寸、时长、输出路径、错误码和最多 2,000 字符的错误消息。还保存模型、采样参数和环境摘要，以及可用的帧计数、技术验证、音轨和观察文件信息。 |
| `agent_feedback` | 人工填写的观察、影响、建议、作者、分类、状态、标签和上下文。可通过 `run_id` 关联 `runs`。 |
| `policy_versions`、`improvement_proposals`、`experiments` | 策略版本及父版本、对照实验、指标、证据路径、人工决议和回滚依据。 |
| `schema_migrations` | 已应用的存储版本和时间，与 SQLite `PRAGMA user_version` 核对。 |

自动运行记录的提示词专用字段只保存 SHA-256 和字符数。详细 JSONL 保存结构化失败信息，媒体旁车 `.meta.json` 含生成所用的提示词。分享数据库或日志前也要检查兼容表的错误消息、文件路径、设备标识、调用方的请求 ID，以及手工输入的点评和实验文字。图片、视频和模型权重通过文件引用追溯。

SQLite 成功提交后，执行结果的 `run_id` 对应同次终态尝试的 `attempt_id`。同一请求的早期失败和复跑过程可通过摘要与 JSONL 查看。旧库中已有的 `runs` 和 `agent_feedback` 继续通过原入口查询。

## 查历史与记录点评

`history` 查询 v0.2 的尝试摘要。可按 Tone、物理 GPU UUID、扩散模型文件名、分辨率、请求时长和策略版本精确过滤。时长使用请求秒数，例如 5 秒请求对齐到约 5.167 秒交付时，查询仍传 `--duration-s 5`。旧记录用 `rsi runs` 查询，`rsi summary` 汇总兼容运行记录和点评。

```powershell
python -B RSI/__main__.py history --tone t2 --resolution 768x448 --duration-s 5 --limit 10
python -B RSI/__main__.py history --policy-version engine-v0.2-p1
python -B engine/agent.py rsi runs --tone t2 --limit 10
python -B engine/agent.py rsi summary
```

点评可以记录一个可复现的引擎问题或一项可验证的优化机会。先用 `list --search` 查重复，再写事实、复现条件和影响。可以关联时，使用同一库中返回的 `run_id`。没有运行 ID 时可只用 `--task-id`、`--tone` 或 `--artifact` 提供上下文。保存后用返回的 `feedback_id` 查看或更新。

```powershell
python -B engine/agent.py rsi list --status open --search "解码显存"
python -B engine/agent.py rsi note --title "视频解码显存峰值偏高" --observation "关联运行在解码阶段 OOM" --category hardware --severity high --author codex --tone t2 --run-id "<run_id>" --suggestion "对照开启解码前卸载的显存与耗时" --tag video
python -B engine/agent.py rsi list --category hardware --run-id "<run_id>"
python -B engine/agent.py rsi show "<feedback_id>"
python -B engine/agent.py rsi update "<feedback_id>" --status planned --resolution "按设备与解码设置做配对测试"
```

`note` 接受 `--source agent|human|system`、`--impact`、可重复的 `--tag`、`--context-json` 和 `--context-file`。类别为 `bug`、`optimization`、`performance`、`hardware`、`quality`、`workflow`、`api`、`compatibility`、`idea`、`other`，严重程度为 `info`、`low`、`medium`、`high`、`critical`，状态为 `open`、`triaged`、`planned`、`in_progress`、`resolved`、`wont_fix`、`duplicate`。

`engine/agent.py rsi` 默认读取模型配置，可在子命令前加 `--config` 更换配置文件或用 `--db` 直接指定库。`RSI/__main__.py` 和直接构造的 `RSIStore()` 使用默认库或 `COMFYFLASH_RSI_DB`。如果生成配置用了自定义路径，查询同一个库时给独立 CLI 传 `--db`。

在 Python 中，可用 `RSIStore(path)` 显式选择数据库，也可从 `Flash` 实例的 `flash.rsi` 使用生成配置对应的数据库。

```python
from RSI import RSIStore

store = RSIStore()
history = store.query_history(tone="t2", resolution="768x448", duration_s=5, limit=10)
runs = store.list_runs(tone="t2", limit=10)
if runs:
    note = store.add_feedback(
        title="核对视频解码显存",
        observation="以关联运行的分辨率和时长复测解码阶段。",
        author="codex",
        category="hardware",
        run_id=runs[0]["run_id"],
        tone="t2",
    )
```

## 登记策略与对照实验

以下 Python 示例将配对结果登记到专用实验库。使用时替换示例指标和证据路径。对照时固定输入、seed、模型、LoRA、精度、画布、时长和采样设置，记录驱动、内存余量、并发和冷暖启动条件。可一起比较成功率、尾延迟、吞吐、资源峰值、交付有效性与用户要求的保留情况。对于质量评价，记录评审样本和判断依据。OOM 样本可用阶段和资源数据解释。

```python
from RSI import RSIStore

store = RSIStore("RSI/data/review-example.sqlite3")
store.save_policy_revision(version="decode-baseline", target="video_decode")
store.save_policy_revision(
    version="decode-offload", target="video_decode",
    parent_version="decode-baseline", rollback_to="decode-baseline",
)
store.add_proposal(
    proposal_id="decode-review", target="video_decode",
    baseline_policy_version="decode-baseline", candidate_policy_version="decode-offload",
    hypothesis="解码前卸载能降低配对测试的解码显存峰值。",
    test_set="review/paired-shots.json", metrics=["decode_vram_peak_gib"],
    rollback_to="decode-baseline",
)
store.record_experiment(
    experiment_id="decode-review-01", proposal_id="decode-review",
    test_set="review/paired-shots.json",
    baseline_metrics={"decode_vram_peak_gib": 14.9},
    candidate_metrics={"decode_vram_peak_gib": 4.0},
    evidence="review/decode-review-01.json", conclusion="样本中的解码显存峰值下降。",
)
store.record_proposal_decision("decode-review", decision="adopted", evidence="review/decision.md")
```

可通过 `query_policy_versions()`、`query_proposals()` 和 `query_experiments()` 回查。人工决议可登记为 `adopted`、`rejected` 或 `rolled_back`。采纳必须有已关联的实验和回滚版本。回滚须从已采纳状态发起，原采纳证据仍可查询。这里登记的是评审记录。引擎按现有配置和内置策略运行，自动写入执行摘要时会登记内置版本 `engine-v0.2-p1`。试验和实测见 [测试报告](TEST_REPORT.md)。

## 迁移、备份与恢复

本版 SQLite 存储结构为 v3。首次常规查询、写入或调用 `initialize()` 时创建空库或就地升级旧库。v1 迁移会保留原有 `runs`、`agent_feedback`、记录 ID 和点评关联，旧运行仍可通过 `rsi runs` 查询。迁移不会从旧兼容记录补造 v0.2 尝试摘要。高于当前支持版本的库会报告不兼容。

`check` 检查结构、迁移历史、数据库完整性及外键。可以在首次常规查询前对现有 v1 库执行 `check` 或 `backup`，两者无需升级源库。备份使用 SQLite 在线备份并验证新副本。恢复同样写到尚不存在的新路径，验证后可在模型配置中切换过去。

```powershell
python -B RSI/__main__.py --db RSI/data/comfyflash.sqlite3 check
python -B RSI/__main__.py --db RSI/data/comfyflash.sqlite3 backup D:/Backups/ComfyFlash-01.sqlite3
python -B RSI/__main__.py --db D:/Backups/ComfyFlash-01.sqlite3 restore D:/ComfyFlash-local/restored.sqlite3
python -B RSI/__main__.py --db D:/ComfyFlash-local/restored.sqlite3 check
```

SQLite 备份包含现有运行、点评、摘要和评审记录。需要保留原始遥测或产物时，一起备份引用的 JSONL、媒体、旁车及实验文件。若只剩 JSONL，可用 `engine.telemetry.summary(record, path)` 提取摘要，再用 `record_run_summaries(..., builtin_policy=...)` 按策略版本导入。摘要按 `attempt_id` 去重。兼容运行和手工点评需要从数据库备份恢复。
