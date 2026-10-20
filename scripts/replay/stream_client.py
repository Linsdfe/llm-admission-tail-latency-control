#!/usr/bin/env python3
"""llama-server 流式请求客户端：逐块时间戳 + 单请求记录。

提供 do_request() 供驱动脚本调用；也可单独用它发一条请求。

计时口径（均为客户端单调时钟 time.monotonic()，单位 ms）：
- t_send            发起 HTTP 请求前
- ttfb_ms           收到响应头/首个字节（≈ curl 的 time_starttransfer，**不是 TTFT**）
- ttft_ms           收到首个**非空生成内容**（排除元数据块、空 delta、心跳）
- total_ms          收到 [DONE] 或连接结束
- inter_token_ms    相邻内容块到达间隔的分布（不含首块）
- server_overhead_ms  ttft_ms - 服务端 timings.prompt_ms（交付段 + 客户端开销，粗估）

仅用标准库，无第三方依赖。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

CONTENT_PREFIX = "data:"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _percentile(values, p):
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    k = (len(ordered) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * frac)


def _round(value, ndigits=3):
    return None if value is None else round(value, ndigits)


def do_request(base_url, prompt, max_tokens, temperature=0.0, timeout=300.0, metadata=None):
    """发送一次流式 chat completion，返回记录 dict（不含落盘）。"""
    url = base_url.rstrip("/") + "/v1/chat/completions"
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "stream": True,
    }
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    rec = dict(metadata or {})
    rec.update({
        "prompt": prompt,
        "prompt_chars": len(prompt),
        "prompt_sha256": sha256_text(prompt),
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "stream": True,
    })

    t_send = time.monotonic()
    t_send_unix_ms = time.time() * 1000.0
    first_content_t = None
    first_content_sse_index = None
    content_parts = []
    chunk_series = []
    finish_reason = None
    timings = None
    n_sse_json = 0
    stream_done = False
    ttfb = None
    end_t = None
    status = None
    error = None

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            ttfb = time.monotonic() - t_send
            for raw in resp:
                now = time.monotonic()
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith(CONTENT_PREFIX):
                    continue
                data = line[len(CONTENT_PREFIX):].strip()
                if data == "[DONE]":
                    stream_done = True
                    end_t = now
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                n_sse_json += 1
                choice = (obj.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    if first_content_t is None:
                        first_content_t = now
                        first_content_sse_index = n_sse_json - 1
                    content_parts.append(piece)
                    chunk_series.append({
                        "i": len(chunk_series),
                        "t_ms": round((now - t_send) * 1000.0, 3),
                        "chars": len(piece),
                    })
                if choice.get("finish_reason") is not None:
                    finish_reason = choice["finish_reason"]
                if obj.get("timings"):
                    timings = obj["timings"]
    except urllib.error.HTTPError as exc:
        status = exc.code
        error = "HTTPError %s: %s" % (exc.code, exc.read()[:200])
    except Exception as exc:  # noqa: BLE001 - 记录所有异常，不中断整批
        error = "%s: %s" % (type(exc).__name__, exc)
    finally:
        if end_t is None:
            end_t = time.monotonic()

    itls = []
    if len(chunk_series) >= 2:
        for prev, cur in zip(chunk_series, chunk_series[1:]):
            itls.append(cur["t_ms"] - prev["t_ms"])

    content = "".join(content_parts)
    ttft_ms = _round((first_content_t - t_send) * 1000.0) if first_content_t else None
    prompt_ms = None
    if isinstance(timings, dict):
        prompt_ms = timings.get("prompt_ms")
    rec.update({
        "http_status": status,
        "error": error,
        "client": {
            "clock": "time.monotonic()（客户端单调时钟），单位 ms",
            "t_send_unix_ms": round(t_send_unix_ms, 3),
            "t_send_monotonic_ms": t_send * 1000.0,
            "t_first_content_monotonic_ms": (
                first_content_t * 1000.0
                if first_content_t is not None else None
            ),
            "t_end_monotonic_ms": end_t * 1000.0,
            "stream_done": stream_done,
            "ttfb_ms": _round(ttfb * 1000.0),
            "ttft_ms": ttft_ms,
            "total_ms": _round((end_t - t_send) * 1000.0),
            "first_content_sse_index": first_content_sse_index,
            "n_sse_json_chunks": n_sse_json,
            "n_content_chunks": len(chunk_series),
            "content_chars": len(content),
            "server_overhead_ms": _round(ttft_ms - prompt_ms) if (ttft_ms is not None and prompt_ms is not None) else None,
            "inter_token_ms": {
                "n": len(itls),
                "mean": _round(statistics.fmean(itls)) if itls else None,
                "p50": _round(_percentile(itls, 50)),
                "p95": _round(_percentile(itls, 95)),
                "max": _round(max(itls)) if itls else None,
            },
        },
        "server": {
            "finish_reason": finish_reason,
            "timings": timings,
        },
        "content": content,
        "chunk_series": chunk_series,
    })
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description="发一条流式请求并打印计时")
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--max-tokens", type=int, default=64)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--out", default=None, help="可选：把完整记录写成 JSON")
    ap.add_argument("--req-id", default="adhoc")
    ap.add_argument("--category", default="adhoc")
    args = ap.parse_args(argv)

    rec = do_request(
        args.base_url, args.prompt, args.max_tokens, args.temperature, args.timeout,
        metadata={"req_id": args.req_id, "category": args.category},
    )
    print(json.dumps({k: rec[k] for k in ("req_id", "category", "http_status", "error", "client", "server")},
                     ensure_ascii=False, indent=2))
    print("content:", rec["content"][:200])
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=2)
    return 0 if rec["error"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
