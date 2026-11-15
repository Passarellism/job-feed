#!/usr/bin/env python3
"""Daily job fetcher for Steve's job feed.

Pulls every open job from public ATS job-board APIs (Greenhouse, Lever, Ashby,
Workable, SmartRecruiters, Recruitee, Rippling) for the companies in
companies.json, keeps loosely matching titles in allowed locations, and writes:

  data/new.json   jobs first seen in the last ~30 hours (what the daily email uses)
  data/jobs.json  every currently open matching job (no descriptions)
  data/run.json   run stats, ATS detection results and failures
  data/state.json first-seen dates and detected ATS per company

Recall over precision: when in doubt a job is kept. Standard library only.
"""
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
NOW = datetime.now(timezone.utc)
UA = {"User-Agent": "job-feed/1.0 (personal job search)", "Accept": "application/json"}

# ---------------------------------------------------------------- filters

TITLE_INCLUDE = re.compile(
    r"solutions?\s+(engineer|consultant|architect|specialist|manager|analyst)"
    r"|sales\s+engineer|pre-?\s?sales|presales"
    r"|implementation|onboarding|customer\s+onboarding"
    r"|customer\s+success|client\s+success|customer\s+experience\s+manager"
    r"|technical\s+account\s+manager|\btam\b|account\s+manager"
    r"|customer\s+(solutions|engineer)|solutions\s+consultant"
    r"|deployment\s+(engineer|strategist|specialist|manager|lead)|forward\s+deployed"
    r"|professional\s+services|technical\s+consultant|product\s+specialist"
    r"|integrations?\s+(engineer|specialist|manager|consultant)"
    r"|sales\s+development|business\s+development\s+rep|\bsdr\b|\bbdr\b"
    r"|partner(ship)?s?\s+manager|channel\s+manager"
    r"|gtm\s+engineer|go[-\s]to[-\s]market\s+engineer|revenue\s+engineer",
    re.I,
)
TITLE_EXCLUDE = re.compile(
    r"\b(director|vice\s+president|vp|head\s+of|chief|principal|staff|intern|internship)\b"
    r"|^(senior\s+|sr\.?\s+)?manager\s*,|\bmanager\s+of\b|\bteam\s+lead\b"
    r"|\b(french|german|spanish|japanese|portuguese|italian|dutch|korean|mandarin)[-\s]speaking\b",
    re.I,
)

CO_TERMS = re.compile(
    r"colorado|denver|boulder|broomfield|louisville,?\s*co|lafayette,?\s*co|longmont|"
    r"golden,?\s*co|lakewood|aurora,?\s*co|englewood|westminster,?\s*co|fort\s+collins|front\s+range|,\s*co\b",
    re.I,
)
NON_US = re.compile(
    r"\b(canada|toronto|vancouver|montreal|ontario|uk|united\s+kingdom|england|london|"
    r"ireland|dublin|germany|berlin|munich|france|paris|spain|madrid|barcelona|portugal|"
    r"lisbon|netherlands|amsterdam|india|bangalore|bengaluru|hyderabad|pune|singapore|"
    r"australia|sydney|melbourne|japan|tokyo|brazil|são\s+paulo|sao\s+paulo|mexico|emea|"
    r"apac|latam|europe|israel|tel\s+aviv|poland|warsaw|philippines|manila|argentina|"
    r"colombia|bogota|sweden|stockholm|denmark|copenhagen|switzerland|zurich|italy|milan|"
    r"south\s+africa|nigeria|kenya|new\s+zealand|korea|seoul|china|hong\s+kong|uae|dubai|"
    r"indonesia|jakarta|romania|ukraine|czech|prague|austria|vienna|belgium|finland|norway|"
    r"estonia|serbia|greece|turkey|egypt|vietnam|thailand|malaysia|chile|peru|costa\s+rica)\b",
    re.I,
)
US_TERMS = re.compile(
    r"united\s+states|\busa\b|\bu\.s\.|\bus\b|us[-\s]remote|remote[-\s,(]*us|north\s+america|"
    r"\bamericas\b|anywhere|nationwide",
    re.I,
)
REMOTE_TERMS = re.compile(r"remote|work\s+from\s+home|distributed|anywhere", re.I)

MAX_AGE_DAYS = 21          # drop postings older than this (when a date exists)
NEW_WINDOW_HOURS = 30      # "new" = first seen within this window
FIRST_RUN_NEW_DAYS = 7     # on first run, only postings this recent count as new


def title_ok(title):
    t = title or ""
    return bool(TITLE_INCLUDE.search(t)) and not TITLE_EXCLUDE.search(t)


def location_verdict(loc_text, remote_flag):
    """Return (keep, tag). tag in CO / remote-US / remote-unclear / unknown / US."""
    t = (loc_text or "").strip()
    if CO_TERMS.search(t):
        return True, "CO"
    is_remote = bool(remote_flag) or bool(REMOTE_TERMS.search(t))
    has_us, has_non_us = bool(US_TERMS.search(t)), bool(NON_US.search(t))
    if is_remote:
        if has_non_us and not has_us:
            return False, "remote-non-US"
        return True, "remote-US" if has_us else "remote-unclear"
    if not t:
        return True, "unknown"
    if has_us and not has_non_us and not re.search(r",\s*[A-Z]{2}\b", t):
        return True, "US"  # e.g. "United States" with no city: often remote
    return False, "onsite-elsewhere"


# ---------------------------------------------------------------- helpers

def get_json(url, timeout=25, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(UA)
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise


def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


COMP_RE = re.compile(
    r"\$\s?\d{2,3}(?:,\d{3}|\.\d+)?\s?[kK]?(?:\s*(?:-|–|—|to)\s*\$?\s?\d{2,3}(?:,\d{3}|\.\d+)?\s?[kK]?)?"
)


def find_comp(text):
    m = COMP_RE.search(text or "")
    return m.group(0).strip() if m else ""


def iso(v):
    """Accept ms epoch, s epoch, or ISO string; return ISO UTC or None."""
    if v in (None, "", 0):
        return None
    try:
        if isinstance(v, (int, float)):
            v = v / 1000 if v > 1e11 else v
            return datetime.fromtimestamp(v, timezone.utc).isoformat()
        s = str(v).replace("Z", "+00:00")
        d = datetime.fromisoformat(s[:32]) if "T" in s else datetime.fromisoformat(s[:10])
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def job(company, ats, slug, jid, title, location, remote, url, posted, desc, comp=""):
    desc = strip_html(desc)
    return {
        "id": f"{ats}:{slug}:{jid}",
        "company": company,
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "remote": remote,
        "url": url,
        "posted": iso(posted),
        "comp": comp or find_comp(desc),
        "ats": ats,
        "description": desc[:4000],
    }


# ---------------------------------------------------------------- ATS adapters
# Each returns a list of normalized jobs, or raises on 404 / bad response.

def greenhouse(company, slug):
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        locs = [j.get("location", {}).get("name", "")]
        locs += [o.get("name", "") for o in j.get("offices", []) or []]
        out.append(job(company, "greenhouse", slug, j.get("id"), j.get("title"),
                       " | ".join(x for x in dict.fromkeys(locs) if x), None,
                       j.get("absolute_url"), j.get("first_published") or j.get("updated_at"),
                       j.get("content", "")))
    return out


def lever(company, slug):
    d = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(d, list):
        raise ValueError("not a lever board")
    out = []
    for j in d:
        c = j.get("categories", {}) or {}
        locs = c.get("allLocations") or [c.get("location", "")]
        comp = ""
        sr = j.get("salaryRange") or {}
        if sr.get("min"):
            comp = f"${sr.get('min'):,}-${sr.get('max', 0):,} {sr.get('currency', '')}".strip()
        out.append(job(company, "lever", slug, j.get("id"), j.get("text"),
                       " | ".join(x for x in locs if x), j.get("workplaceType") == "remote",
                       j.get("hostedUrl"), j.get("createdAt"),
                       (j.get("descriptionPlain") or "") + "\n" + (j.get("additionalPlain") or ""),
                       comp))
    return out


def ashby(company, slug):
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    if "jobs" not in d:
        raise ValueError("not an ashby board")
    out = []
    for j in d["jobs"]:
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")]
        locs += [s.get("location", "") for s in j.get("secondaryLocations", []) or []]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        locs.append(", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion"),
                                         addr.get("addressCountry")] if x))
        comp = ((j.get("compensation") or {}).get("compensationTierSummary") or "")
        out.append(job(company, "ashby", slug, j.get("id"), j.get("title"),
                       " | ".join(x for x in dict.fromkeys(locs) if x),
                       j.get("isRemote") or j.get("workplaceType") == "Remote",
                       j.get("jobUrl"), j.get("publishedAt"),
                       j.get("descriptionPlain") or j.get("descriptionHtml", ""), comp))
    return out


def workable(company, slug):
    d = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    if "jobs" not in d:
        raise ValueError("not a workable board")
    out = []
    for j in d["jobs"]:
        locs = [", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)]
        locs += [", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x)
                 for l in j.get("locations", []) or []]
        out.append(job(company, "workable", slug, j.get("shortcode") or j.get("id"), j.get("title"),
                       " | ".join(x for x in dict.fromkeys(locs) if x), j.get("telecommuting"),
                       j.get("url") or j.get("shortlink"), j.get("published_on") or j.get("created_at"),
                       j.get("description", "")))
    return out


def smartrecruiters(company, slug):
    out, offset = [], 0
    while True:
        d = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
        content = d.get("content", [])
        if offset == 0 and d.get("totalFound", 0) == 0:
            raise ValueError("empty smartrecruiters board")
        for j in content:
            l = j.get("location", {}) or {}
            loc = ", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x)
            out.append(job(company, "smartrecruiters", slug, j.get("id"), j.get("name"), loc,
                           l.get("remote"), f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                           j.get("releasedDate"), ""))
        offset += 100
        if offset >= d.get("totalFound", 0) or offset > 1000:
            return out


def recruitee(company, slug):
    d = get_json(f"https://{slug}.recruitee.com/api/offers/")
    if "offers" not in d:
        raise ValueError("not a recruitee board")
    return [job(company, "recruitee", slug, j.get("id"), j.get("title"), j.get("location", ""),
                j.get("remote"), j.get("careers_url"), j.get("published_at") or j.get("created_at"),
                j.get("description", "")) for j in d["offers"]]


def rippling(company, slug):
    # Rippling's public board API is undocumented; parse generously.
    d = get_json(f"https://ats.rippling.com/api/v2/board/{slug}/jobs")
    items = d if isinstance(d, list) else (d.get("items") or d.get("results") or d.get("jobs") or [])
    if not items:
        raise ValueError("no rippling jobs")
    out = []
    for j in items:
        locs = j.get("locations") or j.get("workLocations") or []
        loc = " | ".join((l.get("name") if isinstance(l, dict) else str(l)) for l in locs) \
            or str(j.get("location") or "")
        jid = j.get("id") or j.get("uuid")
        out.append(job(company, "rippling", slug, jid, j.get("name") or j.get("title"), loc,
                       None, j.get("url") or f"https://ats.rippling.com/{slug}/jobs/{jid}",
                       j.get("createdOn") or j.get("created_at"), j.get("description", "")))
    return out


ADAPTERS = {
    "greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable,
    "smartrecruiters": smartrecruiters, "recruitee": recruitee, "rippling": rippling,
}
DETECT_ORDER = ["greenhouse", "ashby", "lever", "workable", "smartrecruiters", "recruitee", "rippling"]


def slug_candidates(c):
    if c.get("slug"):
        return [c["slug"]] if isinstance(c["slug"], str) else list(c["slug"])
    base = re.sub(r"[^a-z0-9 ]", "", c["name"].lower())
    return list(dict.fromkeys([base.replace(" ", ""), base.replace(" ", "-")]))


def fetch_company(c, state):
    """Return (jobs, ats, slug, error)."""
    name = c["name"]
    known = state["detected"].get(name)
    if c.get("ats") and c.get("ats") in ADAPTERS:
        tries = [(c["ats"], s) for s in slug_candidates(c)]
    elif known:
        tries = [(known["ats"], known["slug"])]
    else:
        miss = state["missing"].get(name)
        if miss and (NOW - datetime.fromisoformat(miss)).days < 7:
            return [], None, None, "not found (rechecks weekly)"
        tries = [(a, s) for s in slug_candidates(c) for a in DETECT_ORDER]
    last_err = None
    for ats, slug in tries:
        try:
            jobs = ADAPTERS[ats](name, slug)
            return jobs, ats, slug, None
        except Exception as e:  # noqa: BLE001
            last_err = f"{ats}/{slug}: {type(e).__name__}: {str(e)[:80]}"
    if known:  # board moved or broke: forget it so next run re-detects
        state["detected"].pop(name, None)
    return [], None, None, last_err or "no board found"


# ---------------------------------------------------------------- main

def load(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def main():
    DATA.mkdir(exist_ok=True)
    companies = load(ROOT / "companies.json", [])
    state = load(DATA / "state.json", {"first_seen": {}, "detected": {}, "missing": {}})
    first_run = not state["first_seen"]

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda c: (c, fetch_company(c, state)), companies))

    stats = {"companies": len(companies), "boards_found": 0, "jobs_scanned": 0,
             "by_ats": {}, "failures": [], "dropped": {"title": 0, "location": 0, "age": 0}}
    matched = []
    for c, (jobs, ats, slug, err) in results:
        name = c["name"]
        if err:
            if err.startswith("not found"):
                pass
            else:
                state["missing"][name] = NOW.isoformat()
                stats["failures"].append({"company": name, "error": err})
            continue
        state["missing"].pop(name, None)
        state["detected"][name] = {"ats": ats, "slug": slug}
        stats["boards_found"] += 1
        stats["by_ats"][ats] = stats["by_ats"].get(ats, 0) + 1
        stats["jobs_scanned"] += len(jobs)
        for j in jobs:
            if not title_ok(j["title"]):
                stats["dropped"]["title"] += 1
                continue
            keep, tag = location_verdict(j["location"], j["remote"])
            if not keep:
                stats["dropped"]["location"] += 1
                continue
            if j["posted"] and NOW - datetime.fromisoformat(j["posted"]) > timedelta(days=MAX_AGE_DAYS):
                stats["dropped"]["age"] += 1
                continue
            j["location_tag"] = tag
            j["company_tags"] = c.get("tags", [])
            matched.append(j)

    seen = state["first_seen"]
    new = []
    for j in matched:
        if j["id"] not in seen:
            seen[j["id"]] = NOW.isoformat()
        j["first_seen"] = seen[j["id"]]
        is_new = NOW - datetime.fromisoformat(j["first_seen"]) <= timedelta(hours=NEW_WINDOW_HOURS)
        if is_new and first_run and j["posted"]:
            is_new = NOW - datetime.fromisoformat(j["posted"]) <= timedelta(days=FIRST_RUN_NEW_DAYS)
        if is_new:
            new.append(j)

    # forget first-seen entries for jobs closed more than 60 days ago
    open_ids = {j["id"] for j in matched}
    for k in [k for k, v in seen.items()
              if k not in open_ids and NOW - datetime.fromisoformat(v) > timedelta(days=60)]:
        del seen[k]

    new.sort(key=lambda j: j["posted"] or j["first_seen"], reverse=True)
    lite = [{k: v for k, v in j.items() if k != "description"} for j in matched]
    stats.update({"run_at": NOW.isoformat(), "first_run": first_run,
                  "matched_open": len(matched), "new": len(new)})

    (DATA / "new.json").write_text(json.dumps(new, indent=1, ensure_ascii=False))
    (DATA / "jobs.json").write_text(json.dumps(lite, indent=1, ensure_ascii=False))
    (DATA / "run.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False))
    (DATA / "state.json").write_text(json.dumps(state, indent=1, sort_keys=True))
    print(json.dumps({k: stats[k] for k in ("companies", "boards_found", "jobs_scanned",
                                            "matched_open", "new", "by_ats")}))
    print(f"failures: {len(stats['failures'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
