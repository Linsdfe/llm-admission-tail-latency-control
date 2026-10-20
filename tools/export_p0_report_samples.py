"""Export deterministic examples for the P0 report without changing raw evidence."""
import csv
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'docs/reports/20261021'
BATCH = '20261020-234210-p0-e0e1'
RAW = ROOT / 'results/raw/prestudy' / BATCH
SELECTION = {'r1-E0': ['I000', 'I100', 'I199'],
             'r1-E1': ['I000', 'I100', 'I199', 'B000', 'B050', 'B099']}


def main():
    evidence = json.loads((ROOT / 'docs/experiments' / BATCH / 'evidence-sha256.json').read_text(encoding='utf-8'))
    dest = REPORT / 'data'
    (dest / 'raw').mkdir(parents=True, exist_ok=True)
    records, entries = [], []
    table = ['| 场景和编号 | 计划到达 秒 | 迟发 毫秒 | 首字等待 秒 | 完成耗时 秒 | 输出 token |',
             '|---|---:|---:|---:|---:|---:|']
    for run, ids in SELECTION.items():
        for req_id in ids:
            relative = f'{run}/replay/requests/{req_id}.json'
            source = RAW / relative
            data = source.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if evidence[relative] != sha:
                raise ValueError(f'Original evidence hash mismatch: {relative}')
            target = dest / 'raw' / f'{run}-{req_id}.json'
            target.write_bytes(data)
            r = json.loads(data)
            assert r['terminal'] == 'completed'
            assert abs((r['first_content_offset_ms'] - r['actual_send_offset_ms']) - r['ttft_ms']) < 1e-6
            assert abs((r['actual_send_offset_ms'] - r['arrival_offset_ms']) - r['send_lag_ms']) < 1e-6
            complete = r['end_offset_ms'] - r['arrival_offset_ms']
            row = {'run': run, 'req_id': req_id, 'category': r['category'],
                   **{k: r[k] for k in ('arrival_offset_ms', 'actual_send_offset_ms', 'first_content_offset_ms', 'end_offset_ms', 'send_lag_ms', 'ttft_ms')},
                   'completion_from_planned_ms': complete, 'output_tokens': r['timings']['predicted_n'],
                   'max_tokens': r['max_tokens'], 'finish_reason': r['finish_reason'],
                   'terminal': r['terminal'], 'source_path': source.relative_to(ROOT).as_posix(), 'sha256': sha}
            records.append(row)
            entries.append({'source': row['source_path'], 'copy': target.relative_to(REPORT).as_posix(), 'sha256': sha})
            table.append(f"| {run[3:]} {req_id} | {r['arrival_offset_ms']/1000:.3f} | {r['send_lag_ms']:.3f} | {r['ttft_ms']/1000:.3f} | {complete/1000:.3f} | {r['timings']['predicted_n']} |")
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=list(records[0]))
    writer.writeheader(); writer.writerows(records)
    (dest / 'request-samples.csv').write_bytes(buffer.getvalue().encode('utf-8-sig'))
    manifest = {'batch': BATCH, 'selection': 'first, middle and last planned IDs in repeat 1, selected independently of latency',
                'scope': '9 explanatory examples; not representative statistics; full 1500 requests remain local',
                'evidence': f'docs/experiments/{BATCH}/evidence-sha256.json', 'files': entries}
    (dest / 'samples-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    md = REPORT / 'P0阶段总结与下一阶段安排.md'
    text = md.read_text(encoding='utf-8')
    start, end = '<!-- raw-samples:start -->', '<!-- raw-samples:end -->'
    before, remainder = text.split(start, 1)
    _, after = remainder.split(end, 1)
    md.write_text(before+start+'\n\n'+'\n'.join(table)+'\n\n'+end+after, encoding='utf-8')
    print(f'Exported {len(records)} byte-identical samples and verified original hashes')


if __name__ == '__main__':
    main()
