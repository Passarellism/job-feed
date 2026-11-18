# Daily job-search run: mandatory digest checklist

These rules apply to the weekday scheduled run (full spec lives in the routine prompt).

1. Run `python tools/carryover.py` BEFORE writing the digest. Its output is the list of roles still open this week.
2. The "Ready to act on" table MUST include every carried row with fit >= 6 (referral >= 5), plus today's new roles, merged into one numbered list sorted best fit then newest. Fit-5 rows go under "Worth a look". Never show only today's new roles.
3. State the count in Coverage: "Open carried over: N shown (TOTAL OPEN CARRIED line)".
4. Rows marked CHECK-CLOSED are feed roles not in data/jobs.json: verify (alias keys can false-alarm), then mark Closed or keep.
5. Run ALL sources in the spec (feed, Gmail alerts, referral searches, Wellfound, Built In 4 pages, web fill-in). Do not skip sources to save usage; the user has said usage is not a concern.
6. Try to read the Claude Docs workbook (Tracker + Learned preferences). If no Claude Docs tool exists in the session, say so in Coverage and use data/preferences.md if present.
7. Commit data/seen.json (and companies.json) to main and push; also push the session branch.
