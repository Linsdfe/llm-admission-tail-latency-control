#!/usr/bin/env python3
"""Create a non-destructive, checksummed daily bundle for this LLM project."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

SOURCE_DIRS = ("docs", "scripts", "src", "include", "configs", "patches", "tests", "tools", "skills")
SUFFIXES = {".md", ".svg", ".py", ".sh", ".ps1", ".json", ".yaml", ".yml", ".toml", ".cpp", ".cc", ".c", ".hpp", ".h", ".cmake", ".txt"}
SKIP = {"__pycache__", ".venv", "node_modules", ".git", ".pytest_cache", ".document-work"}


def make_bundle(project, day, runs, extra):
    if len(day) != 8 or not day.isdigit():
        raise ValueError("--date must be YYYYMMDD")
    datetime.strptime(day, "%Y%m%d")
    root = project.resolve(strict=True)
    if "面向混合长短请求" not in (root / "README.md").read_text(encoding="utf-8"):
        raise ValueError("This skill only applies to the LLM admission/tail-latency project")
    if not (root / "docs/implementation-plan.md").is_file():
        raise ValueError("Missing project implementation plan")
    daily = root / "daily" / day
    if not (daily / "README.md").is_file():
        raise ValueError("Write the dated daily README before packaging")
    files = {}

    def add(path):
        rel = path.relative_to(root)
        if path.is_symlink() or any(part in SKIP for part in rel.parts):
            raise ValueError(f"Symlink/cache is not a bundle input: {rel}")
        path.resolve(strict=True).relative_to(root)
        if path.is_file():
            files[rel.as_posix()] = path

    for p in root.glob("*.md"):
        add(p)
    for name in ("CMakeLists.txt", ".gitattributes", ".gitignore"):
        if (root / name).is_file():
            add(root / name)
    for dirname in SOURCE_DIRS:
        for p in sorted((root / dirname).rglob("*")):
            if p.is_file() and p.suffix.lower() in SUFFIXES and not any(x in SKIP for x in p.relative_to(root).parts):
                add(p)
    for p in sorted(daily.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUFFIXES and p.name != "每日归档.json":
            add(p)
    selected_runs = []
    for item in runs:
        p = Path(item)
        p = p if p.is_absolute() else root / p
        if p.is_symlink():
            raise ValueError("Run directory cannot be a symlink")
        p = p.resolve(strict=True)
        p.relative_to((root / "results/raw").resolve())
        if not p.is_dir():
            raise ValueError("--run-dir must name a run directory")
        if len(p.relative_to(root / "results/raw").parts) < 2:
            raise ValueError("Select individual runs, not an entire raw stage")
        selected_runs.append(p.relative_to(root).as_posix())
        for f in sorted(p.rglob("*")):
            if f.is_symlink():
                raise ValueError(f"Run contains symlink: {f}")
            if f.is_file():
                add(f)
    for item in extra:
        p = Path(item)
        p = p if p.is_absolute() else root / p
        if p.is_symlink():
            raise ValueError("Extra artifact cannot be a symlink")
        p = p.resolve(strict=True)
        rel = p.relative_to(root)
        if not rel.parts or rel.parts[0] not in (*SOURCE_DIRS, "daily", "results"):
            raise ValueError("Extra artifacts must be project sources, records, or results")
        if rel.parts[0] == "results" and (len(rel.parts) < 2 or rel.parts[1] not in ("processed", "figures")):
            raise ValueError("Use --run-dir for raw data; do not recursively pack archives")
        for f in sorted(p.rglob("*")) if p.is_dir() else [p]:
            if f.is_symlink():
                raise ValueError(f"Extra artifact contains symlink: {f}")
            if f.is_file():
                add(f)

    now = datetime.now(timezone(timedelta(hours=8)))
    out = root / "results/archives" / day
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"daily-{day}-{now.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}.zip"
    inventory = []
    with ZipFile(target, "x", compression=ZIP_DEFLATED, allowZip64=True) as z:
        for rel, path in sorted(files.items()):
            before = path.stat()
            digest = hashlib.sha256()
            size = 0
            name = f"{day}/project/{rel}"
            with path.open("rb") as source, z.open(name, "w", force_zip64=True) as dest:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    dest.write(chunk)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"Input changed while packaging: {rel}; ZIP is not valid")
            inventory.append({"path": name, "size": size, "sha256": digest.hexdigest()})
        metadata = {"schema_version": 1, "project": "llm-admission-tail-latency-control", "date": day,
                    "packed_at": now.isoformat(), "run_dirs": selected_runs, "file_count": len(inventory),
                    "source_snapshot": "current_at_closeout_not_claimed_as_execution_version",
                    "backup_scope": "local_archive_not_off_device_backup", "files": inventory}
        z.writestr(f"{day}/MANIFEST.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    with ZipFile(target) as z:
        if z.testzip() is not None or len(z.namelist()) != len(inventory) + 1:
            raise RuntimeError("ZIP structure/CRC verification failed")
        for item in inventory:
            digest = hashlib.sha256()
            with z.open(item["path"]) as f:
                while chunk := f.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != item["sha256"]:
                raise RuntimeError(f"Checksum mismatch: {item['path']}")
    digest = hashlib.sha256()
    with target.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    receipt = {"date": day, "archive": target.relative_to(root).as_posix(), "sha256": digest.hexdigest(),
               "file_count": len(inventory), "run_dirs": selected_runs, "verified": True,
               "packed_at": now.isoformat(), "backup_scope": "local_only"}
    target.with_suffix(".zip.sha256").write_text(f"{receipt['sha256']}  {target.name}\n", encoding="utf-8")
    target.with_suffix(".receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (daily / "每日归档.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--run-dir", action="append", default=[])
    ap.add_argument("--include", action="append", default=[])
    args = ap.parse_args()
    print(json.dumps(make_bundle(args.project, args.date, args.run_dir, args.include), ensure_ascii=False))
