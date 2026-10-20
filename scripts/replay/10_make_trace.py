#!/usr/bin/env python3
"""生成合成小轨迹，仅用于回放器正确性核对。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "workloads"


def request(req_id, category, offset_ms, prompt, max_tokens):
    return {
        "req_id": req_id,
        "category": category,
        "arrival_offset_ms": offset_ms,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{path.name}: {len(rows)} requests")


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    interactive = [
        request(
            f"I{i:03d}", "interactive", i * 500,
            f"任务编号 I{i:03d}。请用一句话解释什么是首 token 延迟。",
            64,
        )
        for i in range(6)
    ]

    background = [
        request(
            f"B{i:03d}", "background", 250 + i * 700,
            f"任务编号 B{i:03d}。请分点解释大语言模型推理中的"
            "输入处理、逐步生成、批处理与请求排队，尽量详细。",
            384,
        )
        for i in range(4)
    ]

    mixed = sorted(
        interactive + background,
        key=lambda row: (row["arrival_offset_ms"], row["req_id"]),
    )

    write_jsonl(OUT / "prestudy-sanity-e0.jsonl", interactive)
    write_jsonl(OUT / "prestudy-sanity-e1.jsonl", mixed)


if __name__ == "__main__":
    main()
