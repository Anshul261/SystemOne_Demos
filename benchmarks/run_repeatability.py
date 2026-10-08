"""Repeat the existing GLiNER benchmark and summarize variation between rounds.

Run with the repository's .venv/bin/python. No additional dependencies required.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import httpx
import benchmark as harness

OUT = ROOT / 'benchmarks/rtx5070ti-repeatability-2026-10-07'
ROUNDS = 5
REQUESTS = 500
WARMUP = 50
ITERATIONS = 10
SEED = 20261007
BASE_URL = 'http://127.0.0.1:8010'
T_CRITICAL_DF4 = 2.7764451051977987


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def mean_interval(values):
    """95% Student t interval for five round means; not per-request independence."""
    if len(values) != ROUNDS:
        return None
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    margin = T_CRITICAL_DF4 * sd / math.sqrt(ROUNDS)
    return {'mean': mean, 'round_sd': sd, 'ci95_low': mean-margin,
            'ci95_high': mean+margin, 'round_min': min(values),
            'round_max': max(values), 'rounds': len(values)}


def provenance():
    files = ['benchmark.py', 'run.py', 'browser_forms.py', 'gliner_client.py',
             'services/gliner/server.py', 'services/gliner/uv.lock', 'uv.lock',
             'benchmarks/run_repeatability.py']
    files += [str(p.relative_to(ROOT)) for p in sorted((ROOT/'fixtures').glob('*.json'))]
    return {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files}


def summarize(rounds, protocol):
    samples = [x for r in rounds for x in r['micro']['samples_ms']]
    runs = [dict(run, round=r['round']) for r in rounds for run in r['browser_runs']]
    summary = harness.summarize_browser_runs(runs)
    for demo, stats in summary.items():
        means = []
        for r in rounds:
            successes = [x['end_to_end_seconds'] for x in r['browser_runs']
                         if x['demo']==demo and x['success']]
            if successes:
                means.append(statistics.fmean(successes))
        stats['round_mean_ci95_seconds'] = mean_interval(means)
        stats['round_means_seconds'] = means
    latency = harness.latency_stats(samples)
    total_wall = sum(r['micro']['wall_seconds'] for r in rounds)
    return {'measured_at': rounds[0]['measured_at'], 'completed_at': rounds[-1]['completed_at'],
            'system': protocol['system'], 'model': protocol['model'],
            'configuration': protocol, 'rounds': rounds,
            'micro': {'requests': len(samples), 'warmup_requests_per_round': WARMUP,
                      'wall_seconds': total_wall,
                      'requests_per_second': round(len(samples)/total_wall, 2),
                      'latency': latency, 'samples_ms': samples,
                      'round_mean_ci95_ms': mean_interval(
                          [statistics.fmean(r['micro']['samples_ms']) for r in rounds]),
                      'round_p50_ms': [r['micro']['latency']['p50_ms'] for r in rounds],
                      'round_p95_ms': [r['micro']['latency']['p95_ms'] for r in rounds]},
            'browser_runs': runs, 'browser_summary': summary}


def report(results):
    micro = results['micro']; latency = micro['latency']; rounds = results['rounds']
    interval = micro['round_mean_ci95_ms']
    lines = ['# RTX 5070 Ti repeatability benchmark', '',
             f"Measured from `{results['measured_at']}` to `{results['completed_at']}`.", '',
             f"GPU: `{results['system']['gpu']}`. Model: `{results['model']['model']}`, CUDA.",
             f"Model cache revision: `{results['configuration']['model_cache_revision']}`.", '',
             'Five rounds on one host and one continuously loaded model service. Each round uses 50 warmup requests, 500 measured requests, and ten runs of each of four browser workflows. Workflow order rotates between rounds. The earlier pilot run is excluded.', '',
             '## Decision latency', '',
             '| Requests | Mean | Median | p95 | Sequential throughput |',
             '|---:|---:|---:|---:|---:|',
             f"| {micro['requests']} | {latency['mean_ms']:.2f} ms | {latency['p50_ms']:.2f} ms | {latency['p95_ms']:.2f} ms | {micro['requests_per_second']:.2f}/s |", '',
             f"95% t interval for the mean across five round means: **{interval['ci95_low']:.2f}–{interval['ci95_high']:.2f} ms**. Round mean range: {interval['round_min']:.2f}–{interval['round_max']:.2f} ms.", '',
             '| Round | Workflow order | Mean | Median | p95 | Requests/s |',
             '|---:|---|---:|---:|---:|---:|']
    for r in rounds:
        m=r['micro']; l=m['latency']
        lines.append(f"| {r['round']} | {' → '.join(r['order'])} | {l['mean_ms']:.2f} ms | {l['p50_ms']:.2f} ms | {l['p95_ms']:.2f} ms | {m['requests_per_second']:.2f} |")
    lines += ['', '## Browser workflows', '',
              'Time summaries describe successful attempts. All attempted runs, including failures, remain in the raw data and success denominators.', '',
              '| Workflow | Passed | Mean ± run SD | Median | p95 | 95% t interval for mean across rounds |',
              '|---|---:|---:|---:|---:|---:|']
    for demo, s in results['browser_summary'].items():
        d=s['end_to_end_distribution_seconds']; ci=s['round_mean_ci95_seconds']
        bounds=f"{ci['ci95_low']:.2f}–{ci['ci95_high']:.2f} s" if ci else 'Unavailable: missing successful round'
        lines.append(f"| {demo} | {s['passed']}/{s['runs']} | {d['mean']:.2f} ± {d['standard_deviation']:.2f} s | {d['p50']:.2f} s | {d['p95']:.2f} s | {bounds} |")
    lines += ['', '## Interpretation and limits', '',
              '- Raw samples are pooled before calculating median and p95; round percentiles are not averaged. Throughput divides total requests by total measured wall time.',
              '- Latency includes HTTP and JSON overhead. Browser timings include network access, page loads and execution. Field caches reset per attempt; cache hits remain part of workflow timings.',
              '- Confidence intervals use the five round means, not 2,500 independent-request assumptions. Formula: mean ± t(0.975, df=4) × round SD / sqrt(5). These are approximate intervals conditional on roughly independent, approximately normal round means; five rounds cannot establish those assumptions strongly. Shared host state, temperature and network conditions can induce dependence.',
              '- Median and p95 are descriptive pooled estimates. Their confidence intervals are not estimated. Run SD describes spread and is not a confidence interval.',
              '- Repeated synthetic fixtures measure repeatability of these specific workflows. Passing every attempt does not establish 100% accuracy on unseen tasks or a general browser agent.',
              '- This is single-host, sequential, warmed-service testing. It does not measure cold starts, concurrent capacity, cross-machine reproducibility, or production reliability.',
              '- Default Chromium fonts failed to render locally. The saved fontconfig.xml is scoped to benchmark processes and uses installed DejaVu fonts. Browser workflow source code is unchanged.',
              '- Source hashes and environment details are in protocol.json. Each round contains raw latencies, browser runs, logs and telemetry. No measured outliers are removed.',
              '- The LinkedIn footage is from earlier recordings; only the benchmark overlays are regenerated from this experiment.', '',
              'Method reference: [NIST confidence limits for the mean](https://www.itl.nist.gov/div898/handbook/eda/section3/eda352.htm).', '',
              '## Reproduce', '', '```bash', 'GLINER_DEVICE=cuda ./start-gliner.sh',
              '.venv/bin/python benchmarks/run_repeatability.py', '```', '']
    return '\n'.join(lines)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = OUT/'fontconfig.xml'
    if not config.exists():
        config.write_bytes((ROOT/'benchmarks/rtx5070ti-2026-10-07/fontconfig.xml').read_bytes())
    os.environ['FONTCONFIG_FILE'] = str(config)
    env=harness.environment_metadata()
    model=httpx.get(BASE_URL+'/health', timeout=10).json()
    assert 'RTX 5070 Ti' in env['gpu'] and model['device']=='cuda'
    order=['rpa','parabank','piko','piko-excel']; random.Random(SEED).shuffle(order)
    orders=[order[i%4:]+order[:i%4] for i in range(ROUNDS)]
    revision=(Path.home()/'.cache/huggingface/hub/models--fastino--GLiNER2.5-Decide/refs/main').read_text().strip()
    protocol={'rounds': ROUNDS, 'micro_requests_per_round': REQUESTS,
              'warmup_requests_per_round': WARMUP, 'browser_iterations_per_round': ITERATIONS,
              'seed': SEED, 'orders': orders, 'system': env, 'model': model,
              'model_cache_revision':revision, 'source_sha256':provenance(),
              'fontconfig_file':str(config), 'earlier_pilot_included':False,
              'service_restarted_between_rounds':False,
              'ci_method':'95% Student t across five round means, df=4'}
    dump(OUT/'protocol.json', protocol)
    rounds=[]
    for index, order in enumerate(orders,1):
        directory=OUT/f'round-{index}'; directory.mkdir(exist_ok=True)
        print(f'=== Round {index}/{ROUNDS}, order: {order} ===', flush=True)
        telemetry_before=harness.command_output(['nvidia-smi','--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,power.draw','--format=csv,noheader'])
        start=datetime.now(UTC)
        micro=harness.micro_benchmark(BASE_URL, REQUESTS, warmup=WARMUP)
        runs=harness.run_browser_benchmarks(BASE_URL, order, ITERATIONS)
        result={'round':index, 'order':order, 'measured_at':start.isoformat(),
                'completed_at':datetime.now(UTC).isoformat(), 'micro':micro,
                'browser_runs':runs, 'browser_summary':harness.summarize_browser_runs(runs),
                'gpu_telemetry_before':telemetry_before,
                'gpu_telemetry_after':harness.command_output(['nvidia-smi','--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,power.draw','--format=csv,noheader'])}
        dump(directory/'results.json', result)
        harness.write_runs_csv(directory/'runs.csv', runs)
        rounds.append(result)
        dump(OUT/'progress.json', {'rounds_completed':index, 'rounds':rounds})
        print(f"Round {index}: {micro['latency']}, passed {sum(x['success'] for x in runs)}/{len(runs)}", flush=True)
    result=summarize(rounds, protocol)
    dump(OUT/'results.json', result)
    (OUT/'RESULTS.md').write_text(report(result))
    with (OUT/'runs.csv').open('w') as file:
        keys=['round','demo','iteration','success','end_to_end_seconds','model_calls','model_total_ms','field_cache_hits','error']
        writer=csv.DictWriter(file,fieldnames=keys,extrasaction='ignore'); writer.writeheader(); writer.writerows(result['browser_runs'])
    paths=sorted(p for p in OUT.rglob('*') if p.is_file() and p.name not in {'SHA256SUMS','run.log'})
    (OUT/'SHA256SUMS').write_text(''.join(f"{harness.sha256(p)}  {p.relative_to(OUT)}\n" for p in paths))
    print(report(result), flush=True)


if __name__=='__main__':
    main()
