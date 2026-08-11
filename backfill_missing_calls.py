"""Backfill calls that Vapi completed but the webhook never scored into Supabase.

Some end-of-call-report webhooks die before the server can read them (see the
ConnectionResetError tracebacks in the Railway logs — long-call payloads are
the usual victims), so the call exists in Vapi but has no ceo_live_calls
row. This script replays those calls through the EXACT live scoring path:
it fetches the call from Vapi, wraps it in a synthetic end-of-call-report
payload, and hands it to server_lib.handle_call_webhook with ceo_live_server's
JUDGES list — same prompts, same message layout, same Supabase row shape.

It never touches the SMS/survey pipeline (that lives in the Flask route, not
in handle_call_webhook), and it skips any call_id already present in the
table, so re-runs are safe.

Usage (local, reads ~/ceo_voice_coach/.env for keys):
  python3 backfill_missing_calls.py --dry-run          # show what would run
  python3 backfill_missing_calls.py --limit 1          # score one call (bills API)
  python3 backfill_missing_calls.py                    # score everything missing
  python3 backfill_missing_calls.py --ids <id> [<id>…] # explicit call ids

By default it reconciles live: pulls every Vapi call for both arm assistants
since launch, diffs against the table, and backfills whatever has a transcript
but no row — so it also catches calls dropped after missing_from_supabase.tsv
was written.
"""

import argparse
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Keys live in the analysis repo's .env; map its names onto the ones
# server_lib expects (the Railway env uses VAPI_API_KEY / ARM_*_ASSISTANT_ID).
load_dotenv(Path.home() / "ceo_voice_coach" / ".env")
os.environ.setdefault("VAPI_API_KEY", os.environ.get("VAPI_PRIVATE_KEY", ""))

ARM_A = "68220648-f7ad-4724-b0c3-0df66619bf0a"
ARM_B = "e3225309-921f-43e4-ac0e-6995ff820ce2"
os.environ.setdefault("ARM_A_ASSISTANT_ID", ARM_A)  # so _arm_label() resolves
os.environ.setdefault("ARM_B_ASSISTANT_ID", ARM_B)

from server_lib import handle_call_webhook  # noqa: E402  (env must be set first)
from ceo_live_server import JUDGES, TABLE   # noqa: E402  the live judge roster

LAUNCH = "2026-07-28T00:04:37Z"  # live-number swap (dashboard LIVE_SINCE)
VAPI_H = {"Authorization": f"Bearer {os.environ['VAPI_API_KEY']}"}


def vapi_get(url, params=None, retries=4):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=VAPI_H, timeout=60)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"  vapi retry {attempt + 1}: {type(e).__name__}", file=sys.stderr)
            time.sleep(2)
    raise SystemExit(f"Vapi request failed after {retries} tries: {url}")


def list_arm_calls():
    """Every call for both arm assistants since launch (small pages — big ones
    get truncated mid-response by the API)."""
    calls = []
    for aid in (ARM_A, ARM_B):
        before = None
        while True:
            params = {"assistantId": aid, "limit": 10, "createdAtGe": LAUNCH}
            if before:
                params["createdAtLt"] = before
            batch = vapi_get("https://api.vapi.ai/call", params)
            if not batch:
                break
            calls += batch
            if len(batch) < 10:
                break
            before = batch[-1]["createdAt"]
    return calls


def supabase_call_ids():
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    H = {"apikey": key, "Authorization": f"Bearer {key}"}
    ids, off = set(), 0
    while True:
        r = requests.get(f"{url}/rest/v1/{TABLE}", params={"select": "call_id"},
                         headers={**H, "Range": f"{off}-{off + 999}"}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        ids |= {row["call_id"] for row in batch}
        if len(batch) < 1000:
            return ids
        off += 1000


def backfill_one(call):
    """Replay one Vapi call object through the live scoring path."""
    payload = {"message": {"type": "end-of-call-report", "call": call}}
    response, status = handle_call_webhook(payload, JUDGES, TABLE)
    return response, status


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ids", nargs="+", help="explicit Vapi call ids (skips the reconcile sweep)")
    ap.add_argument("--dry-run", action="store_true", help="list targets, don't score or write")
    ap.add_argument("--limit", type=int, help="backfill at most N calls")
    ap.add_argument("--min-user-turns", type=int, default=1,
                    help="skip calls with fewer participant turns (default 1)")
    args = ap.parse_args()

    existing = supabase_call_ids()
    print(f"{TABLE}: {len(existing)} rows")

    if args.ids:
        calls = [vapi_get(f"https://api.vapi.ai/call/{cid}") for cid in args.ids]
    else:
        calls = list_arm_calls()
        print(f"Vapi calls since {LAUNCH}: {len(calls)}")

    targets = []
    for c in sorted(calls, key=lambda c: c["createdAt"]):
        if c["id"] in existing:
            continue
        transcript = (c.get("artifact") or {}).get("transcript", "")
        user_turns = sum(1 for m in (c.get("artifact") or {}).get("messages", [])
                         if m.get("role") == "user")
        if not transcript or user_turns < args.min_user_turns:
            print(f"  skip {c['id']}  ({c['createdAt'][:16]}, user_turns={user_turns}, "
                  f"{len(transcript)} chars — nothing to score)")
            continue
        targets.append(c)

    if args.limit:
        targets = targets[:args.limit]

    print(f"\n{len(targets)} calls to backfill:")
    for c in targets:
        arm = "A" if c.get("assistantId") == ARM_A else "B"
        t = (c.get("artifact") or {}).get("transcript", "")
        print(f"  {c['id']}  arm={arm}  {c['createdAt'][:19]}  {len(t):>6} chars")
    if args.dry_run:
        print("\n(dry run — nothing scored or written)")
        return
    if not targets:
        return

    ok = failed = 0
    for i, c in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}] scoring {c['id']} …")
        try:
            response, status = backfill_one(c)
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            failed += 1
            continue
        if status == 200 and response.get("status") == "scored":
            verdicts = response.get("scores", {})
            fails = [k for k, v in verdicts.items() if v == "fail"]
            print(f"  scored ({len(verdicts)} judges; fails: {', '.join(fails) or 'none'})")
            ok += 1
        else:
            print(f"  NOT saved: status={status} response={response}")
            failed += 1

    print(f"\nDone: {ok} backfilled, {failed} failed.")


if __name__ == "__main__":
    main()
