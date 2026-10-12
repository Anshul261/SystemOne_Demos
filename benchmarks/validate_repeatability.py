"""Independently verify pooled statistics and inspect within-round latency drift."""
import hashlib
import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT/'benchmarks/rtx5070ti-repeatability-2026-10-07'


def quantile(values, q):
    x=sorted(values); position=(len(x)-1)*q
    low=math.floor(position); high=math.ceil(position)
    return x[low]+(x[high]-x[low])*(position-low)


def t4_cdf(x):
    u=x/math.sqrt(x*x+4)
    return 0.5+0.75*u-0.25*u**3


def t4_critical():
    low, high=0., 10.
    for _ in range(100):
        mid=(low+high)/2
        if t4_cdf(mid)<0.975: low=mid
        else: high=mid
    return (low+high)/2


def correlation(a,b):
    ma=statistics.fmean(a); mb=statistics.fmean(b)
    numerator=sum((x-ma)*(y-mb) for x,y in zip(a,b))
    denom=math.sqrt(sum((x-ma)**2 for x in a)*sum((y-mb)**2 for y in b))
    return numerator/denom if denom else 0.


def main():
    r=json.loads((OUT/'results.json').read_text())
    assert len(r['rounds'])==5
    all_samples=[]; all_runs=[]
    for index,b in enumerate(r['rounds'],1):
        saved=json.loads((OUT/f'round-{index}/results.json').read_text())
        assert saved==b
        assert len(b['micro']['samples_ms'])==500
        assert len(b['browser_runs'])==40
        assert len({(x['demo'],x['iteration']) for x in b['browser_runs']})==40
        all_samples+=b['micro']['samples_ms']; all_runs+=b['browser_runs']
    assert len(all_samples)==2500 and len(all_runs)==200
    for key,q in [('p50_ms',.5),('p95_ms',.95)]:
        assert r['micro']['latency'][key]==round(quantile(all_samples,q),2)
    assert r['micro']['latency']['mean_ms']==round(statistics.fmean(all_samples),2)
    assert r['micro']['requests_per_second']==round(2500/sum(b['micro']['wall_seconds'] for b in r['rounds']),2)
    critical=t4_critical()
    assert abs(critical-2.7764451051977987)<1e-12
    rows=[]
    for demo,s in r['browser_summary'].items():
        selected=[x for x in all_runs if x['demo']==demo]
        passed=[x for x in selected if x['success']]
        assert s['passed']==len(passed) and s['runs']==len(selected)==50
        values=[x['end_to_end_seconds'] for x in passed]
        assert s['end_to_end_distribution_seconds']['mean']==round(statistics.fmean(values),3)
        batch_means=[]
        for b in r['rounds']:
            x=[x['end_to_end_seconds'] for x in b['browser_runs'] if x['demo']==demo and x['success']]
            if x: batch_means.append(statistics.fmean(x))
        ci=s['round_mean_ci95_seconds']
        if len(batch_means)==5:
            mean=statistics.fmean(batch_means); se=statistics.stdev(batch_means)/math.sqrt(5)
            assert abs(ci['ci95_low']-(mean-critical*se))<1e-10
            assert abs(ci['ci95_high']-(mean+critical*se))<1e-10
        else: assert ci is None
    for source,digest in r['configuration']['source_sha256'].items():
        assert hashlib.sha256((ROOT/source).read_bytes()).hexdigest()==digest
    lines=['# Independent validation', '',
           'Share with caveats. Raw counts, round files, source hashes, pooled mean/quantiles, weighted throughput, success denominators and workflow confidence intervals reconcile. Student t critical value was independently solved from the analytic df=4 CDF.', '',
           '## Within-round latency diagnostics', '',
           '| Round | Lag-1 correlation | First-half mean | Second-half mean | Difference |',
           '|---:|---:|---:|---:|---:|']
    for b in r['rounds']:
        x=b['micro']['samples_ms']; first=statistics.fmean(x[:250]); second=statistics.fmean(x[250:])
        lines.append(f"| {b['round']} | {correlation(x[:-1],x[1:]):.3f} | {first:.2f} ms | {second:.2f} ms | {second-first:+.2f} ms |")
    lines += ['', 'These are descriptive checks, not a stationarity or independence test. Round means share one warmed service and one workstation; approximate mean confidence intervals should not be read as general-task accuracy or cross-machine reliability.', '',
              'Media and post should use pooled median/p95, approximate mean confidence intervals, all attempted run counts, and explicit repeated-fixture scope. Footage and measurements come from separate runs.', '']
    (OUT/'VALIDATION.md').write_text('\n'.join(lines))
    print('\n'.join(lines))


if __name__=='__main__': main()
