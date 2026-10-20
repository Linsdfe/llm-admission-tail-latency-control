# 仓库运维脚本（scripts/ops/）

按功能划分的仓库级操作：同步、一致性检查、每日收尾、推送。与 `scripts/replay/`（负载回放与测量）分开。

| 脚本 | 作用 | 何时用 |
|---|---|---|
| `sync.sh` | 单向同步 Windows 主仓库 → WSL 工作副本（`docs/ daily/ configs/ scripts/ src/ include/ tests/ data/ tools/ patches/` + 根文件与 `*.md`/`*.docx`），并清 `__pycache__`；`results/` 与引擎检出不同步 | 改过文档/脚本/配置后 |
| `compare.sh` | 只读对比两端：10 个受管目录的差异数、根文件逐字节、设计差异（`.git`/模型/构建产物/证据）与体积 | 任何怀疑不同步时 |
| `eod.sh` | **每日收尾**：清临时文件与空目录 → 生成 `daily/<YYYYMMDD>/产出清单.md` → 同步 → 一致性检查 → 打印人工待办 | 每天工作结束 |
| `archive_run.py` | 按运行日期生成 ZIP、保存执行脚本快照、逐文件校验和 ZIP SHA256；不修改原始运行 | 每次证据核对完成后 |
| `push.sh` | **一键推送**到 GitHub：在 WSL 内临时借用 Windows 侧 SSH 私钥（用完即删），推送 `/mnt/c` 上的主仓库并校验本地/远端 HEAD 一致 | 提交后备份到远端 |

> 推送说明：Windows 侧 git 直连 GitHub 不通（需代理 + openssl 后端），WSL 网络可直连 GitHub 22 端口；认证私钥在 Windows。故由 `push.sh` 在 WSL 内完成，**临时私钥在退出时自动删除**。

## 每日收尾流程

```bash
# 1) 收尾（自动清理 + 生成清单 + 同步 + 检查）
bash scripts/ops/eod.sh
# 指定日期（补做某天）
bash scripts/ops/eod.sh 20261020

# 2) 人工补三项
#    - 填 daily/<日期>/README.md（人时/问题/下一步）
#    - daily/README.md 索引表加一行
#    - git 提交
```

设计原则：

- **工具按功能放**（`scripts/replay/` 测量、`scripts/ops/` 运维、`scripts/analysis/` 分析），不按日期堆放，避免同一工具产生多份副本。
- **日期文件夹只放"当天的记录与清单"**（`daily/<YYYYMMDD>/README.md` + 自动生成的 `产出清单.md`），不放文件副本，避免与功能目录重复。
- `eod.sh` 只删可安全重建的东西（`*.pid`、`*.tmp`、`*.bak`、`*.pyc`、`nohup.out`、`results/` 下空目录），**不碰证据文件**。
