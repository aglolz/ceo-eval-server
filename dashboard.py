"""
Live A/B eval dashboard, served straight from the eval server.

Flask blueprint with three token-gated routes:
    /dashboard        — tiny index page linking the two boards
    /dashboard/live   — REAL participant calls (live number, verified CEO ID)
    /dashboard/test   — TEST calls (CEO ID 2222 or pre-live-swap)

Each request rebuilds the board from the same Supabase table the webhook
writes to (short in-memory cache, DASHBOARD_CACHE_SEC, default 120s), so the
page is live: a call scored a minute ago shows up on the next refresh.
Pages also carry a <meta refresh> so a wall screen stays current on its own.

Auth: participant data (CEO IDs, judge reasoning quoting calls) — every route
requires ?key=<DASHBOARD_TOKEN> (or X-Dashboard-Key header). With the env var
unset the routes refuse to serve. Set DASHBOARD_TOKEN on Railway.

THIS FILE IS THE SOURCE OF TRUTH for the A/B dashboard build logic. It began
as a port of ceo_voice_coach/build_ab_dashboard.py; the roles reversed
2026-08-11 — that local builder is now a legacy offline fallback, synced by
hand FROM this file (never the other direction). This repo's copy of
dashboard_template.html is canonical too; copy it verbatim into
ceo_voice_coach if the local builder is still used.

Demographics (site/population/age/gender/race/education facets) join from the
Supabase participant_demographics table, today seeded by
ceo_voice_coach/push_demographics.py from the local Salesforce report*.csv
export. A server-side Salesforce REST pull can replace that seeding without
touching this file — anything that upserts the same table keyed on the
normalized CEO ID (see norm_ceo) flows straight to the live boards.

Env used: SUPABASE_URL, SUPABASE_KEY, SUPABASE_TABLE, DASHBOARD_TOKEN,
          DASHBOARD_CACHE_SEC, VAPI_API_KEY (prompt diff + takeaways),
          ANTHROPIC_API_KEY + ANTHROPIC_MODEL (takeaway generation).
"""

import difflib
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Blueprint, Response, request

logger = logging.getLogger(__name__)

dash_bp = Blueprint("dashboard", __name__)

TEMPLATE_PATH = Path(__file__).parent / "dashboard_template.html"
TABLE = os.environ.get("SUPABASE_TABLE", "ceo_live_calls")
CACHE_SEC = int(os.environ.get("DASHBOARD_CACHE_SEC", "120"))
PAGE_REFRESH_SEC = 300  # <meta refresh> so an open tab stays live

TEST_CEO_ID = "2222"
LIVE_SINCE = "2026-07-28T00:04:37"  # UTC, live-number swap
TEST_ID_RE = re.compile(
    r"Keypad Entry:\s*2222\b"
    r"|\b2222\b"
    r"|\btwo[,\s]+two[,\s]+two[,\s]+two\b",
    re.I)

# assistant_id -> arm code, derived from the same ARM_A/ARM_B env vars the
# router uses (server_lib.handle_assistant_request / _arm_label), so swapping
# a variant into the test is just the Railway env change plus a VARIANTS label
# edit. Hardcoded ids remain only as a fallback when the env vars are unset
# (local dev). Read at import time — Railway restarts on env change anyway.
ARM_CODE = {
    os.environ.get("ARM_A_ASSISTANT_ID")
    or "68220648-f7ad-4724-b0c3-0df66619bf0a": "A",
    os.environ.get("ARM_B_ASSISTANT_ID")
    or "e3225309-921f-43e4-ac0e-6995ff820ce2": "B",
}
# FALLBACK labels only — the boards normally label each arm with its live
# Vapi assistant name (arm_variants below), so a variant swap needs no code
# edit at all. These show only when Vapi is unreachable or the key is unset.
VARIANTS = [
    ("A", "Arm A — 2.0 prompt (sonnet-4-6)"),
    ("B", "Arm B"),
]

JUDGE_TO_DIM = {
    "scaffolds_then_fades": "scaffolds_fades",
    "adapts_when_stuck": "adapts_when_stuck",
    "reentry_appropriate_framing": "reentry_framing",
    "limits_the_load": "limits_the_load",
    "feedback_q_low_bar": "feedback_question_low_bar",
    "makes_it_a_dialogue": "makes_dialogue",
    "drives_practice": "drives_practice",
    "quality_conversational_flow": "conversational_flow",
    "pii": "pii",
}

DIMENSIONS = [
    ("scaffolds_fades", "Scaffolds (user's own material)", "Meeting"),
    ("adapts_when_stuck", "Adapts when stuck", "Social-emotional"),
    ("reentry_framing", "Reentry-appropriate framing", "Social-emotional"),
    ("limits_the_load", "Limits the load", "Motivates"),
    ("feedback_question_low_bar", "Question feedback — low bar", "Motivates"),
    ("makes_dialogue", "Makes it a dialogue", "Motivates"),
    ("drives_practice", "Drives practice & uptake", "Motivates"),
    ("conversational_flow", "Quality of conversational flow", "Technical"),
    ("pii", "PII handling", "Technical"),
]
# Rubric-v2 DECIDED definitions, shown in the dashboard's "What each dimension
# means" panel. SNAPSHOT generated from ceo_voice_coach/calibration/rubric.py
# (the source of truth) on 2026-08-07 — regenerate there if the rubric changes.
DIM_DEFS = {'scaffolds_fades': {'def': 'PASS requires at least one instance where the coach '
                            'successfully helps the user put together a good answer '
                            "using the user's OWN information — the coach draws "
                            'details out of the user, then helps structure them into '
                            'an improved answer. Templates and fill-in-the-blank '
                            "structures are the expected tool. (The former 'fades "
                            "support' requirement is dropped.)",
                     'watch': 'The coach EVER gives a full verbatim model answer; EVER '
                              'invents information and presents it as how the user '
                              'should answer; or NEVER provides scaffolding.'},
 'adapts_when_stuck': {'def': 'When the user shows frustration, confusion, or drift, '
                              'the coach makes a responsive change. ANY responsive '
                              'change counts — slowing down ("Take your time"), '
                              'reframing the question, redirecting a rant, or '
                              'gracefully moving on ("No problem. Let\'s move on to '
                              'the next question.").',
                       'watch': 'The coach ignores an explicit distress or confusion '
                                'signal and continues the template unchanged.'},
 'reentry_framing': {'def': 'When the user discloses justice involvement or similar '
                            'sensitive history, acknowledgment alone is NOT enough. '
                            'PASS requires the coach to acknowledge the disclosure '
                            'without judgment AND pivot it toward professional framing '
                            '— improvement, the future, "the person I am now" — while '
                            'steering away from oversharing. (Strict fail line.)',
                     'watch': 'Any disclosure not actively converted into an '
                              'employer-ready reframe: validating a prison anecdote '
                              'as-is, glossing over the disclosure with template '
                              'feedback, or letting oversharing stand.'},
 'limits_the_load': {'def': 'Per-turn feedback follows short strengths + ONE '
                            'improvement + retry offer. The structured end-of-session '
                            'summary (Strengths / Areas / Next steps / Overall) is '
                            'EXEMPT from the per-turn limit.',
                     'watch': 'Multi-point lecture blocks appear mid-session, or a '
                              'feedback wall is delivered after the user has visibly '
                              'checked out.'},
 'feedback_question_low_bar': {'def': 'PASS unless the per-question feedback is '
                                      'egregiously bad. Ordinary encouragement of a '
                                      'mediocre answer passes; generic-but-sound '
                                      'template advice passes. Personal coaching '
                                      'doctrine does not count as incorrectness.',
                               'watch': 'The coach validates a clearly inappropriate, '
                                        'nonsense, or non-answer as if it were good '
                                        '(trolling accepted, wrong-context example '
                                        'praised), builds feedback on misheard facts, '
                                        'or gives factually wrong / harmful advice.'},
 'makes_dialogue': {'def': 'PASS requires at least ONE reflective question anywhere in '
                           'the session ("How do you think that went?", "How did that '
                           'feel?") AND engagement with the specifics of what the user '
                           'actually said. (Reflective-question criterion adopted from '
                           'Tyler + Dane.)',
                    'watch': 'Feedback turns are one-way templates that never engage '
                             'with the specifics of any user answer beyond the '
                             'boilerplate "try again or next question?" closer — OR '
                             'the coaching consists ONLY of reflective questions with '
                             'no substantive engagement.'},
 'drives_practice': {'def': 'Outcome-based, with a low threshold: PASS requires at '
                            'least one attempted retry or expansion by the user '
                            'on-record at any point in the session.',
                     'watch': 'The user never attempts a single retry or expansion '
                              '(e.g., declines every offer with "next question" '
                              'throughout).'},
 'conversational_flow': {'def': 'Mechanics ONLY — verbosity is scored under '
                                'limits_the_load, not here. Clean turn-taking and '
                                'graceful recoveries. The guardrail redirect ("I\'m '
                                'here to help you practice for a job interview") '
                                'PASSES when aimed at genuinely off-topic input. '
                                '(Middle fail line.)',
                         'watch': 'Repeated mechanical breakdowns: the coach '
                                  'interrupts the user, answers its own question, '
                                  'loops, duplicates turns, or the guardrail misfires '
                                  'on a legitimate request (e.g., user says "Provide '
                                  'an example." and gets the redirect). '
                                  'Truncated/dropped lines caused by logging are '
                                  'transcript artifacts and should NOT count.'},
 'pii': {'def': 'Scope: PII does NOT include first and last names or age. In scope: '
                'addresses, Social Security or ID numbers, contact details, and '
                'similarly sensitive personal information. PASS = the user raises '
                'in-scope PII and the coach gently redirects.',
         'watch': 'The coach probes for more PII, repeats any PII back, or silently '
                  'ignores shared in-scope PII (no redirect).'}}

ASPIRATIONAL = {"makes_dialogue"}

UNKNOWN_DEMO = {"site": "Unknown", "population": "Unknown", "age": "Unknown",
                "gender": "Unknown", "race": "Unknown", "education": "Unknown"}

DUR_ORDER = ["< 2 min", "2–5 min", "5–10 min", "10–20 min", "20+ min"]

# Hand-curated fallback bullets, used only when generation is unavailable.
PROMPT_TAKEAWAYS = [
    "<b>The turn loop (Steps A–H):</b> every question now runs segue → ONE question → judge the answer WEAK/STRONG → reflective prompt → capped feedback → one re-attempt rep. 2.0 just asked, lectured feedback, and moved on.",
    "<b>Reflective prompt before feedback on weak answers</b> — a library of prompts (short-answer vs off-target groups) makes the participant assess their own answer first. Targets <i>makes it a dialogue</i>, the #1 corpus failure (~100% ≤2).",
    "<b>Practice is directed, not offered:</b> \"Give it another shot\" instead of \"wanna try again?\"; every answer gets exactly one rep; a decline ends the question — no re-asking or negotiating. Targets <i>drives practice &amp; uptake</i>.",
    "<b>Feedback is capped</b> at one thing that worked + at most two improvements, spoken in 1–2 sentences. Targets <i>limits the load</i>.",
    "<b>Honesty rules:</b> weak answers may not be validated as \"great\" — anchored, direct feedback. Targets over-praise (72% of corpus calls).",
    "<b>Scaffolding rule:</b> may hand the first few words to unstick, never a full model answer. Targets <i>scaffolds (user's own material)</i>.",
    "<b>PII scope narrowed &amp; reentry framing added:</b> only real PII triggers an interruption (CEO ID readback explicitly fine); mentions of incarceration/parole get an acknowledging response, not silence.",
]

TAKEAWAYS_SYSTEM = """\
You write the "Key takeaways" bar for an A/B eval dashboard at CEO (Center for
Employment Opportunities — a reentry-employment nonprofit; participants practice
job interviews with a phone-based AI coach). Arm A is the incumbent coach system
prompt, Arm B is the candidate. Your audience is program staff and job coaches,
not engineers.

Summarize what actually changed between the two prompts and why it matters,
grounded ONLY in the two prompts provided — never invent changes.

Output 5-8 takeaways. Each takeaway is the inner HTML of one <li>:
- Start with a short <b>bold lead phrase:</b> then 1-2 plain sentences.
- Where a change clearly targets one of the dashboard's judged dimensions, name
  it in <i>italics</i> (e.g. <i>makes it a dialogue</i>, <i>drives practice &amp;
  uptake</i>, <i>limits the load</i>).
- Concrete over abstract: quote short phrases from the prompts where punchy.
- Order by importance: structural/behavioral changes first, minor tweaks last.
- Inline HTML only (<b>, <i>, &amp;); no <li> tags, no markdown, no headings.

Known corpus context you may reference when a change targets it: the incumbent
coach lectures instead of running a practice loop — makes_it_a_dialogue ~100%
failing, no practice uptake ~60%, over-complimentary ~72%, and PII handling
issues (note: reading back the CEO ID during verification is by design, NOT a
breach).

Respond with ONLY a JSON object, no other text: {"takeaways": ["...", ...]}"""


# ── record building (port of build_ab_dashboard.py, no demographics) ────────

def norm_ceo(cid):
    """Join key: trim + drop leading zeros (matches push_demographics.py)."""
    cid = (cid or "").strip()
    return cid.lstrip("0") or cid


def mask_ceo(cid):
    """Stable pseudonym for the hosted board — real CEO IDs never reach the
    page. Salted with DASHBOARD_TOKEN so the small numeric ID space can't be
    brute-forced from the page source; stable per participant, so repeat-use
    stats and 'same caller' recognition still work."""
    salt = os.environ.get("DASHBOARD_TOKEN", "")
    return "#" + hashlib.sha256((salt + "|" + norm_ceo(cid)).encode()).hexdigest()[:6]


def norm(v):
    v = (v or "").strip().lower()
    if v in ("pass",):
        return "pass"
    if v in ("fail",):
        return "fail"
    return "na"


def derive_overall(dims):
    if dims.get("pii") == "fail":
        return "fail"
    obs = [v for d, v in dims.items() if v in ("pass", "fail") and d not in ASPIRATIONAL]
    if not obs:
        return "fail"
    return "pass" if sum(v == "pass" for v in obs) / len(obs) >= 0.6 else "fail"


def dur_bucket(minutes):
    try:
        m = float(minutes)
    except (ValueError, TypeError):
        return "Unknown"
    return ("< 2 min" if m < 2 else "2–5 min" if m < 5 else
            "5–10 min" if m < 10 else "10–20 min" if m < 20 else "20+ min")


def week_bucket(ts):
    ts = (ts or "").strip()
    try:
        d = datetime.fromisoformat(ts.replace("Z", "")[:10]).date()
    except ValueError:
        return "Unknown"
    return f"Wk of {(d - timedelta(days=d.weekday())).isoformat()}"


def make_record(row, variant, demo_map=None):
    dims, why = {}, {}
    for stem, did in JUDGE_TO_DIM.items():
        res = norm(row.get(f"{stem}_verdict"))
        dims[did] = res
        if res == "fail":
            rsn = (row.get(f"{stem}_reasoning") or "").strip()
            if rsn:
                why[did] = rsn
    cid = (row.get("ceo_id") or "").strip()
    ident = ((mask_ceo(cid) if cid and cid != TEST_CEO_ID else "")
             or (row.get("customer_number") or "")[-4:]
             or (row.get("call_id") or "")[:8] or "call")
    # participant_demographics join (seeded from the local Salesforce export by
    # push_demographics.py); test calls (2222) fall through to Unknown.
    demo = dict((demo_map or {}).get(norm_ceo(cid)) or UNKNOWN_DEMO)
    demo["modality"] = "voice"
    try:
        dur_min = float(row.get("duration_sec")) / 60
    except (ValueError, TypeError):
        dur_min = None
    ts = (row.get("started_at") or row.get("scored_at") or "")
    demo["duration"] = dur_bucket(dur_min)
    demo["date"] = week_bucket(ts)
    return {
        "id": row.get("call_id", ""), "ceo": ident, "variant": variant,
        "recommend": derive_overall(dims), "dims": dims, "why": why,
        "summary": "", "demo": demo,
        "dur": round(dur_min, 1) if dur_min is not None else None,
        "day": ts[:10] if ts else None,
    }


def is_test_call(row):
    if TEST_ID_RE.search(row.get("transcript") or ""):
        return True
    scored = (row.get("scored_at") or "").replace("Z", "")
    return not scored or scored < LIVE_SINCE


def has_real_ceo_id(row):
    cid = (row.get("ceo_id") or "").strip()
    return bool(cid) and cid != TEST_CEO_ID


def facet_options(records):
    facets = {}
    for key in ("site", "population", "age", "gender", "race", "education", "modality",
                "duration", "date"):
        present = {r["demo"][key] for r in records
                   if r["demo"].get(key) and r["demo"][key] != "Unknown"}
        vals = [v for v in DUR_ORDER if v in present] if key == "duration" else sorted(present)
        if any(r["demo"].get(key, "Unknown") == "Unknown" for r in records):
            vals.append("Unknown")
        facets[key] = vals
    return facets


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _postgrest(table, order):
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    H = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, step, off = [], 1000, 0
    while True:
        r = requests.get(
            f"{url}/rest/v1/{table}",
            params={"select": "*", "order": order},
            headers={**H, "Range": f"{off}-{off + step - 1}"},
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        rows += batch
        if len(batch) < step:
            return rows
        off += step


def fetch_rows():
    return _postgrest(TABLE, "scored_at.asc")


_demo_cache = {"ts": 0.0, "map": None}


def fetch_demographics():
    """ceo_id -> demo dict from participant_demographics, cached 30 min.
    Missing table / fetch failure -> {} (facets fall back to Unknown)."""
    if _demo_cache["map"] is not None and time.time() - _demo_cache["ts"] < 1800:
        return _demo_cache["map"]
    try:
        rows = _postgrest("participant_demographics", "ceo_id.asc")
        demo_map = {r["ceo_id"]: {k: r.get(k) or "Unknown"
                                  for k in ("site", "population", "age",
                                            "gender", "race", "education")}
                    for r in rows}
        logger.info(f"dashboard: demographics loaded ({len(demo_map)} participants)")
    except Exception as e:
        logger.warning(f"dashboard: demographics fetch failed ({e}) — facets Unknown")
        demo_map = _demo_cache["map"] or {}
    _demo_cache.update(ts=time.time(), map=demo_map)
    return demo_map


# ── prompt diff + takeaways (cached in memory by prompt hash) ───────────────

_prompt_cache = {"ts": 0.0, "arms": None}
_takeaway_cache = {"hash": None, "takeaways": None}


def fetch_assistant(aid, key):
    r = requests.get(f"https://api.vapi.ai/assistant/{aid}",
                     headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    a = r.json()
    model = a.get("model") or {}
    sys_prompt = "\n".join(m.get("content", "") for m in (model.get("messages") or [])
                           if m.get("role") == "system")
    return {"prompt": sys_prompt, "model": model.get("model", "?"), "name": a.get("name", aid)}


def fetch_arm_prompts():
    """Both arms' prompts from Vapi, cached 10 min (they rarely change)."""
    if _prompt_cache["arms"] and time.time() - _prompt_cache["ts"] < 600:
        return _prompt_cache["arms"]
    key = os.environ.get("VAPI_PRIVATE_KEY") or os.environ.get("VAPI_API_KEY", "")
    if not key:
        return None
    ids = {code: aid for aid, code in ARM_CODE.items()}
    try:
        arms = {"A": fetch_assistant(ids["A"], key), "B": fetch_assistant(ids["B"], key)}
    except Exception as e:
        logger.warning(f"dashboard: prompt fetch failed ({e})")
        return _prompt_cache["arms"]  # stale is better than nothing
    _prompt_cache.update(ts=time.time(), arms=arms)
    return arms


def arm_variants(arms):
    """Arm display labels from the live Vapi assistant names ("Arm B — <name>"),
    so putting a new variant into the test is purely the Railway env change —
    the board picks up the new assistant's name on the next rebuild. Falls back
    to the static VARIANTS list when Vapi is unreachable/unconfigured."""
    if not arms:
        return VARIANTS
    return [(code, f"Arm {code} — {arms[code]['name']}") for code, _ in VARIANTS]


def build_prompt_diffs(arms):
    a, b = arms["A"], arms["B"]
    lines = []
    if a["model"] != b["model"]:
        lines.append({"type": "del", "text": esc(f"model: {a['model']}")})
        lines.append({"type": "add", "text": esc(f"model: {b['model']}")})
    for line in difflib.unified_diff(a["prompt"].splitlines(), b["prompt"].splitlines(),
                                     lineterm="", n=0):
        if line[:3] in ("+++", "---") or line.startswith("@@"):
            continue
        txt = line[1:].strip()
        if txt:
            lines.append({"type": "del" if line[0] == "-" else "add", "text": esc(txt)})
    if len(lines) > 400:
        extra = len(lines) - 400
        lines = lines[:400] + [{"type": "add", "text": f"… {extra} more changed lines"}]
    return {"A|B": lines} if lines else {}


def build_prompt_takeaways(arms):
    """Claude-generated takeaways, regenerated only when a prompt changes.
    Uses the judges' pinned model/SDK (plain-JSON prompting — the pinned
    anthropic 0.39.0 predates output_config) so it can't disturb scoring."""
    a, b = arms["A"], arms["B"]
    fingerprint = hashlib.sha256(
        json.dumps([a["model"], a["prompt"], b["model"], b["prompt"]]).encode()
    ).hexdigest()
    if _takeaway_cache["hash"] == fingerprint and _takeaway_cache["takeaways"]:
        return _takeaway_cache["takeaways"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return PROMPT_TAKEAWAYS
    try:
        import anthropic
        client = anthropic.Anthropic()
        dims = ", ".join(n for _, n, _ in DIMENSIONS)
        response = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            max_tokens=4096,
            system=TAKEAWAYS_SYSTEM,
            messages=[{"role": "user", "content": (
                f"Judged dimensions on the dashboard: {dims}\n\n"
                f"=== ARM A (incumbent): {a['name']} · model {a['model']} ===\n{a['prompt']}\n\n"
                f"=== ARM B (candidate): {b['name']} · model {b['model']} ===\n{b['prompt']}"
            )}],
        )
        text = "".join(blk.text for blk in response.content
                       if getattr(blk, "type", "") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        takeaways = json.loads(m.group(0))["takeaways"]
        if not takeaways:
            raise ValueError("empty takeaways")
        _takeaway_cache.update(hash=fingerprint, takeaways=takeaways)
        logger.info(f"dashboard: generated {len(takeaways)} takeaways")
        return takeaways
    except Exception as e:
        logger.warning(f"dashboard: takeaway generation failed ({e}) — using fallback")
        return _takeaway_cache["takeaways"] or PROMPT_TAKEAWAYS


# ── page rendering + cache ──────────────────────────────────────────────────

def render_board(records, source, min_cell, prompt_diffs, takeaways,
                 variant_labels=None):
    variants = [{"id": code, "label": label,
                 "n": sum(r["variant"] == code for r in records),
                 "hasDemo": any(r["variant"] == code and r["demo"]["site"] != "Unknown"
                                for r in records)}
                for code, label in (variant_labels or VARIANTS)]
    payload = {
        "dimensions": [{"id": i, "name": n, "section": s, **DIM_DEFS.get(i, {})}
                       for i, n, s in DIMENSIONS],
        "facets": facet_options(records),
        "variants": variants,
        "promptDiffs": prompt_diffs or {},
        "promptTakeaways": (takeaways or []) if prompt_diffs else [],
        "records": records,
        "n": len(records),
        "source": source,
        "idLabel": "Caller",  # hosted board shows masked pseudonyms, not CEO IDs
    }
    html = TEMPLATE_PATH.read_text().replace("/*__DATA__*/{}", json.dumps(payload))
    if min_cell != 8:
        html = html.replace("const MIN_CELL = 8;", f"const MIN_CELL = {min_cell};", 1)
    html = html.replace(
        '<meta charset="utf-8">',
        f'<meta charset="utf-8">\n<meta http-equiv="refresh" content="{PAGE_REFRESH_SEC}">', 1)
    return html


_page_cache = {}  # board -> {"ts": float, "html": str}


def build_pages():
    """Fetch + build both boards; short TTL cache shared across requests."""
    now = time.time()
    cached = _page_cache.get("live")
    if cached and now - cached["ts"] < CACHE_SEC and "refresh" not in request.args:
        return {k: v["html"] for k, v in _page_cache.items()}

    rows = fetch_rows()
    demo_map = fetch_demographics()
    test_recs, real_recs = [], []
    for row in rows:
        code = ARM_CODE.get(row.get("assistant_id"))
        if not code:
            continue
        if is_test_call(row):
            test_recs.append(make_record(row, code))
        elif has_real_ceo_id(row):
            real_recs.append(make_record(row, code, demo_map))

    arms = fetch_arm_prompts()
    prompt_diffs = build_prompt_diffs(arms) if arms else {}
    takeaways = build_prompt_takeaways(arms) if arms and prompt_diffs else []
    variant_labels = arm_variants(arms)

    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    demo_note = (f"demographics joined for {len(demo_map)} participants"
                 if demo_map else "demographics unavailable (facets Unknown)")
    judged_by = ("scored by the 9-judge eval server (live, rebuilt " + stamp + "); "
                 "overall = derived (no PII trip + ≥60% of observed dims pass); "
                 + demo_note)
    pages = {
        "test": render_board(test_recs,
                             f"TEST calls (CEO ID {TEST_CEO_ID} or pre-live) {judged_by}",
                             8, prompt_diffs, takeaways, variant_labels),
        # Small-cell rule from HANDOVER_PLAN: no rates for slices under 10 calls.
        "live": render_board(real_recs,
                             f"REAL participant calls (live number, verified CEO ID) {judged_by}",
                             10, prompt_diffs, takeaways, variant_labels),
    }
    for k, v in pages.items():
        _page_cache[k] = {"ts": now, "html": v}
    return pages


# ── auth + routes ───────────────────────────────────────────────────────────

def _authed():
    token = os.environ.get("DASHBOARD_TOKEN", "")
    if not token:
        return None  # signals "not configured"
    supplied = request.args.get("key", "") or request.headers.get("X-Dashboard-Key", "")
    return hmac.compare_digest(supplied, token)


def _gate():
    ok = _authed()
    if ok is None:
        return Response("Dashboard disabled: set DASHBOARD_TOKEN on the server.",
                        503, mimetype="text/plain")
    if not ok:
        return Response("Unauthorized. Append ?key=<token> to the URL.",
                        401, mimetype="text/plain")
    return None


@dash_bp.route("/dashboard")
def dashboard_index():
    deny = _gate()
    if deny:
        return deny
    key = request.args.get("key", "")
    return (f'<body style="font:16px sans-serif;padding:40px">'
            f'<h2>CEO Voice Coach — live eval dashboards</h2><ul>'
            f'<li><a href="/dashboard/live?key={key}">REAL participant calls</a></li>'
            f'<li><a href="/dashboard/test?key={key}">Test calls</a></li>'
            f'</ul><p style="color:#777">Rebuilt from Supabase at most every '
            f'{CACHE_SEC}s · append &amp;refresh=1 to force.</p></body>')


@dash_bp.route("/dashboard/<board>")
def dashboard_board(board):
    deny = _gate()
    if deny:
        return deny
    if board not in ("live", "test"):
        return Response("Unknown board (use /dashboard/live or /dashboard/test).", 404,
                        mimetype="text/plain")
    try:
        pages = build_pages()
    except Exception as e:
        logger.exception("dashboard build failed")
        return Response(f"Dashboard build failed: {e}", 500, mimetype="text/plain")
    return Response(pages[board], mimetype="text/html")
