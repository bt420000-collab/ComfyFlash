# RSI 本地存储

ComfyFlash 在请求结束后写入一条小型摘要。批量调用由父进程在结果汇总后提交一个 SQLite 事务，worker 只把尝试结果交给父进程。同步 Python 调用在返回时记录自己的结果。注册、预检、构造 `RSIStore` 和采样循环均无需打开数据库。SQLite 使用 Python 标准库，连接按次关闭。

| 表 | 内容与关联 |
| --- | --- |
| `run_summaries` | 尝试 ID、请求 ID、Tone、模型、尺寸、请求秒数、设备指纹、计时、资源峰值、失败阶段、复跑状态和文件引用。可关联执行策略版本。 |
| `improvement_proposals` | 目标、假说、基线与候选版本、数据集引用、指标、决定和回滚版本。 |
| `experiments` | 关联提案，保存基线与候选指标、结论及原始证据路径。 |
| `policy_versions` | 版本、父版本、设备/模型范围、回滚版本和证据路径。 |
| `schema_migrations` | 已应用的迁移版本和时间，与 SQLite `user_version` 一致。 |

默认为 `RSI/data/rsi.sqlite3`，JSONL 默认写入 `RSI/data/runs/`。同一命令使用唯一 JSONL 文件，独立 CLI 并发时各写各的。可在 `models/config.json` 指定其他可写本地目录。

```json
{
  "rsi_enabled": true,
  "rsi_db_path": "D:/ComfyFlash-local/rsi.sqlite3",
  "telemetry_enabled": true,
  "telemetry_dir": "D:/ComfyFlash-local/runs"
}
```

单独设 `rsi_enabled:false` 会跳过 SQLite，仍可写 JSONL，并返回 `RSI_DISABLED`。设 `telemetry_enabled:false` 后可只保留 SQLite 摘要。两者均关闭时只在返回结果中提供本次遥测。目录不可写、SQLite 损坏或在忙锁预算内无法提交时，生成结果保留，并返回 `TELEMETRY_UNAVAILABLE` 或 `RSI_STORAGE_UNAVAILABLE`。保存失败后用返回文件和 JSONL 引用进行诊断。当前内置调度策略独立于历史数据库，SQLite 决议只记留痕。

## 查询与手工记录

```powershell
python -B RSI/__main__.py --db D:/ComfyFlash-local/rsi.sqlite3 history --tone t2 --resolution 768x448 --duration-s 5 --limit 10
python -B RSI/__main__.py --db D:/ComfyFlash-local/rsi.sqlite3 history --policy-version engine-v0.2-p1
```

还可加 `--device`，使用结果中的物理 GPU UUID，或 `--model`，使用权重文件名。筛选按请求时长精确匹配。Python API 包括 `RSIStore(path)`、`initialize()`、`query_history(...)`、`save_policy_revision(...)`、`add_proposal(...)`、`record_experiment(...)`、`record_proposal_decision(...)` 及对应的提案、实验和版本查询。正式执行首次写摘要时，在同一事务登记内置策略版本。需要重建历史时，可按 `attempt_id` 从 JSONL 重放摘要。重复写入同一尝试不会产生重复条目。

引擎摘要使用字段白名单。完整提示词、GPU 连续采样、媒体二进制、latent 和模型权重均在 SQLite 范围之外。JSONL 不保存提示词或原始异常全文。路径、调用方的 `request_id` 和手工填写的假说仍可能涉及项目隐私。媒体旁车有复现所需的有效提示词，分享前按项目规则处理。默认运行数据目录和 SQLite 常见主库、锁文件、备份后缀由 Git 忽略。

## 备份、升级与恢复

```powershell
python -B RSI/__main__.py --db D:/ComfyFlash-local/rsi.sqlite3 check
python -B RSI/__main__.py --db D:/ComfyFlash-local/rsi.sqlite3 backup D:/Backups/ComfyFlash-01.sqlite3
python -B RSI/__main__.py --db D:/Backups/ComfyFlash-01.sqlite3 restore D:/ComfyFlash-local/restored.sqlite3
python -B RSI/__main__.py --db D:/ComfyFlash-local/restored.sqlite3 check
```

备份使用 SQLite 在线备份 API，并在发布前校验结构与引用。恢复写到新路径，核验通过后可以把配置指向恢复库。首次操作初始化数据库，已有库按顺序迁移并保留记录。库版本高于程序支持时提示不兼容。默认使用 SQLite rollback journal，自动生成路径的写入锁等待为 250 毫秒，手工维护接口默认 5 秒。

Windows 临时目录实测，SQLite 3.50.4 单次事务中位数 4.03 毫秒，16 条摘要批写中位数 5.21 毫秒。四个独立进程共写入 640 条摘要，全部唯一。自动化另测试了忙锁超时和释放后多进程写入。试验方法见 [测试报告](CF_V02_TEST_REPORT.md)。
