#!/usr/bin/env python3
"""Seen-jobs log for the daily Claude run (replaces the 'Seen jobs' doc tab).

data/seen.json: {key: {company, role, source, first_seen, outcome, fit, url}}
fetch_jobs.py skips any feed id already in this file.

Usage:
  python tools/seen.py check KEY [KEY ...]       -> prints keys already seen (and company+role matches)
  python tools/seen.py match "Company" "Role"    -> prints rows for the same company + similar role
  python tools/seen.py upsert < rows.jsonl       -> add/update rows (one JSON object per line, needs "key")
  python tools/seen.py open                      -> roles still open this week (shown/referral/worth a look/
                                                    drafted/waiting) first seen <= 7 days ago; marks older
                                                    ones 'Expired (no action)'
  python tools/seen.py prune                     -> delete rows first seen > 90 days ago
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "seen.json"
NOW = datetime.now(timezone.utc)
OPEN = re.compile(r"^(shown|referral|worth a look|drafted|waiting on referral)", re.I)


def load():
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return {}


def save(d):
    PATH.write_text(json.dumps(d, indent=1, sort_keys=True, ensure_ascii=False))


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def age_days(row):
    try:
        return (NOW - datetime.fromisoformat(row["first_seen"])).days
    except Exception:
        return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    d = load()
    if cmd == "check":
        for k in argv[2:]:
            if k in d:
                print(k, json.dumps(d[k], ensure_ascii=False))
    elif cmd == "match":
        c, r = norm(argv[2]), norm(argv[3])
        for k, v in d.items():
            if norm(v.get("company")) == c and (norm(v.get("role")) == r or r in norm(v.get("role"))
                                                 or norm(v.get("role")) in r):
                print(k, json.dumps(v, ensure_ascii=False))
    elif cmd == "upsert":
        n = 0
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            k = row.pop("key")
            cur = d.get(k, {"first_seen": NOW.isoformat()})
            cur.update({a: b for a, b in row.items() if b not in (None, "")})
            d[k] = cur
            n += 1
        save(d)
        print(f"upserted {n}")
    elif cmd == "open":
        changed, shown = False, set()
        for k, v in sorted(d.items(), key=lambda kv: kv[1].get("first_seen", ""), reverse=True):
            if not OPEN.match(v.get("outcome", "")):
                continue
            if age_days(v) > 7:
                v["outcome"] = "Expired (no action)"
                changed = True
                continue
            role = (norm(v.get("company")), norm(v.get("role")))
            if role not in shown:  # one line per role even when it has several keys
                shown.add(role)
                print(k, json.dumps(v, ensure_ascii=False))
        if changed:
            save(d)
    elif cmd == "prune":
        before = len(d)
        d = {k: v for k, v in d.items() if age_days(v) <= 90}
        save(d)
        print(f"pruned {before - len(d)}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
