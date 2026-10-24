#!/usr/bin/env python3
"""File-only paired description of the predeclared output-budget sentinels.

This script never imports a collector, uses a GPU, or assigns semantic quality.
The separately written assistant review must read all sixteen complete outputs.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")


def repetitions(content):
    text = "".join(content.split())
    values = []
    for width in (4, 8, 16):
        frequencies = Counter(text[start:start + width] for start in range(max(0, len(text) - width + 1)))
        windows = sum(frequencies.values())
        maximum = max(frequencies.values(), default=0)
        values.append({"n_characters": width, "windows": windows, "unique_ngrams": len(frequencies),
                       "max_occurrences": maximum,
                       "max_frequency_ratio": maximum / windows if windows else None,
                       "duplicate_excess_ratio": (windows - len(frequencies)) / windows if windows else None})
    return {"character_count_without_whitespace": len(text), "per_n": values,
            "max_ngram_frequency_ratio_across_n": max((v["max_frequency_ratio"] for v in values if v["max_frequency_ratio"] is not None), default=None)}


def analyze(run, out):
    run = run.resolve(strict=True)
    if out.exists():
        raise FileExistsError("Do not overwrite an existing report")
    status = load(run / "summary.json")
    if status.get("status") != "completed" or load(run / "batch-status.json") != status:
        raise ValueError("Completed, closed probe evidence is required")
    assert status["measured_completions"] == 16 and status["completion_requests"] == 18
    assert status["final_test_generated_or_read"] is False
    diagnostics = load(run / "diagnostics.json")
    assert len(diagnostics) == 16
    grouped = {}
    evidence = {"summary.json": sha(run / "summary.json"), "batch-status.json": sha(run / "batch-status.json"),
                "diagnostics.json": sha(run / "diagnostics.json"), "probe-plan.jsonl": sha(run / "probe-plan.jsonl")}
    for diagnostic in diagnostics:
        directory = run / diagnostic["directory"]
        rows = [json.loads(line) for line in (directory / "replay/client.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) == 1
        row = rows[0]
        assert row["terminal"] == "completed" and row["http_status"] == 200
        assert row["output_tokens"] == row["native_final"]["tokens_predicted"] == row["timings"]["predicted_n"]
        assert row["input_tokens"] == row["native_final"]["tokens_evaluated"] == row["new_prompt_tokens"] + row["cached_prompt_tokens"]
        assert row["native_final"]["truncated"] is False
        content = (directory / "output.txt").read_text(encoding="utf-8")
        assert content == row["content"] == diagnostic["content"]
        actual = repetitions(content)
        recorded = diagnostic["repetition"]
        assert actual["character_count_without_whitespace"] == recorded["character_count_without_whitespace"]
        assert actual["max_ngram_frequency_ratio_across_n"] == recorded["max_ngram_frequency_ratio_across_n"]
        compact = "".join(content.split())
        for fresh, old in zip(actual["per_n"], recorded["per_n"]):
            assert all(old[key] == value for key, value in fresh.items())
            n, gram = old["n_characters"], old["most_frequent_ngram"]
            counted = sum(compact[start:start+n] == gram for start in range(max(0, len(compact)-n+1))) if gram is not None else 0
            assert counted == old["max_occurrences"]
        pair = grouped.setdefault(row["probe_source_case"], {})
        assert row["probe_level"] not in pair
        pair[row["probe_level"]] = {"req_id": row["req_id"], "source_req_id": row["probe_source_req_id"],
            "category": row["category"], "input_tokens": row["input_tokens"], "new_prompt_tokens": row["new_prompt_tokens"],
            "cached_prompt_tokens": row["cached_prompt_tokens"], "max_tokens": row["max_tokens"],
            "output_tokens": row["output_tokens"], "stop_type": row["native_final"]["stop_type"],
            "context_truncated": row["native_final"]["truncated"], "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "prompt_sha256": hashlib.sha256(row["prompt"].encode()).hexdigest(), "prompt": row["prompt"],
            "content": content, "repetition": actual, "source_directory": diagnostic["directory"],
            "semantic_quality": "not_automatically_assessed"}
        for path in (directory / "output.txt", directory / "diagnostic.json", directory / "replay/client.jsonl",
                     directory / "replay/requests" / (row["req_id"] + ".json")):
            evidence[path.relative_to(run).as_posix()] = sha(path)
    assert len(grouped) == 8
    for pair in grouped.values():
        assert set(pair) == {"low", "high"}
        assert pair["low"]["source_req_id"] == pair["high"]["source_req_id"]
        assert pair["low"]["prompt_sha256"] == pair["high"]["prompt_sha256"]
        assert pair["high"]["max_tokens"] == 2 * pair["low"]["max_tokens"]
    rows = [item for pair in grouped.values() for item in pair.values()]
    result = {"status": "passed_file_only_objective_recomputation", "run_id": run.name,
              "analyzed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
              "measured_completions": 16, "selected_prompts": 8, "warmups_separate": 2,
              "stop_types_by_level": {level: dict(Counter(pair[level]["stop_type"] for pair in grouped.values())) for level in ("low", "high")},
              "total_executed_output_tokens_by_level": {level: sum(pair[level]["output_tokens"] for pair in grouped.values()) for level in ("low", "high")},
              "objective_repetition_matches_collector_for_all_16": True,
              "context_truncations": sum(item["context_truncated"] for item in rows),
              "paired_text_relations": {case: {
                  "exact_content_equal": pair["low"]["content"] == pair["high"]["content"],
                  "high_text_starts_with_low_text": pair["high"]["content"].startswith(pair["low"]["content"])}
                  for case, pair in grouped.items()},
              "quality_verdict": None, "pairs": grouped,
              "scope": "Eight post-hoc selected sentinels; no overall quality rate, scheduler effect, or automatic semantic verdict.",
              "analysis_script_sha256": sha(Path(__file__))}
    out.mkdir(parents=True, exist_ok=False)
    dump(out / "summary.json", result)
    dump(out / "evidence-sha256.json", evidence)
    (out / "analysis-source.py").write_bytes(Path(__file__).read_bytes())
    lines = ["# 输出预算哨兵：客观记录", "", f"运行：`{run.name}`。8条后验选例、16次测量、2预热另计。所有重复指标由文件独立复算，未自动判定回答质量。", "",
             "|原case|类别|输入token|低/高上限|低/高实际输出|低/高停因|低/高8字符重复超额比率|", "|---|---|---:|---|---|---|---|"]
    for case, pair in sorted(grouped.items()):
        low, high = pair["low"], pair["high"]
        rates = [item["repetition"]["per_n"][1]["duplicate_excess_ratio"] for item in (low, high)]
        lines.append(f"|{case}|{'聊天' if low['category']=='interactive' else '后台'}|{low['input_tokens']}|{low['max_tokens']} / {high['max_tokens']}|{low['output_tokens']} / {high['output_tokens']}|{low['stop_type']} / {high['stop_type']}|{rates[0]:.3f} / {rates[1]:.3f}|")
    lines += ["", "重复超额比率为去空白后字符滑窗的(窗口数−唯一数)/窗口数，保留标点；长文本和合法列举也会影响此值，没有自动合格线。完整4/8/16字符指标、全文、缓存量和提示见summary.json。limit表示达到输出上限，eos表示自然结束，两者均不是语义质量判定。", "",
              "同一提示采用低→高、高→低交替顺序，但未消除服务缓存或时间影响。序列长度记录不作为性能重复；不与主校准、开发验证或旧2010条负载测量合并。", ""]
    with (out / "metrics.md").open("x", encoding="utf-8") as output:
        output.write("\n".join(lines))
    dump(out / "report-sha256.json", {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()})
    return {k: result[k] for k in ("status", "run_id", "measured_completions", "stop_types_by_level", "total_executed_output_tokens_by_level")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.run_dir, args.out_dir), ensure_ascii=False))
