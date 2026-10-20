#!/usr/bin/env python3
"""Create a dated, verified local ZIP with run evidence and source snapshots.

Never moves, deletes or rewrites the source run. Archives remain local by default.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]


def archive(run):
    run = run.resolve()
    run.relative_to((ROOT / "results/raw").resolve())
    if not run.is_dir() or not (run / "manifest.json").is_file():
        raise ValueError("Expected an existing run directory with manifest.json")
    raw = sorted(p for p in run.rglob("*") if p.is_file())
    if any(p.is_symlink() for p in run.rglob("*")):
        raise ValueError("Run must not contain symlinks")
    # Capture execution sources only after verifying their recorded hashes.
    matched = []
    for line in (run / "code-sha256.txt").read_text(encoding="utf-8").splitlines():
        expected, recorded = line.split(maxsplit=1)
        source = ROOT / "scripts/replay" / Path(recorded.strip()).name
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Execution source changed; cannot claim exact snapshot: {source}")
        matched.append(source)
    support = [ROOT / name for name in [
        "scripts/replay/60_run_sanity.sh", "scripts/analysis/10_check_sanity.py",
        "scripts/ops/archive_run.py", "configs/run-manifest.template.json",
        "docs/environment.md", "docs/source-map.md",
    ]]
    entries = {f"{run.name}/raw/{p.relative_to(run).as_posix()}": p.read_bytes() for p in raw}
    entries.update({f"{run.name}/source/{p.relative_to(ROOT).as_posix()}": p.read_bytes() for p in matched + support})
    now = datetime.now(timezone(timedelta(hours=8)))
    day = re.sub(r"\D", "", run.name)[:8]
    datetime.strptime(day, "%Y%m%d")
    dest = ROOT / "results/archives" / day
    dest.mkdir(parents=True, exist_ok=True)
    zip_path = dest / f"{run.name}-{now.strftime('%H%M%S%f')}.zip"
    info = {"run_id": run.name, "archived_at": now.isoformat(), "raw_file_count": len(raw),
            "execution_sources_verified": [str(p.relative_to(ROOT)) for p in matched],
            "notes": "raw/ is evidence; source/ includes exact hash-verified execution sources plus current support tools and reference docs. Model/engine binaries are not embedded; use recorded hashes and source/build records. This is a local archive, not off-device backup."}
    entries[f"{run.name}/archive-info.json"] = (json.dumps(info, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    checksums = {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()}
    with ZipFile(zip_path, "x", compression=ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
        z.writestr(f"{run.name}/checksums.json", json.dumps(checksums, indent=2))
    with ZipFile(zip_path) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == len(entries) + 1
        for name, digest in checksums.items():
            assert hashlib.sha256(z.read(name)).hexdigest() == digest, name
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_suffix(".zip.sha256").write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    print(json.dumps({"archive": str(zip_path), "sha256": digest, "raw_files": len(raw), "zip_entries": len(entries) + 1, "verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    archive(ap.parse_args().run_dir)
