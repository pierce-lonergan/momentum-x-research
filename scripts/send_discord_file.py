"""Post a file (+ short message) to a Discord webhook. Zero-dependency (urllib multipart).
Reads the webhook URL from ~/momentum-x-secrets.env by var name (never prints/commits the secret).

Usage: python scripts/send_discord_file.py --webhook OPS_ALERT_WEBHOOK_URL --file docs/research-log/253_x.md --message "..."
"""
from __future__ import annotations
import os, sys, json, uuid, argparse, urllib.request

def load_webhook(var):
    for f in [os.path.expanduser('~/momentum-x-secrets.env'), '.env']:
        if os.path.exists(f):
            for l in open(f, encoding='utf-8', errors='replace'):
                l=l.rstrip('\r\n')
                if l.startswith(var+'=') and not l.lstrip().startswith('#'):
                    return l.split('=',1)[1].strip().strip('"').strip("'")
    return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--webhook', default='OPS_ALERT_WEBHOOK_URL')
    ap.add_argument('--file', required=True)
    ap.add_argument('--message', default='')
    a=ap.parse_args()
    url=load_webhook(a.webhook)
    if not url or 'discord' not in url:
        print(f"FAIL: webhook {a.webhook} not found / not a discord URL"); sys.exit(1)
    if not os.path.exists(a.file):
        print(f"FAIL: file not found: {a.file}"); sys.exit(1)
    fname=os.path.basename(a.file); data=open(a.file,'rb').read()
    boundary='----mx'+uuid.uuid4().hex
    parts=[]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n{json.dumps({"content": a.message[:1900]})}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{fname}"\r\nContent-Type: text/markdown\r\n\r\n'.encode())
    parts.append(data); parts.append(f'\r\n--{boundary}--\r\n'.encode())
    body=b''.join(parts)
    req=urllib.request.Request(url, data=body, method='POST',
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}',
                 'User-Agent': 'MomentumX-Research/1.0 (+https://momentum-x)'})   # Discord 403s default urllib UA
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"OK: posted {fname} ({len(data)} bytes) to {a.webhook} [HTTP {r.status}]")
    except Exception as e:
        print(f"FAIL: {str(e)[:200]}"); sys.exit(1)

if __name__=='__main__': main()
