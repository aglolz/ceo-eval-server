"""
Shared library for CEO Voice Coach Eval pipelines.
Contains judge runners, transcript fetcher, and Supabase writer.
ceo_live_server.py imports this and defines the JUDGES list and TABLE name
(a test instance is the same entry point with different env — see README).
"""

import os
import json
import re
import random
import logging
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import requests
import yaml
from supabase import create_client

logger = logging.getLogger(__name__)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
PROMPTS_DIR = Path(__file__).parent / "prompts"


# ── Vapi Transcript Fetcher ────────────────────────────────────────────────

def fetch_transcript_from_vapi(call_id, max_retries=3):
    """Fetch transcript and call metadata from Vapi API with retries.

    Returns dict with transcript, startedAt, endedAt, or None if fetch fails.
    """
    api_key = os.environ.get("VAPI_API_KEY")
    if not api_key:
        logger.error("VAPI_API_KEY not set")
        return None

    url = f"https://api.vapi.ai/call/{call_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    for attempt in range(max_retries):
        try:
            logger.info(f"Fetching call data from Vapi (attempt {attempt + 1}/{max_retries})...")
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()

            data = resp.json()
            transcript = data.get("artifact", {}).get("transcript", "")
            if transcript:
                logger.info(f"Successfully fetched transcript ({len(transcript)} chars)")
                return {
                    "transcript": transcript,
                    "startedAt": data.get("startedAt", ""),
                    "endedAt": data.get("endedAt", ""),
                }

            logger.warning(f"Vapi response has no transcript, retrying...")
        except requests.RequestException as e:
            logger.warning(f"Vapi API error: {e}")

        if attempt < max_retries - 1:
            time.sleep(5)

    logger.error(f"Failed to fetch transcript after {max_retries} attempts")
    return None


# ── Judge Runners ──────────────────────────────────────────────────────────

def run_judge(transcript, rubric_path):
    """Score a transcript against one judge. Dispatches on file extension:
    .yaml -> structured hill-climbing judge (calibrated path); else the inline
    Markdown-template judge. Both return {verdict, reasoning, scan}."""
    rubric_path = Path(rubric_path)
    if rubric_path.suffix in (".yaml", ".yml"):
        return run_yaml_judge(transcript, rubric_path)
    return run_md_judge(transcript, rubric_path)


def run_md_judge(transcript, rubric_path, retries=2):
    """Markdown-template judge: the file IS the system prompt with a
    {transcript} placeholder; expects {verdict, reasoning, step1_scan}.

    Hardened to match run_yaml_judge: uses _robust_json_parse (strips ```json
    fences / tolerates trailing commas / extracts the first object) and retries
    a couple of times. The old naive re.search + json.loads intermittently
    failed (~1-in-7) on longer or fenced model outputs — see the 'error'
    verdicts on limits_the_load — even when the JSON was well-formed."""
    client = anthropic.Anthropic()
    rubric = rubric_path.read_text()
    system = rubric.replace("{transcript}", transcript)

    last_err = None
    for _ in range(retries + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=1536,
                temperature=0,
                system=system,
                messages=[{"role": "user", "content": "Evaluate and return JSON only."}],
            )
        except anthropic.APIError as e:
            return {"verdict": "error", "reasoning": f"API error: {e}", "scan": None}

        text = next((b.text for b in resp.content if b.type == "text"), "").strip()
        try:
            out = _robust_json_parse(text)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = f"Non-JSON response: {text[:200]}"
            continue

        verdict = str(out.get("verdict", "")).lower()
        if verdict not in {"pass", "fail"}:
            last_err = f"bad verdict value: {out.get('verdict')!r}"
            continue

        return {
            "verdict": verdict,
            "reasoning": str(out.get("reasoning", "")),
            "scan": out.get("step1_scan", None),
        }

    return {"verdict": "error", "reasoning": last_err or "unparseable", "scan": None}


# The structured-judge path below is ported VERBATIM from
# judge-suite/scripts/eval_harness_v2.py (build_system_prompt, _robust_json_parse,
# the temperature=0 + retry loop) so a judge scores a live call identically to how
# it scored the calibration set. Do not "clean up" these to share code with the
# Markdown path — byte-for-byte parity with the calibrated harness is the point.

def build_system_prompt(p):
    return f"""You are an expert evaluator of AI voice coaching sessions.

Your task is to evaluate one specific coaching dimension:

DIMENSION: {p['dimension']}

DEFINITION:
{p['definition']}

PASS — what it looks like:
{p['pass']}

FAIL — what it looks like:
{p['fail']}

N/A — when to use it:
{p['na']}

Evaluate ONLY this dimension. Base your judgment solely on what is observable in the transcript.

Respond in JSON only. No markdown, no code blocks, no extra text:
{{"result": "PASS" or "FAIL" or "N/A", "evidence": "<copy the key exchange verbatim, max 200 chars>", "reasoning": "<one sentence explaining your verdict>"}}"""


def _robust_json_parse(raw):
    """Parse the judge's JSON tolerantly: strip code fences, drop trailing
    commas, and fall back to extracting the first {...} block."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    for candidate in (text, re.sub(r",(\s*[}\]])", r"\1", text)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start != -1:
        for candidate in (text[start:], re.sub(r",(\s*[}\]])", r"\1", text[start:])):
            try:
                obj, _ = json.JSONDecoder().raw_decode(candidate)
                return obj
            except json.JSONDecodeError:
                pass
    raise ValueError(f"could not parse JSON from: {raw[:200]!r}")


def run_yaml_judge(transcript, prompt_path, retries=2):
    """Structured judge — mirrors eval_harness_v2.run_judge exactly:
    temperature=0 (auto-dropped for models that reject it), tolerant JSON parse,
    a couple of retries. Maps result PASS/FAIL/N/A -> verdict pass/fail/na and
    stores the verbatim evidence quote in `scan`."""
    client = anthropic.Anthropic()
    prompt_data = yaml.safe_load(prompt_path.read_text())
    system_prompt = build_system_prompt(prompt_data)

    last_err = None
    send_temp = True
    attempts = 0
    while attempts < retries + 1:
        kwargs = dict(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": f"TRANSCRIPT:\n\n{transcript}"}],
        )
        if send_temp:
            kwargs["temperature"] = 0
        try:
            message = client.messages.create(**kwargs)
        except anthropic.BadRequestError as e:
            if send_temp and "temperature" in str(e).lower():
                send_temp = False  # this model rejects temperature — retry without it
                continue
            return {"verdict": "error", "reasoning": f"API error: {e}", "scan": None}
        except anthropic.APIError as e:
            return {"verdict": "error", "reasoning": f"API error: {e}", "scan": None}

        attempts += 1
        raw = next((b.text for b in message.content if b.type == "text"), "").strip()
        try:
            data = _robust_json_parse(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            continue
        result = str(data.get("result", "")).strip().upper()
        if result not in ("PASS", "FAIL", "N/A"):
            last_err = ValueError(f"bad result value: {data.get('result')!r}")
            continue
        verdict = {"PASS": "pass", "FAIL": "fail", "N/A": "na"}[result]
        return {
            "verdict": verdict,
            "reasoning": str(data.get("reasoning", "")),
            "scan": {"evidence": data.get("evidence", "")},
        }

    return {"verdict": "error", "reasoning": f"unparseable after {retries + 1} attempts: {last_err}", "scan": None}


# ── CEO ID extraction ──────────────────────────────────────────────────────
#
# The participant ID the caller verified with. Test calls use 2222; real
# participants use their own ID — downstream consumers split test vs real on
# this instead of grepping transcripts. Three sources, most reliable first:
#   1. the telephony keypad line Vapi injects ("User's Keypad Entry: 2222")
#   2. the validate_ceo_id tool call in the artifact messages (args + result)
#   3. digits/digit-words spoken in a User turn that mentions the CEO ID
# Returns None when no ID is observable (e.g. the call died pre-verification).

_KEYPAD_ID_RE = re.compile(r"Keypad Entry:\s*#?(\d{2,10})")
_SPOKEN_DIGITS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3",
                  "four": "4", "five": "5", "six": "6", "seven": "7",
                  "eight": "8", "nine": "9"}


def _ceo_id_from_tool_calls(payload):
    """Pull ceo_id from validate_ceo_id messages in the webhook artifact.
    Prefers the tool_call_result (backend-verified) over the model's own
    arguments (which carry whatever the STT heard)."""
    msg = payload.get("message", {})
    from_args = None
    for artifact in (msg.get("artifact", {}), msg.get("call", {}).get("artifact", {})):
        for m in artifact.get("messages", []) or []:
            if m.get("role") == "tool_call_result" and m.get("name") == "validate_ceo_id":
                blob = m.get("metadata", {}).get("responseBody") or m.get("result", "")
                if not isinstance(blob, dict):
                    try:
                        blob = json.loads(blob)
                    except (ValueError, TypeError):
                        continue
                cid = str(blob.get("ceo_id", "")).strip()
                if cid.isdigit():
                    return cid
            for tc in m.get("toolCalls", []) or []:
                fn = tc.get("function", {})
                if fn.get("name") != "validate_ceo_id":
                    continue
                args = fn.get("arguments", {})
                if not isinstance(args, dict):
                    try:
                        args = json.loads(args)
                    except (ValueError, TypeError):
                        continue
                cid = str(args.get("ceo_id", "")).strip()
                if cid.isdigit():
                    from_args = from_args or cid
    return from_args


def _ceo_id_from_spoken(transcript):
    """Digits spoken in a User turn mentioning the CEO ID, e.g.
    'My CEO ID is two two two two.' or 'It's 2222.'"""
    for line in transcript.splitlines():
        if not line.startswith("User:") or "ceo id" not in line.lower():
            continue
        tail = re.split(r"CEO\s*ID", line, flags=re.I)[-1]
        run = ""
        for tok in re.findall(r"\d+|[a-z]+", tail.lower()):
            if tok.isdigit():
                run += tok
            elif tok in _SPOKEN_DIGITS:
                run += _SPOKEN_DIGITS[tok]
            elif run:
                break  # digit run ended (ignore leading filler like "is")
        if 2 <= len(run) <= 10:
            return run
    return None


def extract_ceo_id(transcript, payload):
    """Best-effort CEO ID for a call, or None."""
    m = _KEYPAD_ID_RE.search(transcript or "")
    if m:
        return m.group(1)
    return _ceo_id_from_tool_calls(payload) or _ceo_id_from_spoken(transcript or "")


# ── A/B Routing (assistant-request) ────────────────────────────────────────
#
# For the 50/50 experiment an inbound phone number points at this server with
# no static assistantId, so Vapi sends an `assistant-request` and expects the
# chosen assistant within 7.5s (telephony cap) — never score on this path. The
# returned assistantId reappears on the later end-of-call-report, so the arm is
# self-logging via the existing assistant_id column (no schema change).
#
# Env: ARM_A_ASSISTANT_ID / ARM_B_ASSISTANT_ID — the two arms.
#      AB_FORCE_ARM = "A" | "B" — kill switch pinning all traffic to one arm.

def handle_assistant_request(payload):
    """Pick an A/B arm and return its assistantId fast. Returns (dict, status)."""
    arms = {
        "A": os.environ.get("ARM_A_ASSISTANT_ID", ""),
        "B": os.environ.get("ARM_B_ASSISTANT_ID", ""),
    }
    force = os.environ.get("AB_FORCE_ARM", "").strip().upper()
    arm = force if force in ("A", "B") else random.choice(["A", "B"])
    assistant_id = arms.get(arm, "")

    call_id = payload.get("message", {}).get("call", {}).get("id", "unknown")
    if not assistant_id:
        # Don't 500 — an errored body lets Vapi fall back to any
        # fallbackDestination configured on the number.
        logger.error(f"assistant-request {call_id}: no id for arm {arm} "
                     f"(set ARM_{arm}_ASSISTANT_ID)")
        return {"error": f"no assistant configured for arm {arm}"}, 200

    logger.info(f"assistant-request {call_id}: -> arm {arm} ({assistant_id})"
                + (" [forced]" if force in ("A", "B") else ""))
    return {"assistantId": assistant_id}, 200


def _arm_label(assistant_id):
    """Reverse-map an end-of-call assistant_id back to its A/B arm label from the
    same ARM_A/ARM_B env config used to route. Empty string when it matches
    neither arm (a non-experiment assistant, or the env changed since the call)."""
    if assistant_id and assistant_id == os.environ.get("ARM_A_ASSISTANT_ID"):
        return "A"
    if assistant_id and assistant_id == os.environ.get("ARM_B_ASSISTANT_ID"):
        return "B"
    return ""


# Simulation traffic split. Calls answered by a designated simulation assistant
# (e.g. the sim phone line's copy-of-arm-B) are scored by the same judges but
# stored in their own Supabase table, so synthetic runs never mix with live or
# test A/B data. The dedicated sim number points straight at its assistant, so
# sim calls only ever hit the end-of-call path — never assistant-request.
#
# Env: SIM_ASSISTANT_IDS — comma-separated assistantIds to divert.
#      SIM_TABLE — destination table for diverted calls (default "sim_calls").

def resolve_table(payload, default_table):
    """Return (table_name, is_sim) for an end-of-call payload: the sim table when
    the call's assistantId is listed in SIM_ASSISTANT_IDS, else default_table."""
    sim_ids = {s.strip() for s in os.environ.get("SIM_ASSISTANT_IDS", "").split(",") if s.strip()}
    assistant_id = payload.get("message", {}).get("call", {}).get("assistantId", "")
    if assistant_id and assistant_id in sim_ids:
        return os.environ.get("SIM_TABLE", "sim_calls"), True
    return default_table, False


# ── Webhook Handler ────────────────────────────────────────────────────────

def handle_call_webhook(payload, judges, table_name):
    """Process end-of-call-report from Vapi.

    Args:
        payload: The webhook JSON from Vapi
        judges: List of judge dicts with 'name' and 'prompt' keys
        table_name: Supabase table to write results to

    Returns:
        (response_dict, status_code)
    """
    # Verify this is an end-of-call report
    msg_type = payload.get("message", {}).get("type", "")
    if msg_type != "end-of-call-report":
        logger.info(f"Ignoring message type: {msg_type} | Full keys: {list(payload.get('message', {}).keys())}")
        return {"status": "ignored", "type": msg_type}, 200

    # Extract call data
    call = payload.get("message", {}).get("call", {})
    call_id = call.get("id", "unknown")
    assistant_id = call.get("assistantId", "")
    customer_number = call.get("customer", {}).get("number", "")
    started_at = call.get("startedAt", "")
    ended_at = call.get("endedAt", "")
    transcript = call.get("artifact", {}).get("transcript", "")

    # If no transcript in webhook, try fetching from Vapi API
    if not transcript:
        logger.warning(f"Call {call_id}: no transcript in webhook, fetching from Vapi API...")
        time.sleep(5)  # Wait 5 seconds before first fetch
        api_result = fetch_transcript_from_vapi(call_id, max_retries=3)
        if api_result:
            transcript = api_result.get("transcript", "")
            # Update timestamps from API if they were empty in webhook
            if not started_at:
                started_at = api_result.get("startedAt", "")
            if not ended_at:
                ended_at = api_result.get("endedAt", "")

    # Compute duration
    duration_sec = None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        duration_sec = int((end - start).total_seconds())
    except Exception:
        pass

    # No transcript after retries: still record the call in Supabase (unscored)
    # instead of dropping it silently — a missing transcript must not mean a
    # missing row. The Sheets/SMS forward already fired independently of this,
    # so this call must not go Sheets-only just because scoring couldn't run.
    judges_to_run = judges if transcript else []
    if not transcript:
        logger.warning(f"Call {call_id}: no transcript found after retries — saving unscored row")
    else:
        logger.info(f"Call {call_id}: scoring {len(transcript)} chars across {len(judges_to_run)} judges on table '{table_name}'")

    # Run judges in parallel
    def run_single_judge(judge_dict):
        """Run one judge and return (judge_name, result).

        run_judge already catches anthropic.APIError internally, but a raw
        connection/SSL exception can escape that (confirmed in practice —
        "unknown error (_ssl.c:4293)"). Uncaught, that exception propagates
        through future.result() below with nothing catching it further up
        the stack, which skips the Supabase write entirely and drops the
        call from Supabase with no trace at all — the same failure this
        function's caller already guards against for the no-transcript case,
        just via a different trigger. Retry a couple of times, then degrade
        to a normal error verdict instead of letting it propagate."""
        j = judge_dict
        rubric_path = PROMPTS_DIR / j["prompt"]
        if not rubric_path.exists():
            logger.error(f"Prompt not found: {rubric_path}")
            return (j["name"], {"verdict": "error", "reasoning": "prompt file missing"})

        logger.info(f"  Running {j['name']}...")
        last_err = None
        for attempt in range(3):
            try:
                result = run_judge(transcript, rubric_path)
                logger.info(f"  {j['name']}: {result['verdict']}")
                return (j["name"], result)
            except Exception as e:
                last_err = e
                logger.warning(f"  {j['name']}: attempt {attempt + 1}/3 raised {e}")
                if attempt < 2:
                    time.sleep(2)
        logger.error(f"  {j['name']}: failed after 3 attempts with raw exception: {last_err}")
        return (j["name"], {"verdict": "error", "reasoning": f"exception: {last_err}"})

    scores = {}
    with ThreadPoolExecutor(max_workers=len(judges_to_run) or 1) as executor:
        futures = {executor.submit(run_single_judge, j): j["name"] for j in judges_to_run}
        for future in as_completed(futures):
            judge_name, result = future.result()
            scores[judge_name] = result

    # Write to Supabase
    row = {
        "call_id": call_id,
        "assistant_id": assistant_id,
        "arm_label": _arm_label(assistant_id),
        "ceo_id": extract_ceo_id(transcript, payload),
        "customer_number": customer_number,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_sec": duration_sec,
        "transcript": transcript,
        "scored_at": datetime.utcnow().isoformat() if transcript else None,
    }

    # Flatten judge results into columns (only if judges were run)
    for j in judges:
        name = j["name"]
        if name in scores:
            row[f"{name}_verdict"] = scores[name]["verdict"]
            row[f"{name}_reasoning"] = scores[name]["reasoning"]
            if scores[name].get("scan"):
                row[f"{name}_scan"] = json.dumps(scores[name]["scan"])

    # Convert empty strings to None for date fields
    for key in ['started_at', 'ended_at', 'scored_at', 'transcript']:
        if row.get(key) == '':
            row[key] = None

    # Retry the Supabase write — a transient network/API hiccup here used to
    # mean the call was scored (Claude API cost already spent) but never
    # landed in Supabase at all, with no way to tell from the Sheets side.
    supabase_error = None
    for attempt in range(3):
        try:
            supabase = create_client(
                os.environ["SUPABASE_URL"],
                os.environ["SUPABASE_KEY"]
            )
            supabase.table(table_name).insert(row).execute()
            logger.info(f"Call {call_id}: saved to Supabase table '{table_name}'")
            supabase_error = None
            break
        except Exception as e:
            supabase_error = e
            logger.warning(f"Call {call_id}: Supabase insert attempt {attempt + 1}/3 failed: {e}")
            if attempt < 2:
                time.sleep(2)

    if supabase_error is not None:
        logger.error(f"Call {call_id}: Supabase error after 3 attempts: {supabase_error}")
        return {"status": "error", "reason": str(supabase_error)}, 500

    if not transcript:
        return {"status": "saved_unscored", "call_id": call_id, "reason": "no transcript"}, 200

    return {"status": "scored", "call_id": call_id, "scores": {
        name: scores[name]["verdict"] for name in scores
    }}, 200
