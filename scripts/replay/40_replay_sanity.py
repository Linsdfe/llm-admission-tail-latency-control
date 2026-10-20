#!/usr/bin/env python3
"""真实流式小轨迹核对；限定最多 10 条，不作为正式实验入口。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import runpy
import time

from stream_client import do_request


def save(path, obj):
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = ap.parse_args()

    # 复用上一小步的轨迹数量、ID 和时间顺序检查。
    helpers = runpy.run_path(
        str(Path(__file__).with_name("30_replay_dry_run.py"))
    )
    rows = helpers["load_trace"](args.trace)

    if len(rows) > 10:
        ap.error("本脚本仅用于最多 10 条请求的小轨迹核对")

    for row in rows:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", row["req_id"]):
            ap.error("req_id 只能包含字母、数字、下划线和短横线")
        if row["category"] not in ("interactive", "background"):
            ap.error("未知业务类别")
        if not isinstance(row["prompt"], str) or not row["prompt"].strip():
            ap.error("prompt 不能为空")
        if not isinstance(row["max_tokens"], int) or row["max_tokens"] <= 0:
            ap.error("max_tokens 必须为正整数")
        if row.get("temperature", 0.0) != 0.0:
            ap.error("本次小轨迹固定 temperature=0")

    request_dir = args.out_dir / "requests"
    request_dir.mkdir()  # 已存在则报错，避免覆盖旧请求文件。
    (args.out_dir / "trace.jsonl").write_bytes(args.trace.read_bytes())
    save(args.out_dir / "replay-meta.json", {
        "purpose": "real_http_sanity_not_performance_result",
        "simulation": False,
        "trace_sha256": hashlib.sha256(args.trace.read_bytes()).hexdigest(),
        "requests": len(rows),
        "workers": len(rows),
        "warmup_requests": 2,
        "clock": "time.monotonic",
        "time_unit": "ms",
        "socket_timeout_seconds": 30,
        "note": "Socket timeout is not a unified drain deadline.",
    })

    # 预热单独保存，不计入测量请求。
    for i in range(2):
        rec = do_request(
            args.base_url, "请回复：准备好了。", 16,
            timeout=30,
            metadata={"req_id": f"warmup-{i}", "category": "warmup"},
        )
        save(args.out_dir / f"warmup-{i}.json", rec)
        if (
            rec["error"] is not None
            or rec["http_status"] != 200
            or not rec["client"]["stream_done"]
            or rec["server"]["finish_reason"] not in ("stop", "length")
            or rec["client"]["ttft_ms"] is None
        ):
            raise RuntimeError("预热未正常完成，查看 warmup JSON")

    def worker(row, origin_ms):
        rec = do_request(
            args.base_url,
            row["prompt"],
            row["max_tokens"],
            temperature=row.get("temperature", 0.0),
            timeout=30,
            metadata={
                "req_id": row["req_id"],
                "category": row["category"],
            },
        )
        c = rec["client"]
        planned = row["arrival_offset_ms"]
        actual = c["t_send_monotonic_ms"] - origin_ms
        first = c["t_first_content_monotonic_ms"]

        rec["replay"] = {
            "planned_offset_ms": planned,
            "release_offset_ms": planned,  # 本轮没有外部准入闸门。
            "actual_send_offset_ms": actual,
            "send_lag_ms": actual - planned,
            "first_content_offset_ms": (
                first - origin_ms if first is not None else None
            ),
            "end_offset_ms": c["t_end_monotonic_ms"] - origin_ms,
        }
        save(request_dir / f'{row["req_id"]}.json', rec)
        print(
            f'{row["req_id"]} '
            f'lag={actual - planned:.2f} ms '
            f'TTFT={c["ttft_ms"]} ms '
            f'finish={rec["server"]["finish_reason"]} '
            f'error={rec["error"]}',
            flush=True,
        )
        return rec

    with ThreadPoolExecutor(max_workers=len(rows)) as pool:
        origin_ms = time.monotonic() * 1000 + 200
        futures = []
        for row in rows:
            deadline_ms = origin_ms + row["arrival_offset_ms"]
            # 按绝对计划时刻等待，避免逐次 sleep 累积漂移。
            while True:
                remaining = deadline_ms - time.monotonic() * 1000
                if remaining <= 0:
                    break
                time.sleep(remaining / 1000)

            futures.append(pool.submit(worker, row, origin_ms))

        # 所有请求提交后再收集；完成一条立即追加汇总记录。
        with (args.out_dir / "client.jsonl").open(
            "x", encoding="utf-8"
        ) as f:
            failed = []
            for future in as_completed(futures):
                rec = future.result()
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                if (
                    rec["error"] is not None
                    or rec["http_status"] != 200
                    or not rec["client"]["stream_done"]
                    or rec["server"]["finish_reason"] not in ("stop", "length")
                ):
                    failed.append(rec["req_id"])

    if failed:
        raise SystemExit("Failed sanity requests: " + ", ".join(failed))

    print(f"完成：{len(rows)} 条请求；目录：{args.out_dir}")


if __name__ == "__main__":
    main()
