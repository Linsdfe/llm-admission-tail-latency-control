#!/usr/bin/env python3
"""开放到达调度干跑：模拟响应，不发送 HTTP 请求。"""
import argparse
import asyncio
import json
import math
import time
from pathlib import Path


def load_trace(path):
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError("轨迹不能为空")

    ids = [row["req_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("请求 ID 重复")

    offsets = [row["arrival_offset_ms"] for row in rows]
    if any(
        not isinstance(t, (int, float))
        or not math.isfinite(t)
        or t < 0
        for t in offsets
    ):
        raise ValueError("到达偏移必须是非负有限数值")

    if offsets != sorted(offsets):
        raise ValueError("轨迹必须按到达偏移排序")
    return rows


async def replay(rows, response_ms):
    # 同一进程、同一单调时钟；预留 100 ms 启动准备时间。
    origin = time.monotonic() + 0.1

    async def simulate(row):
        started = time.monotonic()
        dispatch_ms = (started - origin) * 1000
        planned_ms = row["arrival_offset_ms"]

        print(
            f'START {row["req_id"]} '
            f'planned={planned_ms:7.1f} ms '
            f'dispatch={dispatch_ms:7.1f} ms '
            f'lag={dispatch_ms - planned_ms:6.1f} ms',
            flush=True,
        )

        # 模拟响应尚未完成；sleep 会让出执行权。
        await asyncio.sleep(response_ms / 1000)

        ended_ms = (time.monotonic() - origin) * 1000
        print(
            f'END   {row["req_id"]} end={ended_ms:7.1f} ms',
            flush=True,
        )
        return {
            "req_id": row["req_id"],
            "category": row["category"],
            "simulation": True,
            "planned_offset_ms": planned_ms,
            "dispatch_offset_ms": dispatch_ms,
            "dispatch_lag_ms": dispatch_ms - planned_ms,
            "end_offset_ms": ended_ms,
        }

    tasks = []
    for row in rows:
        deadline = origin + row["arrival_offset_ms"] / 1000
        await asyncio.sleep(max(0, deadline - time.monotonic()))

        # 创建任务后立即继续调度，不等待这个请求完成。
        tasks.append(asyncio.create_task(simulate(row)))

    # 所有计划请求启动后，再等待全部模拟响应结束。
    return await asyncio.gather(*tasks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--response-ms", type=float, default=2000)
    args = parser.parse_args()

    if not math.isfinite(args.response_ms) or args.response_ms <= 0:
        parser.error("--response-ms 必须是正的有限数值")

    rows = load_trace(args.trace)
    args.out_dir.mkdir(parents=True, exist_ok=False)

    records = asyncio.run(replay(rows, args.response_ms))
    output = args.out_dir / "dispatch.jsonl"
    with output.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    metadata = {
        "simulation": True,
        "purpose": "open_arrival_scheduler_sanity",
        "trace": str(args.trace.resolve()),
        "requests": len(records),
        "simulated_response_ms": args.response_ms,
        "clock": "time.monotonic",
        "time_unit": "ms",
    }
    (args.out_dir / "run-meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\n完成：{len(records)} 条模拟请求；记录：{output}")


if __name__ == "__main__":
    main()
