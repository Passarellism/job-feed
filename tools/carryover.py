#!/usr/bin/env python3
"""Print the carry-over list for the daily digest: every role still open this week.

Run this EVERY day before writing the digest. The 'Ready to act on' table must contain
every row below with fit >= 6 (referral >= 5), plus today's new roles. 'Worth a look' gets fit 5.
Usage: python tools/carryover.py
Output: one line per role: fit | NEW? | company | role | outcome | key | url | feed-status
Rows marked CHECK-CLOSED are feed roles missing from data/jobs.json (verify, then mark Closed).
Also expires rows older than 7 days (same as `seen.py open`).
"""
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import seen  # noqa: E402

d = seen.load()
try:
    j = json.loads((ROOT / "data" / "jobs.json").read_text())
    j = j if isinstance(j, list) else j.get("jobs", [])
except Exception:
    j = []
ids = {x.get("id") for x in j}
urls = {x.get("url") for x in j}
ct = {(seen.norm(x.get("company")), seen.norm(x.get("title"))) for x in j}
today = datetime.now(timezone.utc).date().isoformat()
FEED = ("greenhouse:", "ashby:", "lever:", "workable:", "smartrecruiters:", "recruitee:", "rippling:")

rows, shown, changed = [], set(), False
for k, v in sorted(d.items(), key=lambda kv: kv[1].get("first_seen", ""), reverse=True):
    o = v.get("outcome", "")
    if not seen.OPEN.match(o):
        continue
    if seen.age_days(v) > 7:
        v["outcome"] = "Expired (no action)"; changed = True; continue
    role = (seen.norm(v.get("company")), seen.norm(v.get("role")))
    if role in shown:
        continue
    shown.add(role)
    m = re.search(r"fit (\d+)|\((\d+)", o)
    fit = int(m.group(1) or m.group(2)) if m else 0
    status = ""
    if k.startswith(FEED) and j:
        live = k in ids or v.get("alias") in ids or v.get("url") in urls or any(
            role[0] == c and (role[1] in t or t in role[1]) for c, t in ct)
        status = "live" if live else "CHECK-CLOSED"
    rows.append((fit, v.get("first_seen", "")[:10] == today, v, k, status))
if changed:
    seen.save(d)
rows.sort(key=lambda r: (-r[0], not r[1]))
for fit, new, v, k, st in rows:
    print(f"{fit} | {'NEW' if new else ''} | {v.get('company')} | {v.get('role')} | {v.get('outcome','')[:70]} | {k} | {v.get('url','')} | {st}")
print(f"TOTAL OPEN CARRIED: {len(rows)}  (digest must show all with fit>=6, referral>=5, and fit 5 under Worth a look)")
