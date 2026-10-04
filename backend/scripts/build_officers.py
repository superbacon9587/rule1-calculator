"""
Build the leadership dataset for the tickers in rule1.db: who currently
holds an officer or director role at each company, according to Wikidata,
with their public professional links.

Writes data/company_officers.csv, then drops and rebuilds the
`company_officers` table in rule1.db from that CSV. No other table is
touched, so this is safe to re-run.

Wikidata lags real leadership changes, so data/officer_overrides.csv (kept
by hand) is applied after the Wikidata pull and wins over it. Its columns
are ticker, person_name, title, action, source_url, checked_on, note,
fallback_url:
  replace_ceo    drop every CEO row for the ticker, add person_name as CEO
  replace_chair  the same for the chair; `title` may give the exact wording
                 (e.g. "Executive Chairman")
  add            add person_name with `title`
  remove         drop person_name's rows (only the `title` one when title is set)
  note           attach `note` (linked to source_url) to person_name's rows
  board_checked  (no person) the ticker's board rows were matched to the company's
                 own board page, source_url, on checked_on; that date goes in the
                 board_checked_on column of every row of the ticker
Rows an override adds are confidence "high" with source "manual override".
Their links still come only from Wikidata: the person is taken from the
company's own rows when already there, otherwise from a Wikidata search
that is accepted only when exactly one hit is a human with a statement
linking them to the company item (with no match, wikidata_id is the
stand-in "manual:<name>"). When that leaves the person with neither
a Wikipedia article nor a website, fallback_url (a company page checked by
hand) is used as their website_url; otherwise they get no links.

After the overrides, a person with a "high" CEO or chair row at a company
keeps only those rows there; their board-member and other lower rows are
dropped.

Usage (from backend/):
    python scripts/build_officers.py                # query Wikidata, write CSV, load table
    python scripts/build_officers.py --from-csv     # no network: reload the table from the CSV
                                                    # (overrides are not re-applied)
    python scripts/build_officers.py --db path/to/rule1.db --csv path/to/out.csv

Sources -- APIs only, no HTML is fetched or scraped:
  - Wikidata SPARQL endpoint: ticker -> company item, company -> officers,
    person -> official website / X handle / LinkedIn ID
  - Wikidata API (wbgetentities): each person's English label and English
    Wikipedia sitelink
  - Wikipedia REST API (page/summary): confirms the sitelinked article
    exists and is about that same Wikidata item

How a row is found:
  1. Company: the one Wikidata item with a current NYSE or Nasdaq listing
     (P414) whose ticker qualifier (P249) is the ticker. No match, or more
     than one, means no rows for that ticker -- there is no name-search
     fallback.
  2. Officers: the company item's statements for the properties in ROLES,
     where the value is a human. Deprecated statements and statements with
     an end date (P582) are skipped.
  3. Links: only values Wikidata has. A property with several current
     values (e.g. two X handles) is left blank rather than picking one.

Only name, title and those links are requested; no query asks for birth
dates, family, addresses or any other personal property.

confidence is "high" when the ticker matched one company item and the
statement names a specific role and is one Wikidata ranks as current.
It is "review" when any of these holds:
  - the statement is normal rank while the same role has a preferred-rank
    statement (Wikidata's own way of marking the current holder)
  - several people hold a one-person role (CEO) with no end date
  - the property doesn't name a role (P1037 "director / manager", or P2828
    without a role qualifier)
  - the person has another role at the company that ended after this one
    started (or this one has no start date), which usually means this
    statement was never closed
The app shows only "high" rows.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "rule1.db"
DEFAULT_CSV = ROOT / "data" / "company_officers.csv"
DEFAULT_OVERRIDES = ROOT / "data" / "officer_overrides.csv"

SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"

USER_AGENT = ("rule1-calculator-build-officers/1.0 "
              "(https://github.com/superbacon9587/rule1-calculator; class project, "
              "one run per rebuild of rule1.db) python-urllib")
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
MAX_TRIES = 4

COLUMNS = ("ticker", "person_name", "title", "wikidata_id", "wikipedia_url", "website_url",
           "x_url", "linkedin_url", "source", "retrieved_at", "confidence", "note", "note_url",
           "board_checked_on")

OVERRIDE_COLUMNS = ("ticker", "person_name", "title", "action", "source_url", "checked_on", "note",
                    "fallback_url")
OVERRIDE_ACTIONS = ("add", "replace_ceo", "replace_chair", "remove", "note", "board_checked")
CEO_TITLE = "Chief Executive Officer"
CHAIR_TITLE = "Chairperson"
OVERRIDE_SOURCE = "manual override"

# Wikidata property -> (title, names a specific role, held by one person at a time).
# Listed in the order the rows are written.
ROLES = {
    "P169": (CEO_TITLE, True, True),
    "P488": (CHAIR_TITLE, True, False),   # co-chairs exist, so several holders isn't a red flag
    "P2828": ("Corporate officer", False, False),   # title comes from the P3831 role qualifier when present
    "P1037": ("Director / manager", False, False),
    "P3320": ("Board member", True, False),
}

# New York Stock Exchange, Nasdaq
US_EXCHANGES = ("Q13677", "Q82059")

TABLE_SQL = """
CREATE TABLE company_officers (
    ticker TEXT NOT NULL,
    person_name TEXT NOT NULL,
    title TEXT NOT NULL,
    wikidata_id TEXT NOT NULL,
    wikipedia_url TEXT,
    website_url TEXT,
    x_url TEXT,
    linkedin_url TEXT,
    source TEXT,
    retrieved_at TEXT,
    confidence TEXT NOT NULL,
    note TEXT,
    note_url TEXT,
    board_checked_on TEXT,
    PRIMARY KEY (ticker, wikidata_id, title)
)
"""


# ---- HTTP ---------------------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    # python.org's macOS build ships without a CA bundle; use certifi's when it's installed.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL = _ssl_context()
_last_request = 0.0


def _get_json(url: str, params: "dict | None" = None, accept: str = "application/json",
              missing_ok: bool = False):
    """GET `url` as JSON, at most one request per MIN_SECONDS_BETWEEN_REQUESTS,
    backing off on 429/5xx. With missing_ok, a 404 returns None."""
    global _last_request
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    for attempt in range(1, MAX_TRIES + 1):
        wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=60, context=_SSL) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404 and missing_ok:
                return None
            if e.code not in (429, 500, 502, 503, 504) or attempt == MAX_TRIES:
                raise
            retry_after = e.headers.get("Retry-After", "")
            time.sleep(int(retry_after) if retry_after.isdigit() else 5 * attempt)
        except urllib.error.URLError:
            if attempt == MAX_TRIES:
                raise
            time.sleep(5 * attempt)


def _sparql(query: str) -> "list[dict]":
    body = _get_json(SPARQL_URL, {"query": query, "format": "json"},
                     accept="application/sparql-results+json")
    return [{k: v["value"] for k, v in b.items()} for b in body["results"]["bindings"]]


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[1]


def _values(ids) -> str:
    return " ".join(f"wd:{i}" for i in ids)


# ---- Wikidata / Wikipedia lookups -------------------------------------------------

def find_companies(tickers: "list[str]") -> "dict[str, list[str]]":
    """ticker -> the Wikidata items currently listed on NYSE/Nasdaq under that ticker."""
    rows = _sparql(f"""
        SELECT DISTINCT ?ticker ?item WHERE {{
          VALUES ?ticker {{ {" ".join(json.dumps(t) for t in tickers)} }}
          VALUES ?exchange {{ {_values(US_EXCHANGES)} }}
          ?item p:P414 ?listing .
          ?listing ps:P414 ?exchange ; pq:P249 ?ticker ; wikibase:rank ?rank .
          FILTER(?rank != wikibase:DeprecatedRank)
          FILTER NOT EXISTS {{ ?listing pq:P582 ?end }}
        }}""")
    found: "dict[str, list[str]]" = {t: [] for t in tickers}
    for r in rows:
        found[r["ticker"]].append(_qid(r["item"]))
    return found


def find_role_statements(company_ids: "list[str]") -> "list[dict]":
    """Every non-deprecated ROLES statement on those items whose value is a
    human, ended ones included (classify() needs them to spot stale rows)."""
    rows = _sparql(f"""
        SELECT ?item ?prop ?person ?best ?start ?end ?roleLabel WHERE {{
          VALUES ?item {{ {_values(company_ids)} }}
          VALUES ?prop {{ {_values(ROLES)} }}
          ?prop wikibase:claim ?claim ; wikibase:statementProperty ?value .
          ?item ?claim ?st .
          ?st ?value ?person ; wikibase:rank ?rank .
          ?person wdt:P31 wd:Q5 .
          FILTER(?rank != wikibase:DeprecatedRank)
          BIND(EXISTS {{ ?st a wikibase:BestRank }} AS ?best)
          OPTIONAL {{ ?st pq:P580 ?start }}
          OPTIONAL {{ ?st pq:P582 ?end }}
          OPTIONAL {{ ?st pq:P3831 ?role . ?role rdfs:label ?roleLabel . FILTER(LANG(?roleLabel) = "en") }}
        }}""")
    return [{
        "company": _qid(r["item"]),
        "prop": _qid(r["prop"]),
        "person": _qid(r["person"]),
        "best": r["best"] == "true",
        "start": r.get("start", "")[:10],
        "end": r.get("end", "")[:10],
        "role": r.get("roleLabel", ""),
    } for r in rows]


def find_links(person_ids: "list[str]") -> "dict[str, dict[str, list[str]]]":
    """person -> {P856 | P2002 | P6634: [current values]}. Asks for those
    three properties and nothing else about the person."""
    out: "dict[str, dict[str, list[str]]]" = {}
    for i in range(0, len(person_ids), 100):
        rows = _sparql(f"""
            SELECT DISTINCT ?person ?prop ?val WHERE {{
              VALUES ?person {{ {_values(person_ids[i:i + 100])} }}
              VALUES ?prop {{ wd:P856 wd:P2002 wd:P6634 }}
              ?prop wikibase:claim ?claim ; wikibase:statementProperty ?value .
              ?person ?claim ?st .
              ?st ?value ?val ; a wikibase:BestRank .
              FILTER NOT EXISTS {{ ?st pq:P582 ?end }}
            }}""")
        for r in rows:
            out.setdefault(_qid(r["person"]), {}).setdefault(_qid(r["prop"]), []).append(r["val"])
    return out


def find_names_and_sitelinks(person_ids: "list[str]") -> "dict[str, dict]":
    """person -> {"name": English label, "enwiki": English Wikipedia article title}."""
    out: "dict[str, dict]" = {}
    for i in range(0, len(person_ids), 50):  # wbgetentities takes 50 ids per call
        body = _get_json(WIKIDATA_API_URL, {
            "action": "wbgetentities", "format": "json", "ids": "|".join(person_ids[i:i + 50]),
            "props": "labels|sitelinks", "languages": "en|mul", "sitefilter": "enwiki", "maxlag": 5,
        })
        if "error" in body:
            raise RuntimeError(f"wbgetentities failed: {body['error'].get('info', body['error'])}")
        for pid, ent in body.get("entities", {}).items():
            labels = ent.get("labels", {})
            label = labels.get("en") or labels.get("mul") or {}
            out[pid] = {"name": label.get("value", ""),
                        "enwiki": ent.get("sitelinks", {}).get("enwiki", {}).get("title", "")}
    return out


def search_person(name: str, company_id: str) -> "list[str]":
    """Wikidata items found by searching `name` that are humans with a statement
    linking them to `company_id`, in either direction."""
    body = _get_json(WIKIDATA_API_URL, {
        "action": "wbsearchentities", "format": "json", "search": name, "language": "en",
        "type": "item", "limit": 10, "maxlag": 5,
    })
    if "error" in body:
        raise RuntimeError(f"wbsearchentities failed: {body['error'].get('info', body['error'])}")
    hits = [h["id"] for h in body.get("search", [])]
    if not hits:
        return []
    rows = _sparql(f"""
        SELECT DISTINCT ?person WHERE {{
          VALUES ?person {{ {_values(hits)} }}
          VALUES ?company {{ wd:{company_id} }}
          ?person wdt:P31 wd:Q5 .
          {{ ?person ?p1 ?st . ?st ?p2 ?company }} UNION {{ ?company ?p1 ?st . ?st ?p2 ?person }}
        }}""")
    return sorted(_qid(r["person"]) for r in rows)


def confirm_wikipedia_url(title: str, wikidata_id: str) -> "tuple[str, str]":
    """(article URL, "") when the English Wikipedia article `title` exists and
    is about `wikidata_id`; otherwise ("", why not)."""
    summary = _get_json(WIKIPEDIA_SUMMARY_URL + urllib.parse.quote(title.replace(" ", "_"), safe=""),
                        missing_ok=True)
    if summary is None:
        return "", "the Wikipedia REST API has no such article"
    if summary.get("type") != "standard":
        return "", f"the article is a {summary.get('type')} page"
    if summary.get("wikibase_item") != wikidata_id:
        return "", f"the article is about {summary.get('wikibase_item')}"
    url = summary.get("content_urls", {}).get("desktop", {}).get("page", "")
    return (url, "") if url else ("", "the REST API returned no page URL")


# ---- Building rows -------------------------------------------------------------

def classify(statements: "list[dict]") -> "list[dict]":
    """The current statements of one company as {person, prop, title, confidence, why},
    one per (person, title), in ROLES order. See the module docstring for the rules."""
    current = [s for s in statements if not s["end"]]
    ended = [s for s in statements if s["end"]]
    has_preferred = {s["prop"] for s in current if s["best"]} & {s["prop"] for s in current if not s["best"]}
    holders: "dict[str, set[str]]" = {}
    for s in current:
        if s["best"]:
            holders.setdefault(s["prop"], set()).add(s["person"])

    rows: "dict[tuple[str, str], dict]" = {}
    for s in sorted(current, key=lambda s: (list(ROLES).index(s["prop"]), s["start"] or "9999", s["person"])):
        default_title, names_role, one_holder = ROLES[s["prop"]]
        title = default_title
        why = []
        if s["role"]:
            title = s["role"][:1].upper() + s["role"][1:]
        elif not names_role:
            why.append(f"{s['prop']} doesn't name a specific role")
        if not s["best"] and s["prop"] in has_preferred:
            why.append("normal rank; another statement for this role is preferred")
        elif one_holder and len(holders.get(s["prop"], ())) > 1:
            why.append("several current holders of a one-person role")
        later_end = max((e["end"] for e in ended if e["person"] == s["person"]
                         and (not s["start"] or e["end"] >= s["start"])), default="")
        if later_end:
            why.append(f"another role of theirs here ended {later_end}; this one may be stale")
        row = {"person": s["person"], "prop": s["prop"], "title": title,
               "confidence": "review" if why else "high", "why": "; ".join(why)}
        key = (s["person"], title)
        # The same person and title from two statements: keep the more trusted one.
        if key not in rows or (rows[key]["confidence"] == "review" and not why):
            rows[key] = row
    return list(rows.values())


def _single(values: "list[str]") -> str:
    return values[0] if len(values) == 1 else ""


def build_rows(tickers: "list[str]") -> "tuple[list[dict], list[str], dict[str, str]]":
    """Query Wikidata and return (CSV rows, notes on what couldn't be found,
    ticker -> company item)."""
    notes: "list[str]" = []
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Looking up {len(tickers)} tickers on Wikidata ...", flush=True)
    candidates = find_companies(tickers)
    company_of: "dict[str, str]" = {}
    for t in tickers:
        if len(candidates[t]) == 1:
            company_of[t] = candidates[t][0]
        elif candidates[t]:
            notes.append(f"{t}: {len(candidates[t])} Wikidata items are listed under this ticker "
                         f"({', '.join(sorted(candidates[t]))}); not choosing between them")
        else:
            notes.append(f"{t}: no Wikidata item has a current NYSE/Nasdaq listing with this ticker")
    if not company_of:
        return [], notes, company_of

    print("Fetching officer statements ...", flush=True)
    statements = find_role_statements(sorted(company_of.values()))
    officers = {t: classify([s for s in statements if s["company"] == c]) for t, c in company_of.items()}

    person_ids = sorted({o["person"] for rows in officers.values() for o in rows})
    if not person_ids:
        return [], notes, company_of
    print(f"Fetching names and links for {len(person_ids)} people ...", flush=True)
    people = fetch_people(person_ids, notes)

    rows: "list[dict]" = []
    for t in tickers:
        for o in officers.get(t, []):
            person = people[o["person"]]
            if not person["person_name"]:
                notes.append(f"{t}: {o['person']} ({o['title']}) has no English label on Wikidata; row skipped")
                continue
            rows.append({
                "ticker": t, **person, "title": o["title"],
                "source": f"https://www.wikidata.org/wiki/{company_of[t]}#{o['prop']}",
                "retrieved_at": retrieved_at, "confidence": o["confidence"],
                "note": "", "note_url": "", "_why": o["why"],
            })
    return rows, notes, company_of


def fetch_people(person_ids: "list[str]", notes: "list[str]") -> "dict[str, dict]":
    """person -> their person_name, wikidata_id and link columns."""
    labels = find_names_and_sitelinks(person_ids)
    links = find_links(person_ids)
    people: "dict[str, dict]" = {}
    for p in person_ids:
        name = labels.get(p, {}).get("name", "")
        article = labels.get(p, {}).get("enwiki", "")
        wikipedia_url = ""
        if article:
            wikipedia_url, problem = confirm_wikipedia_url(article, p)
            if problem:
                notes.append(f"{p} ({name or 'no English label'}): Wikipedia link "
                             f"\"{article}\" left out -- {problem}")
        person_links = links.get(p, {})
        for prop, what in (("P856", "official websites"), ("P2002", "X handles"), ("P6634", "LinkedIn IDs")):
            if len(person_links.get(prop, [])) > 1:
                notes.append(f"{p} ({name}): {len(person_links[prop])} current {what} on Wikidata; "
                             "left blank rather than picking one")
        x_handle = _single(person_links.get("P2002", []))
        linkedin_id = _single(person_links.get("P6634", []))
        people[p] = {
            "person_name": name,
            "wikidata_id": p,
            "wikipedia_url": wikipedia_url,
            "website_url": _single(person_links.get("P856", [])),
            "x_url": f"https://x.com/{urllib.parse.quote(x_handle)}" if x_handle else "",
            "linkedin_url": (f"https://www.linkedin.com/in/{urllib.parse.quote(linkedin_id)}/"
                             if linkedin_id else ""),
        }
    return people


# ---- Manual overrides -------------------------------------------------------------

def read_overrides(path: Path) -> "list[dict]":
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != OVERRIDE_COLUMNS:
            raise SystemExit(f"{path} doesn't have the expected columns: {', '.join(OVERRIDE_COLUMNS)}")
        overrides = [{k: (v or "").strip() for k, v in row.items()} for row in reader]
    for o in overrides:
        o["ticker"] = o["ticker"].upper()
        if o["action"] not in OVERRIDE_ACTIONS:
            raise SystemExit(f"{path}: unknown action \"{o['action']}\" for {o['ticker']} {o['person_name']}")
        if o["action"] == "board_checked":
            incomplete = not o["checked_on"] or not o["source_url"]
        else:
            incomplete = (not o["person_name"] or (o["action"] == "add" and not o["title"])
                          or (o["action"] == "note" and not o["note"]))
        if incomplete:
            raise SystemExit(f"{path}: incomplete {o['action']} row for {o['ticker']} {o['person_name']}")
    return overrides


def _same_name(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def apply_overrides(rows: "list[dict]", overrides: "list[dict]", company_of: "dict[str, str]",
                    notes: "list[str]") -> "list[dict]":
    """`rows` with the hand-maintained overrides applied on top (see the module docstring)."""
    rows = list(rows)
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    blank = {"wikidata_id": "", "wikipedia_url": "", "website_url": "", "x_url": "", "linkedin_url": ""}
    found: "dict[tuple[str, str], dict]" = {}  # (ticker, name) -> link columns
    board_checked: "dict[str, str]" = {}  # ticker -> checked_on

    def person_columns(ticker: str, name: str) -> dict:
        key = (ticker, name.casefold())
        if key in found:
            return found[key]
        existing = next((r for r in rows if r["ticker"] == ticker and r["wikidata_id"]
                         and _same_name(r["person_name"], name)), None)
        if existing is not None:
            found[key] = {c: existing[c] for c in blank}
        elif ticker not in company_of:
            notes.append(f"{ticker}: override person {name} not looked up -- no Wikidata item for the company")
            found[key] = dict(blank)
        else:
            matches = search_person(name, company_of[ticker])
            if len(matches) == 1:
                person = fetch_people(matches, notes)[matches[0]]
                found[key] = {c: person[c] for c in blank}
            else:
                why = ("no Wikidata item for a person of that name is linked to the company" if not matches
                       else f"{len(matches)} linked Wikidata items ({', '.join(matches)})")
                notes.append(f"{ticker}: override person {name} has no links from Wikidata -- {why}")
                found[key] = dict(blank)
        return found[key]

    for o in overrides:
        t, name, action = o["ticker"], o["person_name"], o["action"]
        if action == "board_checked":
            board_checked[t] = o["checked_on"]
            continue
        mine = [r for r in rows if r["ticker"] == t and _same_name(r["person_name"], name)]
        if action == "note":
            if not mine:
                notes.append(f"{t}: note override for {name} matched no row")
            for r in mine:
                r["note"], r["note_url"] = o["note"], o["source_url"]
            continue
        if action == "remove":
            drop = [r for r in mine if not o["title"] or r["title"] == o["title"]]
            if not drop:
                notes.append(f"{t}: remove override for {name} matched no row")
            rows = [r for r in rows if not any(r is d for d in drop)]
            continue
        title = CEO_TITLE if action == "replace_ceo" else (o["title"] or CHAIR_TITLE)
        columns = dict(person_columns(t, name))  # before dropping rows, so an existing row can supply the links
        if not columns["wikipedia_url"] and not columns["website_url"]:
            columns["website_url"] = o["fallback_url"]
        if not columns["wikidata_id"]:
            # wikidata_id is part of the table's key, so someone with no Wikidata item
            # gets a stand-in that can't be mistaken for a real Q-id
            columns["wikidata_id"] = f"manual:{name}"
        if action != "add":
            rank = 0 if action == "replace_ceo" else 1
            rows = [r for r in rows if not (r["ticker"] == t and title_rank(r["title"]) == rank)]
        else:
            rows = [r for r in rows if not (r["ticker"] == t and r["title"] == title
                                            and _same_name(r["person_name"], name))]
        rows.append({"ticker": t, "person_name": name, "title": title, **columns,
                     "source": OVERRIDE_SOURCE, "retrieved_at": retrieved_at, "confidence": "high",
                     "note": "", "note_url": "", "_why": ""})

    for r in rows:
        r["board_checked_on"] = board_checked.get(r["ticker"], "")

    # Keep each ticker's rows together, most senior title first.
    tickers = list(dict.fromkeys(r["ticker"] for r in rows))
    rows.sort(key=lambda r: (tickers.index(r["ticker"]), title_rank(r["title"])))
    return rows


def title_rank(title: str) -> int:
    """CEO, then any chair title ("Chairperson", "Executive Chairman", ...),
    then other officers, then board members."""
    if title == CEO_TITLE:
        return 0
    if "chair" in title.casefold():
        return 1
    return 3 if title == ROLES["P3320"][0] else 2


def keep_highest_role(rows: "list[dict]") -> "list[dict]":
    """Drop the board-member and other lower rows of anyone who has a "high"
    CEO or chair row at the same company."""
    def person(r):
        return r["ticker"], r["wikidata_id"] or r["person_name"].casefold()

    top = {person(r) for r in rows if r["confidence"] == "high" and title_rank(r["title"]) <= 1}
    return [r for r in rows if title_rank(r["title"]) <= 1 or person(r) not in top]


# ---- CSV and rule1.db -----------------------------------------------------------

def write_csv(rows: "list[dict]", path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> "list[dict]":
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise SystemExit(f"{path} doesn't have the expected columns ({', '.join(COLUMNS)}); "
                             "rebuild it by running without --from-csv")
        return list(reader)


def load_table(db_path: Path, csv_path: Path) -> int:
    """Drop and rebuild company_officers from the CSV in one transaction."""
    rows = read_csv(csv_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.isolation_level = None  # explicit BEGIN, so a failed load rolls the DROP back too
        with conn:
            conn.execute("BEGIN")
            conn.execute("DROP TABLE IF EXISTS company_officers")
            conn.execute(TABLE_SQL)
            conn.executemany(
                f"INSERT INTO company_officers ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                # blank cells are stored as NULL
                [[r[c] or None for c in COLUMNS] for r in rows],
            )
    finally:
        conn.close()
    return len(rows)


def db_tickers(db_path: Path) -> "list[str]":
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute("SELECT ticker FROM companies ORDER BY ticker")]
    finally:
        conn.close()


# ---- Report ---------------------------------------------------------------------

def print_report(rows: "list[dict]", tickers: "list[str]", notes: "list[str]") -> None:
    def links_of(r):
        found = [label for label, col in (("wikipedia", "wikipedia_url"), ("website", "website_url"),
                                          ("x", "x_url"), ("linkedin", "linkedin_url")) if r[col]]
        return ", ".join(found) or "-"

    name_w = max([len(r["person_name"]) for r in rows] + [6])
    title_w = max([len(r["title"]) for r in rows] + [5])
    print(f"\n{'ticker':<6}  {'person':<{name_w}}  {'title':<{title_w}}  {'wikidata':<11}  {'conf.':<6}  links")
    print(f"{'-' * 6}  {'-' * name_w}  {'-' * title_w}  {'-' * 11}  {'-' * 6}  {'-' * 5}")
    for r in rows:
        print(f"{r['ticker']:<6}  {r['person_name']:<{name_w}}  {r['title']:<{title_w}}  "
              f"{r['wikidata_id']:<11}  {r['confidence']:<6}  {links_of(r)}")

    print("\nCEO and Chair per ticker (\"high\" rows, as the app shows them):")
    for t in tickers:
        def holders(rank):
            names = [r["person_name"]
                     + (f" ({r['title']})" if r["title"] not in (CEO_TITLE, CHAIR_TITLE) else "")
                     + (" [manual override]" if r["source"] == OVERRIDE_SOURCE else "")
                     for r in rows
                     if r["ticker"] == t and title_rank(r["title"]) == rank and r["confidence"] == "high"]
            return ", ".join(names) or "(none)"
        print(f"  {t:<5} CEO: {holders(0):<40} Chair: {holders(1)}")

    print("\nBoard members per ticker (\"high\" rows; CEO and chair rows not counted):")
    for t in tickers:
        n = sum(r["ticker"] == t and r["confidence"] == "high" and r["title"] == ROLES["P3320"][0] for r in rows)
        checked = next((r.get("board_checked_on") for r in rows if r["ticker"] == t), "")
        print(f"  {t:<5} {n:>2}   " + (f"matched to the company's board page {checked}" if checked
                                      else "not checked against a board page"))

    people = {r["wikidata_id"] or r["person_name"] for r in rows}
    print(f"\n{len(rows)} rows, {len(people)} people, "
          f"{sum(r['confidence'] == 'high' for r in rows)} high / "
          f"{sum(r['confidence'] == 'review' for r in rows)} review")

    empty = [t for t in tickers if not any(r["ticker"] == t for r in rows)]
    print(f"\nTickers with zero officers: {', '.join(empty) or 'none'}")
    no_high = [t for t in tickers if t not in empty
               and not any(r["ticker"] == t and r["confidence"] == "high" for r in rows)]
    if no_high:
        print(f"Tickers with only \"review\" rows (the app shows none of them): {', '.join(no_high)}")

    review = [r for r in rows if r["confidence"] == "review"]
    print(f"\n\"review\" rows: {len(review) or 'none'}")
    for r in review:
        why = f" -- {r['_why']}" if r.get("_why") else ""
        print(f"  {r['ticker']:<5} {r['person_name']} ({r['title']}){why}")

    print("\nMissing links (people, not rows):")
    for label, col in (("Wikipedia article", "wikipedia_url"), ("official website", "website_url"),
                       ("X handle", "x_url"), ("LinkedIn ID", "linkedin_url")):
        have = {r["wikidata_id"] or r["person_name"] for r in rows if r[col]}
        print(f"  no {label}: {len(people - have)} of {len(people)}")
    no_name_link = sorted({r["person_name"] for r in rows if not r["wikipedia_url"] and not r["website_url"]})
    if no_name_link:
        print(f"  no Wikipedia article and no website, so the name isn't a link: {', '.join(no_name_link)}")

    if notes:
        print("\nNotes:")
        for n in notes:
            print(f"  {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build company_officers (CSV + rule1.db table) from Wikidata.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="path to rule1.db")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="path of the CSV to write / load")
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES,
                        help="path of the hand-maintained overrides CSV")
    parser.add_argument("--from-csv", action="store_true",
                        help="skip Wikidata and just rebuild the table from the existing CSV")
    args = parser.parse_args()

    if not args.db.exists():
        sys.exit(f"{args.db} doesn't exist; build it first with build_rule1_db.py")
    tickers = db_tickers(args.db)

    if args.from_csv:
        if not args.csv.exists():
            sys.exit(f"{args.csv} doesn't exist; run without --from-csv to build it")
        rows, notes = read_csv(args.csv), []
    else:
        rows, notes, company_of = build_rows(tickers)
        overrides = read_overrides(args.overrides)
        rows = keep_highest_role(apply_overrides(rows, overrides, company_of, notes))
        print(f"Applied {len(overrides)} overrides from {args.overrides}")
        write_csv(rows, args.csv)
        print(f"Wrote {len(rows)} rows to {args.csv}")

    loaded = load_table(args.db, args.csv)
    print(f"Loaded {loaded} rows into company_officers in {args.db}")
    print_report(rows, tickers, notes)


if __name__ == "__main__":
    main()
