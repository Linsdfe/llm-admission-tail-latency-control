#!/usr/bin/env python3
"""Analyze expanded calibration and fresh development verification, never schedule.

Predictor and primary threshold selection read calibration rows only. The fresh
development rows only assess the already selected artifact. No final data, GPU,
queue simulation, M/B3 decisions, or overall performance claims are produced.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
METHODS = ("median", "mean")
CLASSES = ("interactive", "background")
CAL_SEEDS = (11002, 11003, 11004, 11005, 11006)
# These are the P1 main arrival mix and bucket counts, not this balanced sample.
TARGET_COUNTS = {"interactive:0": 67, "interactive:1": 67, "interactive:2": 66,
                 "background:0": 10, "background:1": 10, "background:2": 10}
RESIDUAL_LEVELS = (.80, .95)
FIXED_MARGINS = (0, 32, 64, 128)
EPSILON = 1e-9


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jsonlines(path):
    return [json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines() if s.strip()]


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def artifact_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def dump(path, value):
    with Path(path).open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")


def cell(row):
    return f"{row['category']}:{row['input_token_bucket']}"


def q7(values, probability):
    if not values or not 0 <= probability <= 1:
        raise ValueError("Nonempty finite values and a probability are required")
    values = sorted(values)
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError("Nonfinite observation")
    position = (len(values) - 1) * probability
    low = int(position)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)


def weighted_quantile(values, weights, probability):
    """Smallest observation reaching p of mass; exact rational accumulation."""
    if len(values) != len(weights) or not values or not 0 <= probability <= 1:
        raise ValueError("Nonempty matching values/weights and probability required")
    pairs = []
    for value, weight in zip(values, weights):
        weight = weight if isinstance(weight, Fraction) else Fraction(str(weight))
        if weight < 0 or not math.isfinite(float(value)):
            raise ValueError("Negative weight or nonfinite value")
        if weight:
            pairs.append((value, weight))
    if not pairs:
        raise ValueError("Positive total weight required")
    pairs.sort(key=lambda pair: pair[0])
    threshold = Fraction(str(probability)) * sum(w for _, w in pairs)
    cumulative = Fraction(0)
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return pairs[-1][0]


def distribution(values):
    if not values:
        return {"n": 0, "min": None, "p50": None, "p95": None, "max": None, "mean": None}
    return {"n": len(values), "min": min(values), "p50": q7(values, .5),
            "p95": q7(values, .95), "max": max(values), "mean": statistics.mean(values)}


def group_rows(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[cell(row)].append(row)
    if set(groups) != set(TARGET_COUNTS):
        raise ValueError("All six registered class/input cells must be represented")
    return groups


def target_weights(rows):
    counts = Counter(cell(row) for row in rows)
    if set(counts) != set(TARGET_COUNTS):
        raise ValueError("Target weighting requires all six cells")
    return [Fraction(TARGET_COUNTS[cell(row)], 230 * counts[cell(row)]) for row in rows]


def target_mean(rows, values):
    if len(rows) != len(values):
        raise ValueError("Metric and row counts differ")
    return float(sum(w * float(v) for w, v in zip(target_weights(rows), values)))


def fit(rows, method):
    if method not in METHODS:
        raise ValueError("Unregistered point estimator")
    aggregate = statistics.median if method == "median" else statistics.mean
    model = {}
    for name, group in sorted(group_rows(rows).items()):
        limits = {row["max_tokens"] for row in group}
        if len(limits) != 1:
            raise ValueError("This version requires the registered constant limit per cell")
        model[name] = {"point_prediction": float(aggregate(row["output_tokens"] for row in group)),
                       "samples": len(group), "max_tokens": next(iter(limits))}
    return model


def predict(row, model):
    # Output caps are known at arrival; actual output is never read here.
    return min(float(row["max_tokens"]), max(0.0, model[cell(row)]["point_prediction"]))


def select_from_calibration(rows):
    """Five LOSO fits. This function has no development/validation argument."""
    seeds = sorted({row["seed"] for row in rows})
    if seeds != list(CAL_SEEDS) or any(row["split"] != "calibration" for row in rows):
        raise ValueError("Selection is restricted to the five registered calibration seeds")
    results = {}
    oof_predictions = {}
    for method in METHODS:
        folds, oof = [], {}
        for held_seed in seeds:
            train = [row for row in rows if row["seed"] != held_seed]
            test = [row for row in rows if row["seed"] == held_seed]
            assert len(train) == 96 and len(test) == 24
            model = fit(train, method)
            predicted = [predict(row, model) for row in test]
            loss = target_mean(test, [abs(row["output_tokens"] - p) for row, p in zip(test, predicted)])
            folds.append({"held_out_seed": held_seed, "train_seeds": [s for s in seeds if s != held_seed],
                          "train_requests": len(train), "held_out_requests": len(test),
                          "target_weighted_MAE": loss, "train_model": model})
            for row, p in zip(test, predicted):
                if row["req_id"] in oof:
                    raise ValueError("Calibration identity repeated across held-out folds")
                oof[row["req_id"]] = p
        results[method] = {"folds": folds,
                           "mean_fold_target_weighted_MAE": statistics.mean(f["target_weighted_MAE"] for f in folds)}
        oof_predictions[method] = oof
    median_loss = results["median"]["mean_fold_target_weighted_MAE"]
    mean_loss = results["mean"]["mean_fold_target_weighted_MAE"]
    chosen = "mean" if mean_loss < median_loss - EPSILON else "median"
    return {"method": chosen, "candidate_results": results,
            "criterion": "Mean of five leave-one-seed-out target-weighted MAEs; abs tie <=1e-9 selects median",
            "tie_epsilon": EPSILON}, oof_predictions


def error_metrics(rows, predicted):
    errors = [row["output_tokens"] - p for row, p in zip(rows, predicted)]
    positive = [max(0.0, e) for e in errors]
    return {"requests": len(rows), "MAE": statistics.mean(abs(e) for e in errors),
            "target_weighted_MAE": target_mean(rows, [abs(e) for e in errors]),
            "underprediction_count": sum(e > EPSILON for e in errors),
            "underprediction_fraction": sum(e > EPSILON for e in errors) / len(errors),
            "target_weighted_underprediction_fraction": target_mean(rows, [e > EPSILON for e in errors]),
            "signed_error_actual_minus_prediction": distribution(errors),
            "positive_residual": distribution(positive), "absolute_error": distribution([abs(e) for e in errors]),
            "point_at_output_cap": sum(abs(p - row["max_tokens"]) <= EPSILON for row, p in zip(rows, predicted)),
            "point_at_output_cap_fraction": statistics.mean(abs(p - row["max_tokens"]) <= EPSILON for row, p in zip(rows, predicted)),
            "point_headroom_to_output_cap": distribution([row["max_tokens"] - p for row, p in zip(rows, predicted)]),
            "actual_output_limit_hits": sum(row["finish_reason"] == "length" for row in rows)}


def local_error_metrics(rows, predicted):
    # Cell and seed tables cannot use target weights unless all cells exist.
    errors = [row["output_tokens"] - p for row, p in zip(rows, predicted)]
    return {"requests": len(rows), "MAE": statistics.mean(abs(e) for e in errors),
            "underprediction_fraction": statistics.mean(e > EPSILON for e in errors),
            "signed_error_actual_minus_prediction": distribution(errors),
            "absolute_error": distribution([abs(e) for e in errors]),
            "positive_residual": distribution([max(0.0, e) for e in errors]),
            "actual_output": distribution([row["output_tokens"] for row in rows]),
            "point_prediction": distribution(predicted),
            "point_at_output_cap": sum(abs(p - row["max_tokens"]) <= EPSILON for row, p in zip(rows, predicted)),
            "point_headroom_to_output_cap": distribution([row["max_tokens"] - p for row, p in zip(rows, predicted)]),
            "actual_output_limit_hits": sum(row["finish_reason"] == "length" for row in rows)}


def classification(rows, predicted, tau):
    confusion = Counter()
    business = {category: {"predicted_short": 0, "predicted_long": 0} for category in CLASSES}
    correct, false_short, false_long = [], [], []
    labels = []
    for row, p in zip(rows, predicted):
        pred_short = p <= tau
        actual_short = row["output_tokens"] <= tau
        confusion[f"actual_{'short' if actual_short else 'long'}_predicted_{'short' if pred_short else 'long'}"] += 1
        business[row["category"]]["predicted_short" if pred_short else "predicted_long"] += 1
        correct.append(pred_short == actual_short)
        false_short.append(pred_short and not actual_short)
        false_long.append(not pred_short and actual_short)
        labels.append(pred_short)
    short = sum(labels)
    return {"tau": tau, "requests": len(rows), "predicted_short": short, "predicted_long": len(rows) - short,
            "all_one_predicted_class": short in (0, len(rows)),
            "business_class_by_predicted_length_class": business,
            "same_as_interactive_short_background_long": all(label == (row["category"] == "interactive") for row, label in zip(rows, labels)),
            "confusion_actual_executed_length_vs_predicted_length": {name: confusion[name] for name in (
                "actual_short_predicted_short", "actual_short_predicted_long", "actual_long_predicted_short", "actual_long_predicted_long")},
            "accuracy": statistics.mean(correct), "target_weighted_accuracy": target_mean(rows, correct),
            "false_short_count": sum(false_short), "false_short_fraction_all_requests": statistics.mean(false_short),
            "false_long_count": sum(false_long), "false_long_fraction_all_requests": statistics.mean(false_long),
            "target_weighted_false_short_fraction_all_requests": target_mean(rows, false_short),
            "target_weighted_false_long_fraction_all_requests": target_mean(rows, false_long),
            "actual_target": "Observed executed length under the registered output cap; not unobserved uncapped natural length"}


def make_artifact(calibration):
    selection, oof = select_from_calibration(calibration)
    models = {method: fit(calibration, method) for method in METHODS}
    selected = selection["method"]
    thresholds = {"target_weighted_actual_q85": weighted_quantile(
                      [r["output_tokens"] for r in calibration], target_weights(calibration), .85),
                  "pooled_actual_type7_q85": q7([r["output_tokens"] for r in calibration], .85),
                  "fixed_128_sensitivity": 128}
    residual_tables = {}
    for method in METHODS:
        residual_tables[method] = {}
        for name, group in sorted(group_rows(calibration).items()):
            in_sample = [max(0.0, row["output_tokens"] - predict(row, models[method])) for row in group]
            crossfit = [max(0.0, row["output_tokens"] - oof[method][row["req_id"]]) for row in group]
            residual_tables[method][name] = {"in_sample": {"distribution": distribution(in_sample),
                    "q80": q7(in_sample, .80), "q95": q7(in_sample, .95)},
                "out_of_fold": {"distribution": distribution(crossfit), "q80": q7(crossfit, .80), "q95": q7(crossfit, .95)}}
    artifact = {"artifact_kind": "calibration_refinement_development_candidate_not_G1_frozen",
        "selection_uses": "New calibration seeds only; no fresh-development or previous-batch output used",
        "calibration_seeds": list(CAL_SEEDS), "calibration_requests": len(calibration),
        "target_counts_by_cell": TARGET_COUNTS, "target_total": 230,
        "point_estimator_selection": selection, "selected_point_estimator": selected,
        "candidate_models": models, "selected_model": models[selected],
        "thresholds": thresholds, "primary_threshold_name": "target_weighted_actual_q85",
        "primary_threshold_selected_before_development_evaluation": True,
        "weighted_quantile_definition": "Smallest observed length where cumulative target-mixture mass reaches p, exact rational weights",
        "residual_quantile_definition": "Empirical type7 positive-residual quantiles; OOF is the primary static-bound diagnostic, in-sample sensitivity separately reported",
        "residual_tables": residual_tables,
        "fixed_margin_sensitivities": list(FIXED_MARGINS),
        "out_of_sample_update": "Not implemented or simulated here; real M must only consume feedback already completed at its decision time",
        "coverage_scope": "Descriptive only, no conformal or unconditional tail/latency/capacity guarantee",
        "response_quality_scope": "Executed length, EOS and no context truncation do not establish correct, useful or instruction-complete answers; independent output-budget sentinels are analyzed separately",
        "mixture_caveat": "200/230 interactive mass exceeds .85, all interactive executed lengths <=64; target-weighted actual q85 is therefore <=64 and can make predicted classes coincide with business labels"}
    return artifact, oof


def evaluate(rows, method, model, thresholds, residual_table):
    predicted = [predict(row, model) for row in rows]
    metrics = error_metrics(rows, predicted)
    metrics["by_cell"] = {}
    metrics["by_seed"] = {}
    for name, group in sorted(group_rows(rows).items()):
        metrics["by_cell"][name] = local_error_metrics(group, [predict(row, model) for row in group])
    for seed in sorted({row["seed"] for row in rows}):
        group = [row for row in rows if row["seed"] == seed]
        metrics["by_seed"][str(seed)] = error_metrics(group, [predict(row, model) for row in group])
    metrics["classifications"] = {name: classification(rows, predicted, tau) for name, tau in thresholds.items()}
    metrics["bound_diagnostics"] = {}
    for source in ("out_of_fold", "in_sample"):
        source_metrics = {}
        for level in ("q80", "q95"):
            bounds = [min(row["max_tokens"], p + residual_table[cell(row)][source][level]) for row, p in zip(rows, predicted)]
            caps = [abs(bound - row["max_tokens"]) <= EPSILON for row, bound in zip(rows, bounds)]
            error = [row["output_tokens"] > bound + EPSILON for row, bound in zip(rows, bounds)]
            source_metrics[level] = {"at_output_cap_count": sum(caps), "at_output_cap_fraction": statistics.mean(caps),
                "bound_length": distribution(bounds),
                "headroom_to_output_cap": distribution([row["max_tokens"] - bound for row, bound in zip(rows, bounds)]),
                "target_weighted_at_output_cap_fraction": target_mean(rows, caps),
                "observed_bound_exceedance_count": sum(error), "observed_bound_exceedance_fraction": statistics.mean(error),
                "target_weighted_observed_bound_exceedance_fraction": target_mean(rows, error),
                "fixed_margin_bound_comparisons": {str(margin): {
                    "equal_length_bound_count": sum(abs(bound-min(row["max_tokens"], p+margin)) <= EPSILON for row,p,bound in zip(rows,predicted,bounds)),
                    "different_length_bound_count": sum(abs(bound-min(row["max_tokens"], p+margin)) > EPSILON for row,p,bound in zip(rows,predicted,bounds))}
                    for margin in FIXED_MARGINS}}
        metrics["bound_diagnostics"][source] = source_metrics
    metrics["point_estimator"] = method
    return metrics


def load_records(batch, split, expected_seeds, expected_count, evidence):
    records = []
    paths = sorted((batch / split).glob("seed*/c*/replay/client.jsonl"))
    if len(paths) != expected_count:
        raise ValueError(f"{split}: expected {expected_count} single-request records, found {len(paths)}")
    for path in paths:
        evidence[path.relative_to(batch).as_posix()] = sha(path)
        rows = jsonlines(path)
        if len(rows) != 1:
            raise ValueError("Serial trial must contain one request")
        row = rows[0]
        assert row["split"] == split and row["seed"] in expected_seeds
        assert row["terminal"] == "completed" and row["output_tokens_known"] is True
        assert row["native_final"]["truncated"] is False
        assert row["tokens_evaluated"] == row["input_tokens"] == row["new_prompt_tokens"] + row["cached_prompt_tokens"]
        assert row["output_tokens"] == row["native_final"]["tokens_predicted"] == row["timings"]["predicted_n"]
        assert 0 < row["output_tokens"] <= row["max_tokens"]
        assert row["input_tokens"] + row["max_tokens"] + 1 <= 2048
        records.append(row)
    assert len({row["req_id"] for row in records}) == len(records)
    assert set(row["seed"] for row in records) == set(expected_seeds)
    assert all(len(group) == 20 for group in group_rows(records).values())
    return records


def report_text(result):
    artifact = result["artifact"]
    selected = artifact["selected_point_estimator"]
    candidate = artifact["point_estimator_selection"]["candidate_results"]
    lines = ["# 扩充校准与新开发数据验证", "", f"批次：`{result['batch_id']}`。分析时间：{result['analyzed_at']}。", "",
        "本轮收集120条新校准和120条新开发验证，另2条预热。保持提示机制、输入桶、输出上限和自然结束设置，旧24条与旧2010条不并入训练。这里只检查长度估计、余量裁剪和长短分类，没有实现或模拟调度，没有M/B3性能结果。", "",
        "## 1. 预测器如何选，选出了什么", "",
        "候选只有每类×输入桶的中位数和均值。每次留出一个校准种子，用其余四个种子拟合，再按主场景200聊天、30后台及各输入桶比例计算绝对误差，平均五折风险后选择；差不超过1e-9时选中位数。新开发数据不参与选择。", "",
        "|候选|五折平均目标加权绝对误差（token）|", "|---|---:|",
        f"|中位数|{candidate['median']['mean_fold_target_weighted_MAE']:.6f}|",
        f"|均值|{candidate['mean']['mean_fold_target_weighted_MAE']:.6f}|", "",
        f"按事先规则选择：{'均值' if selected=='mean' else '中位数'}。不是为了刻意留出余量而压低预测。五折训练集有重叠，不把五折当作五次独立算法实验，也不给逐请求置信区间。", "",
        "|类别与输入桶|样本数|中位数预测|均值预测|实际输出触顶数|", "|---|---:|---:|---:|---:|"]
    for name in sorted(TARGET_COUNTS):
        med = artifact["candidate_models"]["median"][name]
        mean = artifact["candidate_models"]["mean"][name]
        hits = result["calibration"][selected]["by_cell"][name]["actual_output_limit_hits"]
        lines.append(f"|{name}|{med['samples']}|{med['point_prediction']:.3f}|{mean['point_prediction']:.3f}|{hits}|")
    lines += ["", "## 2. 新开发数据怎样验证", "",
        "新验证集按类别平衡采集，所以下表同时报告原始均值和主场景比例加权误差；两者不是同一个总体。这里的真实长度指输出上限内实际执行长度，不是被上限截住后不可见的完整自然长度。", "",
        "|候选|新验证MAE|主场景加权MAE|低估比例|点预测已达上限|", "|---|---:|---:|---:|---:|"]
    for method in METHODS:
        m = result["development_validation"][method]
        lines.append(f"|{method}{'（按校准选中）' if method==selected else '（敏感性对照）'}|{m['MAE']:.3f}|{m['target_weighted_MAE']:.3f}|{100*m['underprediction_fraction']:.1f}%|{m['point_at_output_cap']} / {m['requests']}|")
    selected_metrics = result["development_validation"][selected]
    lines += ["", "所选预测器的新开发误差分布（token）：", "",
              "|误差定义|最小值|中位数|95%分位|最大值|", "|---|---:|---:|---:|---:|"]
    for label, key in (("实际减预测（可为负）", "signed_error_actual_minus_prediction"),
                       ("只保留低估部分，其他记0", "positive_residual"), ("误差绝对值", "absolute_error")):
        values = selected_metrics[key]
        lines.append(f"|{label}|{values['min']:.3f}|{values['p50']:.3f}|{values['p95']:.3f}|{values['max']:.3f}|")
    lines += ["", "新验证即使更偏向另一个候选，也不在这里改选。两份数据模板家族分开，验证提示实例是新种子，但仍属已经公开方案下的开发数据，不是最终测试。", "",
        "## 3. 留出余量后是否仍被上限裁平", "",
        "使用留种子外预测的正残差，按每类×桶计算经验80%和95%分位，再与点预测相加并裁到用户上限。留种子外残差为主诊断，训练集内残差另列敏感性。它们没有逐请求覆盖或延迟保证。", "",
        "|所选预测器的新验证静态上界|达到输出上限|实际输出超过上界|与固定余量相同的上界数（0 / 32 / 64 / 128）|", "|---|---:|---:|---|"]
    chosen = result["development_validation"][selected]
    for q in ("q80", "q95"):
        b = chosen["bound_diagnostics"]["out_of_fold"][q]
        matches = " / ".join(str(b["fixed_margin_bound_comparisons"][str(m)]["equal_length_bound_count"]) for m in FIXED_MARGINS)
        lines.append(f"|{q}|{b['at_output_cap_count']} / {chosen['requests']}|{b['observed_bound_exceedance_count']} / {chosen['requests']}|{matches}|")
    lines += ["", "上表只比较同一条请求的静态长度上界是否相同，不是固定余量B3与在线余量M的接纳决定、等待或吞吐对照。点预测没有触顶，也可能在加误差余量后触顶；仍须G2/G3验证真实决策机会。", "",
        "## 4. 长短分界有没有退化", "",
        "主方案事先指定按主场景比例加权的实际输出85%分位，取累计权重首次达到85%的最小观测值；普通混合85%分位和固定128只作敏感性，不能按新验证表现挑弱最近邻。", "",
        "|分界方案|阈值token|预测短 / 长|是否全为一类|是否完全等于聊天短、后台长|误判短 / 误判长|", "|---|---:|---:|---|---|---:|"]
    for name, item in chosen["classifications"].items():
        lines.append(f"|{name}{'（主方案）' if name==artifact['primary_threshold_name'] else ''}|{item['tau']:g}|{item['predicted_short']} / {item['predicted_long']}|{'是' if item['all_one_predicted_class'] else '否'}|{'是' if item['same_as_interactive_short_background_long'] else '否'}|{item['false_short_count']} / {item['false_long_count']}|")
    primary = chosen["classifications"][artifact["primary_threshold_name"]]
    business = primary["business_class_by_predicted_length_class"]
    confusion = primary["confusion_actual_executed_length_vs_predicted_length"]
    lines += ["", "主方案的新开发交叉表：", "",
              "|业务类别|预测短|预测长|", "|---|---:|---:|",
              f"|聊天|{business['interactive']['predicted_short']}|{business['interactive']['predicted_long']}|",
              f"|后台|{business['background']['predicted_short']}|{business['background']['predicted_long']}|", "",
              "|实际执行长度类别|预测短|预测长|", "|---|---:|---:|",
              f"|实际短|{confusion['actual_short_predicted_short']}|{confusion['actual_short_predicted_long']}|",
              f"|实际长|{confusion['actual_long_predicted_short']}|{confusion['actual_long_predicted_long']}|"]
    lines += ["", "主场景聊天占200/230，超过85%，聊天输出又不超过64，因此主加权分界必不超过64。这可能只是把业务类别转换成预测类别，不能把“不再全为短请求”当成预测分类价值已证明。业务类别×预测长短、实际长度×预测长短的2×2计数均在summary.json。若仍退化或与业务优先等价，应保留事实，不为制造长类而看最终结果改阈值。", "",
        "## 5. 来源和限制", "",
        f"完整性状态：`{result['status']}`。新校准120、新开发验证120、预热2均单列。", "",
        "- [summary.json](summary.json)：全部候选、五折风险、六桶误差、分界混淆和静态上界诊断。",
        "- [model-artifact.json](model-artifact.json)：只由本轮校准生成的候选工件，尚非G1冻结或已集成预测器。",
        "- [predictions.jsonl](predictions.jsonl)：逐条预测、误差、分类与静态上界，没有模拟调度。",
        "- [evidence-sha256.json](evidence-sha256.json)：分析实际读取的原始文件指纹。",
        "- [report-sha256.json](report-sha256.json)：派生文件指纹。", "",
        "每桶20条比原来的4条多，但不足以保证任意提示或漂移场景的预测效果。没有运行真实负载对照，串行采集不承担聊天尾延迟、吞吐、后台公平或稳定性结论。最终测试数据未读取，M/B3主问题保持不变。", "",
        "EOS、没有上下文截断以及长度预测误差较小，都不能证明回答正确、有用或完成了两句/六点要求。均值若留出点预测空间，也不等于修复了回答质量。独立的后验输出预算哨兵另立批次，不能并入本轮训练或据其改选候选。", ""]
    return "\n".join(lines)


def analyze(batch, out):
    batch, out = batch.resolve(), out.resolve()
    if out.exists():
        raise FileExistsError("Refuse to overwrite any prior analysis")
    evidence = {}
    status_path = batch / "batch-status.json"
    status = read(status_path)
    evidence[status_path.relative_to(batch).as_posix()] = sha(status_path)
    if status.get("status") != "completed":
        raise ValueError("A completed new calibration/refinement batch is required")
    expected_counts = {"calibration_requests": 120, "development_validation_requests": 120,
                       "measured_requests": 240, "warmup_requests": 2, "completion_requests": 242}
    if any(status.get(name) != value for name, value in expected_counts.items()):
        raise ValueError("Collection counts do not match the registered 120+120+2 design")
    assert status["source_runtime_model_unchanged"] is True
    assert status["final_test_generated_or_read"] is False
    assert status["server_exit_code"] == 0 and status["accounting"]["events"] == 484
    assert status["terminal_counts"] == {"completed": 240, "rejected": 0, "timeout": 0,
                                         "cancelled": 0, "failed": 0, "unfinished": 0}
    summary_path = batch / "summary.json"
    assert read(summary_path) == status
    evidence[summary_path.relative_to(batch).as_posix()] = sha(summary_path)
    calibration = load_records(batch, "calibration", CAL_SEEDS, 120, evidence)
    # The selected artifact is derived before development records are even read.
    artifact, oof = make_artifact(calibration)
    artifact["calibration_training_files_sha256"] = {
        name: digest for name, digest in evidence.items() if name.startswith("calibration/")}
    selection_digest = artifact_hash(artifact)
    development = load_records(batch, "development", (21004,), 120, evidence)
    assert not ({r["template_family"] for r in calibration} & {r["template_family"] for r in development})
    assert not ({r["content_sha256"] for r in calibration} & {r["content_sha256"] for r in development})
    metrics = {"calibration": {}, "development_validation": {}}
    for method in METHODS:
        model = artifact["candidate_models"][method]
        residual = artifact["residual_tables"][method]
        metrics["calibration"][method] = evaluate(calibration, method, model, artifact["thresholds"], residual)
        metrics["development_validation"][method] = evaluate(development, method, model, artifact["thresholds"], residual)
    source_checks = {}
    for manifest_name, snapshot in (("source-sha256.json", "source"), ("engine-source-sha256.json", "engine-source")):
        path = batch / manifest_name
        if not path.exists():
            raise ValueError("Missing frozen source manifest: " + manifest_name)
        evidence[path.relative_to(batch).as_posix()] = sha(path)
        manifest = read(path)
        for relative, digest in manifest.items():
            frozen = batch / snapshot / relative
            assert frozen.is_file() and sha(frozen) == digest, "Snapshot mismatch: " + relative
            evidence[frozen.relative_to(batch).as_posix()] = digest
        source_checks[manifest_name] = {"files": len(manifest), "snapshot_bytes_match": True}
    before = batch / "engine-runtime-sha256.json"
    after = batch / "engine-runtime-sha256-after.json"
    assert read(before) == read(after)
    evidence[before.relative_to(batch).as_posix()] = sha(before)
    evidence[after.relative_to(batch).as_posix()] = sha(after)
    source_checks["runtime_before_after_identical"] = True
    environment_path, model_after_path = batch / "environment.json", batch / "model-sha256-after.json"
    environment, model_after = read(environment_path), read(model_after_path)
    assert environment["model_sha256"] == model_after["sha256"]
    assert environment["model"] == model_after["path"]
    for path in (environment_path, model_after_path):
        evidence[path.relative_to(batch).as_posix()] = sha(path)
    source_checks["model_before_after_identical"] = True
    summary = {"schema_version": 1, "status": "passed_calibration_and_development_recomputation",
        "batch_id": batch.name, "analyzed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "analysis_script_sha256": sha(Path(__file__)), "calibration_requests": 120,
        "development_validation_requests": 120, "warmup_requests": status["warmup_requests"],
        "final_test_accessed": False, "G1_frozen": False, "performance_or_scheduler_simulation": False,
        "old_calibration_or_development_in_training": False,
        "artifact": artifact, "artifact_sha256": selection_digest,
        **metrics, "source_checks": source_checks, "collection_status": status,
        "scope": "Expanded calibration/model selection and untouched-development verification of executed output length only; no M/B3 decisions or performance comparison.",
        "limits": ["Five LOSO folds share training data; no claim that they are five independent scheduler experiments.",
            "A balanced calibration/verification sample is reweighted to one predeclared main mixture, not all workloads.",
            "Classifications and false-short/long labels use executed capped lengths, not uncapped natural output.",
            "Static bound equality/difference is not actual admission equality/difference or algorithm value.",
            "EOS and absence of context truncation do not establish answer quality; separate sentinels do not enter this fit.",
            "Primary target-weighted threshold and point estimator are fixed using calibration alone; development results never select alternatives."]}
    out.mkdir(parents=True, exist_ok=False)
    dump(out / "summary.json", summary)
    dump(out / "model-artifact.json", artifact)
    dump(out / "evidence-sha256.json", evidence)
    dump(out / "model-free-self-test.json", {**self_test(), "analysis_script_sha256": sha(Path(__file__))})
    (out / "analysis-source.py").write_bytes(Path(__file__).read_bytes())
    with (out / "predictions.jsonl").open("x", encoding="utf-8") as output:
        for split, rows in (("calibration", calibration), ("development_validation", development)):
            for row in rows:
                for method in METHODS:
                    p = predict(row, artifact["candidate_models"][method])
                    residual = artifact["residual_tables"][method][cell(row)]
                    record = {"split": split, "seed": row["seed"], "req_id": row["req_id"],
                        "category": row["category"], "input_token_bucket": row["input_token_bucket"],
                        "input_tokens": row["input_tokens"], "max_tokens": row["max_tokens"],
                        "actual_output_tokens": row["output_tokens"], "point_estimator": method,
                        "is_selected_point_estimator": method == artifact["selected_point_estimator"],
                        "point_prediction": p, "actual_minus_point": row["output_tokens"] - p,
                        "positive_residual": max(0.0, row["output_tokens"] - p),
                        "calibration_oof_prediction": oof[method].get(row["req_id"]) if split == "calibration" else None,
                        "threshold_classification": {name: {"predicted": "short" if p <= tau else "long",
                            "actual_executed": "short" if row["output_tokens"] <= tau else "long"}
                            for name, tau in artifact["thresholds"].items()},
                        "static_length_bounds": {source: {q: min(row["max_tokens"], p + residual[source][q])
                            for q in ("q80", "q95")} for source in ("out_of_fold", "in_sample")},
                        "fixed_margin_length_bounds": {str(m): min(row["max_tokens"], p + m) for m in FIXED_MARGINS}}
                    output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    with (out / "README.md").open("x", encoding="utf-8") as output:
        output.write(report_text(summary))
    dump(out / "report-sha256.json", {path.name: sha(path) for path in sorted(out.iterdir()) if path.is_file()})
    return summary


def self_test():
    assert q7([0, 10], .5) == 5
    assert weighted_quantile([0, 10], [1, 1], .5) == 0
    assert weighted_quantile([10, 0, 0], [Fraction(1, 5), Fraction(2, 5), Fraction(2, 5)], .8) == 0
    try:
        weighted_quantile([1], [-1], .85)
    except ValueError:
        pass
    else:
        raise AssertionError("Negative weight accepted")
    sample = []
    for seed in CAL_SEEDS:
        for name in TARGET_COUNTS:
            category, bucket = name.split(":")
            cap = 64 if category == "interactive" else 384
            for index in range(4):
                sample.append({"req_id": f"{seed}-{name}-{index}", "split": "calibration", "seed": seed,
                    "category": category, "input_token_bucket": int(bucket), "max_tokens": cap,
                    "output_tokens": 32 if category == "interactive" else 200, "finish_reason": "stop"})
    assert sum(target_weights(sample)) == 1
    selection, _ = select_from_calibration(sample)
    assert selection["method"] == "median"
    artifact, _ = make_artifact(sample)
    assert artifact["thresholds"]["target_weighted_actual_q85"] == 32
    before = artifact_hash(artifact)
    assert artifact_hash(make_artifact(sample)[0]) == before
    rejected_holdout = [dict(row, split="development") for row in sample]
    try:
        select_from_calibration(rejected_holdout)
    except ValueError:
        pass
    else:
        raise AssertionError("Development rows accepted for model selection")
    skewed = []
    for row in sample:
        value = (10 if row["seed"] in CAL_SEEDS[:2] else 64) if row["category"] == "interactive" else (
            100 if row["seed"] in CAL_SEEDS[:2] else 384)
        skewed.append(dict(row, output_tokens=value))
    skew_artifact, _ = make_artifact(skewed)
    assert skew_artifact["selected_point_estimator"] == "mean"
    model = fit(sample, "mean")
    tiny = dict(sample[0], max_tokens=10)
    assert predict(tiny, model) == 10
    labels = classification(sample, [predict(r, model) for r in sample], 32)
    assert labels["same_as_interactive_short_background_long"] and not labels["all_one_predicted_class"]
    # Exercise the actual evaluation path with different held-out outcomes. The
    # prediction/selection artifact must remain identical, while measured errors
    # must respond to those outcomes. Merely assigning an unrelated variable
    # would not test the dependency boundary.
    development = [dict(row, req_id=f"development-{index}", split="development", seed=21004)
                   for index, row in enumerate(sample)]
    adversarial_development = [dict(row, output_tokens=row["max_tokens"], finish_reason="length")
                               for row in development]
    frozen_artifact_hash = artifact_hash(artifact)
    for method in METHODS:
        selected_model = artifact["candidate_models"][method]
        residual_table = artifact["residual_tables"][method]
        original_metrics = evaluate(development, method, selected_model, artifact["thresholds"], residual_table)
        perturbed_metrics = evaluate(adversarial_development, method, selected_model,
                                     artifact["thresholds"], residual_table)
        assert original_metrics["MAE"] == 0 < perturbed_metrics["MAE"]
        assert [predict(row, selected_model) for row in development] == [
            predict(row, selected_model) for row in adversarial_development]
    assert artifact_hash(artifact) == frozen_artifact_hash == artifact_hash(make_artifact(sample)[0])
    metrics = evaluate(sample, "mean", model, artifact["thresholds"], artifact["residual_tables"]["mean"])
    assert metrics["bound_diagnostics"]["out_of_fold"]["q80"]["fixed_margin_bound_comparisons"]["0"]["equal_length_bound_count"] == 120
    skew_metrics = evaluate(skewed, "mean", skew_artifact["candidate_models"]["mean"],
                            skew_artifact["thresholds"], skew_artifact["residual_tables"]["mean"])
    assert skew_metrics["point_at_output_cap"] == 0
    assert skew_metrics["bound_diagnostics"]["out_of_fold"]["q95"]["at_output_cap_count"] == 120
    return {"status": "passed", "checks": 18,
            "scope": "Model-free quantile, rational weighting, selection/tie, bounds and classification checks; not real predictor or scheduler validation"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
        return
    if not args.run_dir or not args.out_dir:
        parser.error("--run-dir and --out-dir are required")
    result = analyze(args.run_dir, args.out_dir)
    selected = result["artifact"]["selected_point_estimator"]
    print(json.dumps({"status": result["status"], "selected_point_estimator": selected,
        "thresholds": result["artifact"]["thresholds"],
        "validation_target_weighted_MAE": result["development_validation"][selected]["target_weighted_MAE"],
        "out_dir": str(args.out_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
