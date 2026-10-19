#!/usr/bin/env python3
"""P0 冒烟 v2 请求驱动。

固定顺序（写入 client.jsonl，且每条请求独立文件 requests/<req_id>.json）：
  R00-warmup-1  预热 1：捕获 CUDA graph、拉升时钟
  R00-warmup-2  预热 2：确认稳态（预热不计入结果表）
  R01-interactive        交互类：短 prompt、max_tokens=64、全新 prompt
  R02-interactive-cache  与 R01 **完全相同**的 prompt，用于观察 prompt cache 影响（不作为独立样本）
  R03-background         后台类：长 prompt、max_tokens=384

约定：所有请求共用同一 chat 模板（Qwen2.5 默认 system 前缀约 24 token 必然命中缓存），
因此结果表同时报告 cache_n（命中 token 数）与 prompt_n（实际评估 token 数）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stream_client import do_request  # noqa: E402

REQUESTS = [
    {
        "req_id": "R00-warmup-1",
        "category": "warmup",
        "max_tokens": 8,
        "prompt": "请只回复两个字：收到",
        "note": "预热 1，不计入结果表",
    },
    {
        "req_id": "R00-warmup-2",
        "category": "warmup",
        "max_tokens": 8,
        "prompt": "请只回复两个字：好的",
        "note": "预热 2，不计入结果表",
    },
    {
        "req_id": "R01-interactive",
        "category": "interactive",
        "max_tokens": 64,
        "prompt": "用一句话解释什么是 LLM 推理的尾延迟。",
        "note": "交互类；该 prompt 首次出现",
    },
    {
        "req_id": "R02-interactive-cache",
        "category": "interactive-cache",
        "max_tokens": 64,
        "prompt": "用一句话解释什么是 LLM 推理的尾延迟。",
        "note": "与 R01 完全相同的 prompt，观察 prompt cache 影响",
    },
    {
        "req_id": "R03-background",
        "category": "background",
        "max_tokens": 384,
        "prompt": "请用约 300 字说明：在同一个 LLM 推理实例上混合处理交互请求与后台批处理任务时，为什么平均延迟不足以反映服务质量，应该关注哪些指标？",
        "note": "后台类；长 prompt 与长输出",
    },
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args(argv)

    req_dir = os.path.join(args.out_dir, "requests")
    os.makedirs(req_dir, exist_ok=True)
    jsonl_path = os.path.join(args.out_dir, "client.jsonl")

    with open(jsonl_path, "w", encoding="utf-8") as jsonl:
        for order, spec in enumerate(REQUESTS):
            meta = {
                "req_id": spec["req_id"],
                "category": spec["category"],
                "order": order,
                "note": spec["note"],
            }
            rec = do_request(
                args.base_url, spec["prompt"], spec["max_tokens"], args.temperature,
                args.timeout, metadata=meta,
            )
            path = os.path.join(req_dir, spec["req_id"] + ".json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, ensure_ascii=False, indent=2)

            slim = {k: rec[k] for k in ("req_id", "category", "order",
                                        "http_status", "error", "prompt_chars",
                                        "max_tokens", "client", "server")}
            slim["record_file"] = os.path.relpath(path, args.out_dir)
            jsonl.write(json.dumps(slim, ensure_ascii=False) + "\n")

            cli = rec["client"]
            srv = (rec["server"]["timings"] or {})
            print("%-22s %-17s status=%s ttft=%8s ms ttfb=%7s ms total=%9s ms chars=%4s "
                  "finish=%-4s cache_n=%3s prompt_n=%3s prompt_ms=%7s predicted_n=%s" % (
                      spec["req_id"], spec["category"], rec["http_status"],
                      cli["ttft_ms"], cli["ttfb_ms"], cli["total_ms"], cli["content_chars"],
                      rec["server"]["finish_reason"],
                      srv.get("cache_n"), srv.get("prompt_n"), srv.get("prompt_ms"),
                      srv.get("predicted_n"),
                  ), flush=True)

    print("已写入:", args.out_dir, "（逐请求明细在 requests/，汇总在 client.jsonl）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
