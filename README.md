# job-feed

Daily job collector for Steve's search.

- `companies.json` — companies to watch. Add `{"name": "Acme", "tags": ["saas"]}`; the ATS and board slug are detected automatically (add `"slug"` if the guess is wrong).
- `fetch_jobs.py` — pulls public job-board APIs (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Rippling), keeps loosely matching titles that are remote-US, Colorado, or unknown location, posted within 21 days.
- `.github/workflows/fetch.yml` — runs daily at ~4:17am Denver and commits `data/`.
- `data/new.json` — jobs first seen in the last ~30 hours; the daily Claude task scores these plus LinkedIn alert emails and emails a ranked list.
- `data/run.json` — stats and companies whose board couldn't be found.

Recall over precision: filters only drop jobs that clearly don't fit. Fit scoring happens in the Claude task.
