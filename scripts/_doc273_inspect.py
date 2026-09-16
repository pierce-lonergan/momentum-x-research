import json
r = json.load(open('data/research/doc273_results.json'))
print('per-t: uncond net mean | base | oracle med:')
for lbl in r['grid']:
    m = r['M'][lbl]
    print(f"  {lbl:>6}: uncond {m['uncond_mean']*100:+.2f}%  base {m['base_rate']*100:.1f}%  oracle_med {m['oracle_med']*100:+.1f}%")
print('\nsplit-half I(gbm) curves:')
for h in ('H1', 'H2'):
    vals = r['I_half'][h]
    finite = {k: round(v, 4) for k, v in vals.items() if v == v}
    mx = max(finite.values(), default=float('nan'))
    pos = {k: v for k, v in finite.items() if v > 0}
    print(f'  {h}: max = {mx:+.4f} | positive points: {pos or "none"}')
print('\nI(logit) full curve:', {k: round(v['logit'], 4) for k, v in r['I'].items()})
