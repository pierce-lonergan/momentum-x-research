"""doc 267 - Together serverless model bench: the 6 NEW models vs current production models.
Per-model: availability, latency (3 reps), JSON-compliance on a tier-2-style extraction task, and a
judgment sanity case (the doc-253 dilution red-flag trap). Research only; no settings changed.
"""
from __future__ import annotations
import os, re, json, time
import numpy as np
from openai import OpenAI

def key():
    for f in [os.path.expanduser('~/momentum-x-secrets.env'), '.env']:
        if os.path.exists(f):
            for l in open(f, encoding='utf-8', errors='replace'):
                if l.startswith('TOGETHER_AI_API_KEY='): return l.split('=',1)[1].strip().strip('"').strip("'")
    return None

NEW=[('deepseek-ai/DeepSeek-V4-Pro','$1.74/$3.48'),
     ('zai-org/GLM-5.1','$1.40/$4.40'),
     ('nvidia/nemotron-3-ultra-550b-a55b','$0.60/$3.60'),
     ('moonshotai/Kimi-K2.6','$1.20/$4.50'),
     ('MiniMaxAI/MiniMax-M2.7','$0.30/$1.20'),
     ('Qwen/Qwen3.7-Max','$1.25/$3.75')]
CURRENT=[('Qwen/Qwen3-235B-A22B-Instruct-2507-tput','(current strong serverless)'),
         ('meta-llama/Llama-3.3-70B-Instruct-Turbo','$0.88 (emergency tier)'),
         ('Qwen/Qwen2.5-7B-Instruct-Turbo','$0.30 (tier1 fallback)')]

EXTRACT=("Extract trading signal as JSON only, no prose: "
 '{"signal":"BULL|BEAR|NEUTRAL","confidence":0.0-1.0,"catalyst_type":"FDA|EARNINGS|OFFERING|MERGER|OTHER"}.\n'
 "NEWS: Acme Biotech (ACMB) announces FDA approval of its lead drug; simultaneously prices a $50M "
 "registered direct offering at a 20% discount to yesterday's close.")
JUDGE=("A small-cap gapped +40% premarket. Its last 8-K discloses a $600M at-the-market (ATM) equity offering "
 "program and a going-concern note. As a risk analyst, is the dilution/fade risk HIGH or LOW? "
 'Respond JSON only: {"risk":"HIGH|LOW","reason":"<=10 words"}.')

def bench(client, mid):
    lat=[]; parse=0; judge_ok=None; err=''
    for i in range(3):
        t0=time.time()
        try:
            r=client.chat.completions.create(model=mid, temperature=0.1, max_tokens=120,
                messages=[{'role':'user','content':EXTRACT}])
            lat.append(time.time()-t0)
            m=re.search(r'\{.*\}', r.choices[0].message.content, re.S)
            if m:
                o=json.loads(m.group(0))
                if str(o.get('signal','')).upper() in ('BULL','BEAR','NEUTRAL'): parse+=1
        except Exception as e:
            err=str(e)[:70]; break
    if lat:
        try:
            r=client.chat.completions.create(model=mid, temperature=0.1, max_tokens=80,
                messages=[{'role':'user','content':JUDGE}])
            m=re.search(r'\{.*\}', r.choices[0].message.content, re.S)
            judge_ok = bool(m) and str(json.loads(m.group(0)).get('risk','')).upper()=='HIGH'
        except Exception:
            judge_ok=False
    return lat, parse, judge_ok, err

def main():
    k=key()
    if not k: print('no key'); return
    c=OpenAI(base_url='https://api.together.xyz/v1', api_key=k, max_retries=0)
    print(f"{'model':<44}{'price in/out':<16}{'p50 lat':>8}{'json':>6}{'judge':>7}  note")
    for grp,models in [('NEW',NEW),('CURRENT',CURRENT)]:
        print(f"--- {grp} ---")
        for mid,price in models:
            lat,parse,judge,err=bench(c,mid)
            if not lat:
                print(f"{mid:<44}{price:<16}{'FAIL':>8}{'-':>6}{'-':>7}  {err}")
                continue
            print(f"{mid:<44}{price:<16}{np.median(lat):>7.1f}s{f'{parse}/3':>6}{('PASS' if judge else 'MISS'):>7}")

if __name__=='__main__': main()
