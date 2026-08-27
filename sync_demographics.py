"""Refresh participant_demographics from the Coefficient-synced Google Sheet.

Feed shape:  Salesforce --(Coefficient)--> the "ceo codes for ai pilot" tab of
the participant tracker sheet --(Apps Script web app, JSON)--> this script
--> Supabase participant_demographics --> the hosted /dashboard join.

This replaces the manual path (hand-dropped Salesforce report*.csv +
ceo_voice_coach/push_demographics.py run on Maya's laptop). Rollups are
identical to that path -- age is bucketed and gender/race collapsed HERE, so
raw ages and free-text categories never reach Supabase.

Run:  python sync_demographics.py             # pull + upsert
      python sync_demographics.py --dry-run   # show headers/counts, write nothing

Env:  DEMOGRAPHICS_SHEET_URL    Apps Script web-app /exec URL (see
                                ceo_voice_coach/docs/runbooks/demographics_sheet_apps_script.md)
      DEMOGRAPHICS_SHEET_TOKEN  shared secret the web app checks
      SUPABASE_URL / SUPABASE_KEY
"""

import argparse
import logging
import os
import sys

import requests

logger = logging.getLogger(__name__)

TABLE = "participant_demographics"
BATCH = 1000

# Sheet header -> field. Coefficient names columns after the Salesforce fields,
# but a human can rename a column in the sheet at any time, so each field
# accepts a few spellings (matched case/space/punctuation-insensitively).
COLUMNS = {
    "ceo_id":     ["CEO Code", "CEO ID", "CEO Codes", "Participant: CEO Code"],
    "site":       ["CEO Location: Site Name", "Site", "Site Name", "Location"],
    "population": ["Referral Population", "Population"],
    "age":        ["Age at Enrollment", "Age"],
    "gender":     ["Participant: Gender", "Gender"],
    "race":       ["Participant: Race/Ethnicity", "Race/Ethnicity", "Race"],
    "education":  ["Education Level Sub-Category", "Education Level Acquired",
                   "Education Level", "Education"],
}

RACE_KEEP = {"Black or African American", "Hispanic or Latino", "White", "Asian",
             "American Indian/Alaskan Native"}


def _key(h):
    return "".join(c for c in (h or "").lower() if c.isalnum())


def norm_ceo(cid):
    """Join key: trim + drop leading zeros (must match dashboard.py's norm)."""
    cid = str(cid or "").strip()
    return cid.lstrip("0") or cid


def age_bucket(raw):
    try:
        a = int(float(raw))
    except (ValueError, TypeError):
        return "Unknown"
    return "18-24" if a < 25 else "25-34" if a < 35 else "35-44" if a < 45 else "45-54" if a < 55 else "55+"


def race_rollup(raw):
    raw = (raw or "").strip()
    if not raw:
        return "Unknown"
    if ";" in raw:
        return "Two or more"
    return raw if raw in RACE_KEEP else "Other"


def gender_rollup(raw):
    raw = (raw or "").strip()
    return raw if raw in ("Male", "Female") else "Other / undisclosed"


def fetch_sheet():
    """-> (headers, [row dict, ...]) from the Apps Script web app."""
    url = os.environ.get("DEMOGRAPHICS_SHEET_URL", "")
    token = os.environ.get("DEMOGRAPHICS_SHEET_TOKEN", "")
    if not url:
        sys.exit("Set DEMOGRAPHICS_SHEET_URL (Apps Script /exec URL) in the environment.")
    r = requests.get(url, params={"token": token}, timeout=60)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("ok"):
        sys.exit(f"Sheet endpoint refused: {payload.get('error', payload)}")
    return payload.get("headers", []), payload.get("rows", [])


def resolve_headers(headers):
    """field -> actual sheet header. Missing optional fields just stay Unknown."""
    seen = {_key(h): h for h in headers}
    mapping = {}
    for field, candidates in COLUMNS.items():
        for cand in candidates:
            if _key(cand) in seen:
                mapping[field] = seen[_key(cand)]
                break
    return mapping


def rollup(rows, mapping):
    """-> [{ceo_id, site, population, age, gender, race, education}, ...].

    One row per ENROLLMENT is possible (a re-enrolled participant repeats with a
    different age); keep the latest enrollment, i.e. the highest age seen.
    """
    def cell(r, field):
        col = mapping.get(field)
        return r.get(col, "") if col else ""

    best_age, out = {}, {}
    for r in rows:
        key = norm_ceo(cell(r, "ceo_id"))
        if not key:
            continue
        try:
            raw_age = int(float(cell(r, "age") or 0))
        except (ValueError, TypeError):
            raw_age = 0
        if key in best_age and raw_age < best_age[key]:
            continue
        best_age[key] = raw_age
        out[key] = {
            "ceo_id": key,
            "site": str(cell(r, "site") or "Unknown").replace("CEO ", "").strip() or "Unknown",
            "population": str(cell(r, "population") or "Unknown").strip() or "Unknown",
            "age": age_bucket(cell(r, "age")),
            "gender": gender_rollup(str(cell(r, "gender") or "")),
            "race": race_rollup(str(cell(r, "race") or "")),
            "education": str(cell(r, "education") or "Unknown").strip() or "Unknown",
        }
    return list(out.values())


def upsert(rows):
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "")
    if not (url and key):
        sys.exit("Set SUPABASE_URL and SUPABASE_KEY in the environment.")
    H = {"apikey": key, "Authorization": f"Bearer {key}",
         "Content-Type": "application/json",
         "Prefer": "resolution=merge-duplicates,return=minimal"}
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        r = requests.post(f"{url}/rest/v1/{TABLE}?on_conflict=ceo_id",
                          headers=H, json=chunk, timeout=60)
        if r.status_code == 404:
            sys.exit(f"  ! table {TABLE} missing — run "
                     "migrations/010_participant_demographics.sql in the Supabase "
                     "SQL editor first.")
        r.raise_for_status()
        logger.info(f"  upserted {min(i + BATCH, len(rows))}/{len(rows)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the sheet's headers, the resolved mapping and a "
                         "sample row; write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    headers, raw_rows = fetch_sheet()
    logger.info(f"  sheet: {len(raw_rows)} rows, {len(headers)} columns")
    mapping = resolve_headers(headers)

    if "ceo_id" not in mapping:
        sys.exit("  ! no CEO-code column found. Sheet headers were:\n    "
                 + "\n    ".join(headers)
                 + "\n  Add the right spelling to COLUMNS['ceo_id'] in this file.")
    missing = [f for f in COLUMNS if f not in mapping]
    if missing:
        logger.warning(f"  ! no column for {', '.join(missing)} — those facets "
                       "will read Unknown on the dashboard")

    rows = rollup(raw_rows, mapping)
    logger.info(f"  {len(rows)} participants after rollup")

    if args.dry_run:
        logger.info("  headers:  " + " | ".join(headers))
        logger.info("  mapping:  " + ", ".join(f"{f} <- {c}" for f, c in mapping.items()))
        if rows:
            logger.info(f"  sample:   {rows[0]}")
        logger.info("  (dry run — nothing written)")
        return

    upsert(rows)
    logger.info("  done. /dashboard picks it up within its 30-minute demo cache.")


if __name__ == "__main__":
    main()
