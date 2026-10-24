#!/usr/bin/env python3
"""Development-only length/margin headroom diagnostic; no scheduler simulation."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import statistics


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines(path):
    return [json.loads(v) for v in path.read_text(encoding="utf-8").splitlines() if v.strip()]


def q7(values, probability):
    values = sorted(values)
    h = (len(values)-1)*probability
    i = int(h)
    return values[i] + (values[min(i+1,len(values)-1)]-values[i])*(h-i)


def dist(values):
    return {"n":len(values),"min":min(values),"median":statistics.median(values),
            "p95":q7(values,.95),"max":max(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir",type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True)
    args = parser.parse_args()
    batch,out = args.run_dir.resolve(),args.out_dir.resolve()
    assert read(batch/"batch-status.json")["status"] == "completed"
    groups = defaultdict(list)
    evidence = {}
    cal = []
    for path in sorted((batch/"prep/calibration").glob("c*/replay/client.jsonl")):
        evidence[path.relative_to(batch).as_posix()] = sha(path)
        for row in lines(path):
            assert row["split"] == "calibration" and row["terminal"] == "completed" and row["output_tokens_known"]
            groups[(row["category"],row["input_token_bucket"])].append(row)
            cal.append(row)
    assert len(cal) == 24 and len(groups) == 6
    models = {}
    cal_rows = []
    for (category,bucket),rows in sorted(groups.items()):
        assert len(rows) == 4
        outputs = [r["output_tokens"] for r in rows]
        limit = rows[0]["max_tokens"]
        estimate = statistics.median(outputs)
        models[(category,bucket)] = estimate
        cal_rows.append({"category":category,"bucket":bucket,"samples":4,
            "actual_outputs":outputs,"temporary_median_prediction":estimate,"max_output_tokens":limit,
            "output_limit_hits":sum(r["finish_reason"] == "length" for r in rows),
            "initial_positive_margin_headroom":limit-estimate})
    tau = q7([r["output_tokens"] for r in cal],.85)
    runs = []
    for path in sorted(batch.glob("r*-prototype/replay/client.jsonl")):
        evidence[path.relative_to(batch).as_posix()] = sha(path)
        metadata = read(path.parents[1]/"manifest.json")
        rows = lines(path)
        by_class = {}
        for category in ("interactive","background"):
            selected = [r for r in rows if r["category"] == category]
            predictions = [min(r["max_tokens"],models[(category,r["input_token_bucket"])]) for r in selected]
            headroom = [r["max_tokens"]-p for r,p in zip(selected,predictions)]
            residuals = [max(0,r["output_tokens"]-p) for r,p in zip(selected,predictions) if r["output_tokens_known"]]
            by_class[category] = {"requests":len(selected),"prediction_at_output_cap":sum(h == 0 for h in headroom),
                "zero_headroom_fraction":sum(h == 0 for h in headroom)/len(selected),
                "headroom":dist(headroom),"positive_residuals":dist(residuals),
                "qba_short_at_draft_threshold":sum(p <= tau for p in predictions),
                "qba_long_at_draft_threshold":sum(p > tau for p in predictions)}
        runs.append({"run":path.parents[1].name,"repeat":metadata["repeat"],"scenario":metadata["scenario"],"classes":by_class})
    assert len(runs) == 9
    summary = {"status":"completed_development_diagnostic_only", "created_at":datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "batch":batch.name,"script_sha256":sha(Path(__file__)),"calibration_requests":24,
        "scope":"Temporary predictor diagnostic only; no predictor freeze, no M/B3 decisions, scheduler simulation or performance comparison.",
        "calibration_groups":cal_rows,"runs":runs,
        "qba_draft_threshold":{"definition":"type7 85th percentile of all 24 actual calibration output lengths",
            "tau":tau,"classification_rule":"prediction<=tau is short; prediction>tau is long",
            "all_development_predictions_short":all(v["qba_long_at_draft_threshold"] == 0 for r in runs for v in r["classes"].values()),
            "consequence":"The draft QBA classification degenerates; revise and document the development classification before G1, or transparently report equivalence. Do not label this an implemented strong NN result."},
        "input_sha256":evidence,
        "limits":["Four samples per calibration bucket do not establish a stable predictor.",
            "Same developer input content is replayed across scenarios; these are not independent training examples.",
            "Zero initial output-bound headroom in some buckets does not establish identical soft decisions or overall algorithm redundancy."]}
    out.mkdir(parents=True,exist_ok=False)
    with (out/"summary.json").open("x",encoding="utf-8") as f:
        json.dump(summary,f,ensure_ascii=False,indent=2)
        f.write("\n")
    text = ["# 临时预测与余量空间检查", "", "本检查只使用已完成的校准和开发数据，不生成最终测试、不模拟调度、不比较算法收益。每桶只有4条校准，以下临时中位数不作为冻结预测器。", "",
        "|类别|输入桶|4条实际输出|临时预测|输出上限|还能增加多少余量|", "|---|---:|---|---:|---:|---:|"]
    for r in cal_rows:
        text.append(f"|{'聊天' if r['category']=='interactive' else '后台'}|{r['bucket']}|{r['actual_outputs']}|{r['temporary_median_prediction']}|{r['max_output_tokens']}|{r['initial_positive_margin_headroom']}|")
    text += ["", "聊天的第2号桶、后台的第1号桶，临时预测已经等于输出上限。若继续按输出上限裁剪，加多少正余量也不会改变这两桶的输出长度上界；各开发轮约三分之一请求处于此情况。其他桶仍有余量空间，真实软决策还取决于负载、候选和保护规则，不能据此宣称M/B3整体没有区别。", "",
        f"QBA适配卡原拟用全部24条校准输出的85%分位作长短分界，本次得到{tau:g} token。临时预测都不超过该值，按草案规则，9轮全部预测为短请求，长请求提拔机制无法发挥作用。校准按类平衡采样，主场景类比例不同，分界的总体口径也需要说明。", "",
        "G1前应重新审查校准规模、点估计和长短分界，记录退化与适配差异。可以在开发期比较有使用依据的长度阈值或与目标请求比例一致的分位方案，锁定后再测试，不能依据最终结果挑一个弱最近邻。当前不修改已采集数据或提示，也不认定原论文方法无效。", "",
        "逐轮比例、正残差与来源哈希见[summary.json](summary.json)。"]
    with (out/"README.md").open("x",encoding="utf-8") as f:
        f.write("\n".join(text)+"\n")
    print(json.dumps({"status":summary["status"],"calibration_groups":len(cal_rows),"runs":len(runs),"qba_tau":tau,
        "all_predicted_short":summary["qba_draft_threshold"]["all_development_predictions_short"]},ensure_ascii=False))


if __name__ == "__main__":
    main()
