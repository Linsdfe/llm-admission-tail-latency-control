#!/usr/bin/env python3
"""List dated source changes without duplicating raw evidence inventories."""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess


def generate(root, day):
    root = root.resolve(strict=True)
    tz = timezone(timedelta(hours=8))
    start = datetime.strptime(day, "%Y%m%d").replace(tzinfo=tz)
    end = start + timedelta(days=1)
    names = subprocess.check_output(["git", "-C", str(root), "-c", "safe.directory=*", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]).decode("utf-8").split("\0")
    rows = []
    for name in sorted(set(n for n in names if n)):
        path = root / name
        if not path.is_file() or path.suffix == ".docx" or path.name == "产出清单.md" or any(part in (".git", "__pycache__") for part in path.parts):
            continue
        if name.startswith(("results/raw/", "results/archives/")):
            continue
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, tz)
        if start <= modified < end:
            rows.append(f"| `{name}` | {stat.st_size} | {modified:%Y-%m-%d %H:%M:%S} |")
    target = root / "daily" / day / "产出清单.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join([f"# {start:%Y-%m-%d} 当天产出清单（自动生成）", "",
        "> 由 scripts/ops/eod.sh 调用 day_inventory.py 生成；请勿手工编辑。",
        "> 按 Asia/Shanghai 当日修改时间列 Git 已跟踪及未忽略的源文件；不重复列 raw/archives、Word 或自身。",
        "> 原始运行、失败尝试与日期包见同目录实验结果索引；删除和融合见当天记录及 Git 差异。",
        f"> 生成时间：{datetime.now(tz).isoformat()}", "", "| 文件 | 字节 | 修改时间 |", "|---|---:|---|", *rows, "", f"合计 **{len(rows)}** 个源文件。", ""]), encoding="utf-8")
    print(f"Generated daily/{day}/产出清单.md: {len(rows)} source files")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--date", required=True)
    a = p.parse_args()
    generate(a.project, a.date)
