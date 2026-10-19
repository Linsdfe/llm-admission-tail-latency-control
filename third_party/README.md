# 引擎依赖

首轮在 `llama.cpp/` 获取并检出选定的完整提交 SHA，在 `docs/environment.md` 和每次运行清单登记。当前未添加 submodule；本地检出默认被 `.gitignore` 忽略。

不要将未固定的最新分支当成正式实验版本。修改后相对固定提交导出 patches，复现时检查应用顺序与工作区差异。

## 当前固定版本（2026-10-19）

| 项目 | 值 |
|---|---|
| 版本 tag | **v0.7.0** |
| 完整提交 SHA | `620d2225cc436ab0b001e3200e590e3b286ef9ab` |
| 获取方式 | `git clone --depth 1 --branch v0.7.0`（浅克隆，SHA 完整） |
| Windows 副本 | `third_party/llama.cpp/`（169.7 MB；源码检索用，不入 git） |
| WSL 副本 | `~/projects/llm-admission-tail-latency-control/third_party/llama.cpp/`（编译与实验用） |
| 记录文件 | `results/raw/prestudy/2026-10-19-build/clone-record.md` |

构建命令、构建开关、产物路径与耗时在首轮构建完成后登记到 `docs/environment.md`。升级引擎版本须重建基线，旧结果保留。
