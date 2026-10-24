#!/usr/bin/env python3
"""One predeclared prompt repair, with frozen v1 tasks/data and real token checks.

This module never consumes completions or exposes final-test generation. It
separates reference records from the task and repeats the requested answer form
at the end. Existing v1 prompts, sources and measured records remain untouched.
"""
from copy import deepcopy
import json
from pathlib import Path
import re

import p1_workload as v1

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs/p1-prompt-task-repair-v2.json"
TEMPLATE_VERSION = "p1_prompt_task_repair_v2"
VARIANT = "records_first_task_last_single_candidate"


def load_config(path=DEFAULT_CONFIG):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def extract_task_parts(row):
    """Parse only the known frozen v1 user text, without interpreting its data."""
    v1._require_split(row["split"])
    pattern = (r"<\|im_start\|>system\n.*?<\|im_end\|>\n"
               r"<\|im_start\|>user\n(.*?)<\|im_end\|>\n<\|im_start\|>assistant\n")
    match = re.fullmatch(pattern, row["prompt"], flags=re.DOTALL)
    if match is None:
        raise ValueError("Expected one complete frozen v1 ChatML prompt")
    user = match.group(1)
    category = row["category"]
    marker = "题号" if category == "interactive" else "任务"
    identity, separator, body = user.partition("。")
    if not separator or not identity.startswith(marker):
        raise ValueError("Missing frozen case identity")
    if category == "interactive":
        intro = ""
        task, separator, records = body.partition("参考情况：")
        if "请用两句话" not in task:
            raise ValueError("Expected the original two-sentence task")
        records = records if separator else ""
    elif category == "background":
        task, separator, records = body.partition("记录如下：")
        if not separator:
            task, separator, rest = body.partition("目前")
            records = "目前" + rest if separator else ""
        if not records or "请分六点" not in task:
            raise ValueError("Expected the original six-point task and source data")
        intro, separator, task_tail = task.partition("请分六点")
        task = separator + task_tail
    else:
        raise ValueError("Unknown business category")
    return {"identity": identity, "intro": intro, "task": task, "records": records,
            "source_prompt_sha256": v1.sha_text(row["prompt"]),
            "task_sha256": v1.sha_text(task), "records_sha256": v1.sha_text(records)}


def render_repaired_prompt(v1row):
    """Keep all original task/data characters; alter framing and their order only."""
    parts = extract_task_parts(v1row)
    if v1row["category"] == "interactive":
        system = "只用中文回答任务，不抄背景。"
        user = parts["identity"] + "。\n"
        if parts["records"]:
            user += "背景（不是题干）：\n[背景]\n" + parts["records"] + "\n[/背景]\n"
        user += "任务：" + parts["task"] + "\n只答两句。"
    else:
        system = "只依据资料回答，勿抄录；依据不足就说明，不补造事实。"
        user = (parts["identity"] + "。" + parts["intro"] + "\n[资料]\n" + parts["records"] + "\n[/资料]\n"
                "任务：" + parts["task"] + "\n"
                "资料无阈值或时间顺序时，不判断正常、异常或趋势。\n"
                "仅写六点，每点含依据和行动。")
    return ("<|im_start|>system\n" + system + "<|im_end|>\n"
            "<|im_start|>user\n" + user + "<|im_end|>\n<|im_start|>assistant\n")


def repair_metadata(v1row):
    parts = extract_task_parts(v1row)
    return {"prompt_repair_variant": VARIANT,
            "repair_source_prompt_sha256": parts["source_prompt_sha256"],
            "repair_task_sha256": parts["task_sha256"],
            "repair_records_sha256": parts["records_sha256"],
            "repair_original_task": parts["task"],
            "repair_original_intro": parts["intro"],
            "repair_original_records": parts["records"],
            "repair_uses_completion_or_answer": False}


def materialize_v2(descriptors, tokenize, config=None):
    """Choose record count only by actual input tokens in predeclared v1 buckets."""
    cfg = config or load_config()
    rows = []
    for item in descriptors:
        v1._require_split(item["split"])
        if item["seed"] not in cfg[item["split"]]["seeds"]:
            raise ValueError("Unregistered repair-pilot seed")
        low, high = item["input_token_range"]
        cache = {}

        def measure(units):
            if units not in cache:
                old_prompt = v1.render_candidate(item, units)
                original = dict(item, prompt=old_prompt)
                prompt = render_repaired_prompt(original)
                tokens = tokenize(prompt)
                if not isinstance(tokens, list) or not tokens or any(type(t) is not int for t in tokens):
                    raise ValueError("Tokenizer must return actual nonempty integer token IDs")
                cache[units] = (prompt, tokens, repair_metadata(original))
            return len(cache[units][1])

        if measure(0) >= low:
            units = 0
        else:
            left, right = 1, 1
            while measure(right) < low and right < 128:
                left, right = right + 1, min(128, right * 2)
            if measure(right) < low:
                raise ValueError("Input range unreachable: " + item["req_id"])
            while left < right:
                middle = (left + right) // 2
                if measure(middle) < low:
                    left = middle + 1
                else:
                    right = middle
            units = left
        prompt, tokens, metadata = cache[units]
        if not low <= len(tokens) <= high:
            raise ValueError(f"Repair input range infeasible for {item['req_id']}: {len(tokens)} not in {low}..{high}")
        if len(tokens) + item["max_tokens"] + cfg["input_plus_output_safety_tokens"] > cfg["slot_context_tokens"]:
            raise ValueError("Repaired prompt exceeds registered hard context budget")
        row = {k: deepcopy(value) for k, value in item.items() if k not in ("case_a", "case_b", "detail_seed")}
        row.update(prompt=prompt, input_tokens=len(tokens), detail_units=units,
                   source_template_version=row["template_version"], template_version=TEMPLATE_VERSION,
                   token_ids_sha256=v1.sha_text(json.dumps(tokens, separators=(",", ":"))),
                   content_sha256=v1.sha_text(prompt), prompt_format=cfg["prompt_format"],
                   tokenization=deepcopy(cfg["tokenizer_request"]),
                   arrival_offset_ms=item["category_index"] * 500 + (250 if item["category"] == "background" else 0),
                   **metadata)
        rows.append(row)
    if len({r["req_id"] for r in rows}) != len(rows) or len({r["content_sha256"] for r in rows}) != len(rows):
        raise ValueError("Duplicate repair request identity or content")
    return sorted(rows, key=lambda row: (row["arrival_offset_ms"], row["req_id"]))


materialize_trace = materialize_v2
