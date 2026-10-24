#!/usr/bin/env python3
"""Deterministic, token-checked P1 calibration/development inputs.

The module deliberately has no final-test templates or generation path. Prompts
are explicit Qwen ChatML strings for /completion, not /chat/completions messages.
The tokenize callback must return actual token IDs using add_special=True and
parse_special=True, matching the fixed server's /completion implementation.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs/p1-development-workload-v1.json"
ALLOWED_SPLITS = ("calibration", "development")
SOURCE_ID = "project_original_synthetic_zh_v1"
TEMPLATE_VERSION = "p1_original_zh_v1"
SCENARIOS = ("main_bursty_mixed", "boundary_steady", "boundary_sparse")

# Families are separated by purpose and topic, not just by numeric seed.
FAMILIES = {
    "calibration": {
        "interactive": (
            ("library_lending", "借书期限还剩{a}天，计划每天读{b}页，请用两句话给出阅读安排。"),
            ("kitchen_scaling", "一份食谱需要{a}克配料，要做{b}份，请用两句话说明换算。"),
            ("activity_time", "活动准备需{a}分钟，休息{b}分钟，请用两句话安排先后顺序。"),
            ("stationery_budget", "文具预算{a}元，单件{b}元，请用两句话说明怎样避免超支。")),
        "background": (
            ("greenhouse_log", "温室观察记录", "温度", "湿度", "分析异常、补查依据和后续观察安排"),
            ("community_event", "社区活动筹备记录", "报名人数", "物资份数", "分析人员、物资和现场协调安排"),
            ("storage_inventory", "仓库存量盘点记录", "库存件数", "当天领用", "分析缺货风险、盘点顺序和补货安排"),
            ("training_diary", "运动训练日志", "训练分钟", "休息分钟", "分析训练节奏、休息和记录改进"))},
    "development": {
        "interactive": (
            ("bike_route", "骑行路线长{a}公里，中途休息{b}分钟，请用两句话提醒出发前的准备。"),
            ("pc_cooling", "电脑风扇噪声持续{a}分钟，桌边空隙{b}厘米，请用两句话给出检查顺序。"),
            ("study_break", "学习任务共{a}页，每轮休息{b}分钟，请用两句话安排专注与休息。"),
            ("laundry_plan", "有{a}件衣物，晾晒位置{b}个，请用两句话说明怎样分批处理。")),
        "background": (
            ("repair_ticket", "设备报修工单", "等待分钟", "影响人数", "分析处理顺序、验证步骤和进度通知"),
            ("course_feedback", "课程练习反馈", "已做题数", "待订正题数", "分析共性困难、订正安排和反馈方法"),
            ("parcel_dispatch", "包裹配送记录", "包裹件数", "剩余分钟", "分析路线安排、异常核对和交接记录"),
            ("service_incident", "服务运行记录", "请求件数", "重试次数", "分析排查顺序、观测指标和恢复确认"))}}


def sha_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_split(split):
    if split not in ALLOWED_SPLITS:
        raise ValueError("Only calibration and development are allowed; final-test generation is disabled")


def load_config(path=DEFAULT_CONFIG):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_candidates(split, seed, interactive_count=None, background_count=None, config=None):
    """Return deterministic descriptors; these do not yet claim token lengths.

    render_candidate(descriptor, detail_units) exposes the ordered length
    candidates. materialize_trace() selects using actual tokenizer IDs only.
    """
    _require_split(split)
    cfg = config or load_config()
    if isinstance(seed, bool) or not isinstance(seed, int) or seed not in cfg[split]["seeds"]:
        raise ValueError("Seed is not registered for this split")
    counts = {"interactive": interactive_count, "background": background_count}
    result = []
    prefix = "cal" if split == "calibration" else "dev"
    for category, override in counts.items():
        count = cfg[split][category + "_count"] if override is None else override
        if isinstance(count, bool) or not isinstance(count, int) or count < 0 or count > cfg[split][category + "_count"]:
            raise ValueError("Count must stay within the registered split budget")
        for index in range(count):
            rng = random.Random(f"{split}:{seed}:{category}:{index}")
            kind = "i" if category == "interactive" else "b"
            family = FAMILIES[split][category][index % len(FAMILIES[split][category])][0]
            content_id = f"{prefix}-{seed}-{kind}{index:03d}"
            # Rotate burst representatives through all length buckets, so the
            # predeclared sparse subset does not accidentally retain only short B.
            bucket = (index + index // 3) % 3 if category == "background" else index % 3
            result.append({"req_id": content_id, "content_id": content_id,
                           "category": category, "split": split, "seed": seed,
                           "category_index": index, "template_family": f"{split}_{family}",
                           "template_version": TEMPLATE_VERSION, "source_id": SOURCE_ID,
                           "input_token_bucket": bucket,
                           "input_token_range": deepcopy(cfg["input_token_buckets"][category][bucket]),
                           "max_tokens": cfg["max_output_tokens"][category],
                           "temperature": cfg["sampling"]["temperature"], "ignore_eos": False,
                           "case_a": rng.randint(12, 72), "case_b": rng.randint(2, 9),
                           "detail_seed": rng.randrange(10**6)})
    return result


def render_candidate(descriptor, detail_units):
    """Render one candidate, where detail_units is a pre-outcome length knob."""
    split, category = descriptor["split"], descriptor["category"]
    _require_split(split)
    if isinstance(detail_units, bool) or not isinstance(detail_units, int) or not 0 <= detail_units <= 128:
        raise ValueError("detail_units must be an integer from 0 to 128")
    index = descriptor["category_index"]
    family = FAMILIES[split][category][index % len(FAMILIES[split][category])]
    rng = random.Random(descriptor["detail_seed"])
    a, b = descriptor["case_a"], descriptor["case_b"]
    if category == "interactive":
        task = family[1].format(a=a, b=b)
        # The case identifier ensures independent synthetic content per seed.
        user = f"题号{descriptor['content_id']}。{task}"
        details = [f"补充{k+1}：可用时间{rng.randint(10, 90)}分钟，已有安排{rng.randint(1, 8)}项。"
                   for k in range(detail_units)]
        if details:
            user += "参考情况：" + "".join(details)
    else:
        user = (f"任务{descriptor['content_id']}。以下是虚构的{family[1]}。"
                f"请分六点{family[4]}，每点写出具体依据和行动。数字只用于本题，不补造事实。")
        details = [f"记录{k+1}：{family[2]}{rng.randint(12, 90)}，{family[3]}{rng.randint(1, 15)}。"
                   for k in range(detail_units)]
        if details:
            user += "记录如下：" + "".join(details)
        else:
            user += f"目前{family[2]}{a}，{family[3]}{b}。"
    return ("<|im_start|>system\n请用中文回答。<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n")


def materialize_trace(descriptors, tokenize, config=None):
    """Select input lengths using actual token IDs; never use output/performance.

    tokenize(prompt) must return a list of integer token IDs. Candidates increase
    by adding complete synthetic records. Binary search finds a bucket candidate;
    a skipped/infeasible range is an error, not permission to relax the bucket.
    """
    cfg = config or load_config()
    rows = []
    for item in descriptors:
        _require_split(item["split"])
        low, high = item["input_token_range"]
        cache = {}

        def measure(units):
            if units not in cache:
                prompt = render_candidate(item, units)
                token_ids = tokenize(prompt)
                if not isinstance(token_ids, list) or not token_ids or any(isinstance(t, bool) or not isinstance(t, int) for t in token_ids):
                    raise ValueError("Tokenizer must return nonempty actual integer token IDs")
                cache[units] = (prompt, token_ids)
            return len(cache[units][1])

        # Small buckets commonly fit the unextended prompt, saving many calls.
        if measure(0) >= low:
            units = 0
        else:
            left, right = 1, 1
            while measure(right) < low and right < 128:
                left, right = right + 1, min(128, right * 2)
            if measure(right) < low:
                raise ValueError(f"Input range unreachable for {item['req_id']}")
            while left < right:
                middle = (left + right) // 2
                if measure(middle) < low:
                    left = middle + 1
                else:
                    right = middle
            units = left
        prompt, tokens = cache[units]
        if not low <= len(tokens) <= high:
            raise ValueError(f"Input range infeasible for {item['req_id']}: {len(tokens)} not in {low}..{high}")
        if len(tokens) + item["max_tokens"] + cfg["input_plus_output_safety_tokens"] > cfg["slot_context_tokens"]:
            raise ValueError("Prompt plus output budget exceeds registered context safety check")
        row = {k: deepcopy(v) for k, v in item.items() if k not in ("case_a", "case_b", "detail_seed")}
        row.update(prompt=prompt, input_tokens=len(tokens), detail_units=units,
                   token_ids_sha256=sha_text(json.dumps(tokens, separators=(",", ":"))),
                   content_sha256=sha_text(prompt), prompt_format=cfg["prompt_format"],
                   tokenization=deepcopy(cfg["tokenizer_request"]),
                   arrival_offset_ms=item["category_index"] * 500 if item["category"] == "interactive" else item["category_index"] * 500 + 250)
        rows.append(row)
    ids = [r["req_id"] for r in rows]
    hashes = [r["content_sha256"] for r in rows]
    if len(ids) != len(set(ids)) or len(hashes) != len(set(hashes)):
        raise ValueError("Duplicate request identity or prompt content")
    return sorted(rows, key=lambda r: (r["arrival_offset_ms"], r["req_id"]))


def make_scenario(rows, scenario):
    """Copy development rows; main/steady differ only in arrival times."""
    if scenario not in SCENARIOS or not rows or any(r["split"] != "development" for r in rows):
        raise ValueError("A registered development scenario is required")
    if len([r for r in rows if r["category"] == "interactive"]) != 200 or len([r for r in rows if r["category"] == "background"]) != 30:
        raise ValueError("Development scenarios require the full 200 I + 30 B trace")
    result = []
    for source in rows:
        row = deepcopy(source)
        k = row["category_index"]
        if row["category"] == "interactive":
            row["arrival_offset_ms"] = 500 * k
        elif scenario == "main_bursty_mixed":
            row["arrival_offset_ms"] = 10000 * (k // 3) + 250 + 50 * (k % 3)
        elif scenario == "boundary_steady":
            row["arrival_offset_ms"] = 250 + 10000 * k / 3
        else:
            if k % 3:
                continue
            row["arrival_offset_ms"] = 250 + 10000 * (k // 3)
        result.append(row)
    return sorted(result, key=lambda r: (r["arrival_offset_ms"], r["req_id"]))


def http_tokenizer(base_url):
    if base_url.rstrip("/") not in ("http://127.0.0.1:8080", "http://localhost:8080"):
        raise ValueError("Only the registered local server is allowed")

    def tokenize(prompt):
        payload = json.dumps({"content": prompt, "add_special": True, "parse_special": True}, ensure_ascii=False).encode("utf-8")
        request = Request(base_url.rstrip("/") + "/tokenize", data=payload, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=15) as response:
            return json.load(response)["tokens"]
    return tokenize


def write_json(path, obj):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", required=True, choices=ALLOWED_SPLITS)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    descriptors = build_candidates(args.split, args.seed)
    if args.out_dir.exists():
        raise FileExistsError("Use a new output directory; previous evidence must not be overwritten")
    args.out_dir.mkdir(parents=True)
    write_json(args.out_dir / "candidate-descriptors.json", descriptors)
    rows = materialize_trace(descriptors, http_tokenizer(args.base_url))
    scenarios = {name: make_scenario(rows, name) for name in SCENARIOS} if args.split == "development" else {"calibration_serial": rows}
    for name, trace in scenarios.items():
        with (args.out_dir / f"{name}.jsonl").open("x", encoding="utf-8") as handle:
            for row in trace:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_json(args.out_dir / "input-manifest.json", {
        "status": "token_checked_development_only", "split": args.split, "seed": args.seed,
        "source_id": SOURCE_ID, "template_version": TEMPLATE_VERSION,
        "generation_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(DEFAULT_CONFIG.read_bytes()).hexdigest(),
        "tokenizer_request": {"add_special": True, "parse_special": True},
        "final_test_generated": False,
        "scenarios": {name: {"requests": len(trace), "trace_sha256": hashlib.sha256((args.out_dir / f"{name}.jsonl").read_bytes()).hexdigest()} for name, trace in scenarios.items()}})
    print(json.dumps({"status": "passed", "out_dir": str(args.out_dir), "scenarios": {name: len(trace) for name, trace in scenarios.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
