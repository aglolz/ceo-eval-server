"""
Shared library for CEO Voice Coach Eval pipelines.
Contains judge runners, transcript fetcher, and Supabase writer.
Individual servers (ankita_server.py, maya_server.py, ceo_live_server.py)
import this and define their JUDGES list and TABLE name.
"""

import os
import json
import re
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


def run_md_judge(transcript, rubric_path):
    """Markdown-template judge: the file IS the system prompt with a
    {transcript} placeholder; expects {verdict, reasoning, step1_scan}."""
    client = anthropic.Anthropic()
    rubric = rubric_path.read_text()
    system = rubric.replace("{transcript}", transcript)

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": "Evaluate and return JSON only."}],
        )
    except anthropic.APIError as e:
        return {"verdict": "error", "reasoning": f"API error: {e}", "scan": None}

    text = next((b.text for b in resp.content if b.type == "text"), "")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(m.group(0) if m else text)
    except Exception:
        return {"verdict": "error", "reasoning": f"Non-JSON response: {text[:200]}", "scan": None}

    verdict = str(out.get("verdict", "")).lower()
    if verdict not in {"pass", "fail"}:
        verdict = "error"

    return {
        "verdict": verdict,
        "reasoning": str(out.get("reasoning", "")),
        "scan": out.get("step1_scan", None),
    }


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

    if not transcript:
        logger.warning(f"Call {call_id}: no transcript found after retries")
        return {"status": "error", "reason": "no transcript"}, 200

    # Compute duration
    duration_sec = None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        duration_sec = int((end - start).total_seconds())
    except Exception:
        pass

    logger.info(f"Call {call_id}: scoring {len(transcript)} chars across {len(judges)} judges on table '{table_name}'")

    # Run judges in parallel
    def run_single_judge(judge_dict):
        """Run one judge and return (judge_name, result)."""
        j = judge_dict
        rubric_path = PROMPTS_DIR / j["prompt"]
        if not rubric_path.exists():
            logger.error(f"Prompt not found: {rubric_path}")
            return (j["name"], {"verdict": "error", "reasoning": "prompt file missing"})

        logger.info(f"  Running {j['name']}...")
        result = run_judge(transcript, rubric_path)
        logger.info(f"  {j['name']}: {result['verdict']}")
        return (j["name"], result)

    scores = {}
    with ThreadPoolExecutor(max_workers=len(judges)) as executor:
        futures = {executor.submit(run_single_judge, j): j["name"] for j in judges}
        for future in as_completed(futures):
            judge_name, result = future.result()
            scores[judge_name] = result

    # Write to Supabase
    row = {
        "call_id": call_id,
        "assistant_id": assistant_id,
        "customer_number": customer_number,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_sec": duration_sec,
        "transcript": transcript,
        "scored_at": datetime.utcnow().isoformat(),
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
    for key in ['started_at', 'ended_at', 'scored_at']:
        if row.get(key) == '':
            row[key] = None

    try:
        supabase = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"]
        )
        supabase.table(table_name).insert(row).execute()
        logger.info(f"Call {call_id}: saved to Supabase table '{table_name}'")
    except Exception as e:
        logger.error(f"Call {call_id}: Supabase error: {e}")
        return {"status": "error", "reason": str(e)}, 500

    return {"status": "scored", "call_id": call_id, "scores": {
        name: scores[name]["verdict"] for name in scores
    }}, 200
