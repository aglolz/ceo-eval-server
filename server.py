"""
CEO Voice Coach Eval — Live Scoring Server
Receives end-of-call webhooks from Vapi, runs judge prompts via Claude API,
writes scores to Supabase.

Deploy to Railway, set environment variables, point Vapi's webhook at the URL.
"""

import os
import json
import re
import logging
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify
import anthropic
import requests
from supabase import create_client

# ── Config ──────────────────────────────────────────────────────────────────

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
PROMPTS_DIR = Path(__file__).parent / "prompts"

# Judge dimensions to run — add new ones here as you build them
JUDGES = [
    {"name": "limits_the_load", "prompt": "limits_the_load.md"},
    {"name": "feedback_q_low_bar", "prompt": "feedback_question_low_bar.md"},
    # {"name": "drives_practice_uptake", "prompt": "drives_practice_uptake.md"},
    # {"name": "scaffolds_then_fades", "prompt": "scaffolds_then_fades.md"},
    # Add more as they're ready
]


# ── Vapi Transcript Fetcher ────────────────────────────────────────────────

def fetch_transcript_from_vapi(call_id, max_retries=3):
    """Fetch transcript from Vapi API with retries.

    Returns transcript string, or None if fetch fails after retries.
    """
    api_key = os.environ.get("VAPI_API_KEY")
    if not api_key:
        logger.error("VAPI_API_KEY not set")
        return None

    url = f"https://api.vapi.ai/call/{call_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    for attempt in range(max_retries):
        try:
            logger.info(f"Fetching transcript from Vapi (attempt {attempt + 1}/{max_retries})...")
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()

            data = resp.json()
            transcript = data.get("artifact", {}).get("transcript", "")
            if transcript:
                logger.info(f"Successfully fetched transcript ({len(transcript)} chars)")
                return transcript

            logger.warning(f"Vapi response has no transcript, retrying...")
        except requests.RequestException as e:
            logger.warning(f"Vapi API error: {e}")

        if attempt < max_retries - 1:
            time.sleep(5)

    logger.error(f"Failed to fetch transcript after {max_retries} attempts")
    return None


# ── Judge ───────────────────────────────────────────────────────────────────

def run_judge(transcript, rubric_path):
    """Score a transcript against a rubric. Same logic as judge.py."""
    client = anthropic.Anthropic()
    rubric = Path(rubric_path).read_text()
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
        return {"verdict": "error", "reasoning": f"API error: {e}"}

    text = next((b.text for b in resp.content if b.type == "text"), "")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(m.group(0) if m else text)
    except Exception:
        return {"verdict": "error", "reasoning": f"Non-JSON response: {text[:200]}"}

    verdict = str(out.get("verdict", "")).lower()
    if verdict not in {"pass", "fail"}:
        verdict = "error"

    return {
        "verdict": verdict,
        "reasoning": str(out.get("reasoning", "")),
        "scan": out.get("step1_scan", None),
    }


# ── Webhook Handler ─────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Receive end-of-call-report from Vapi, score, write to Supabase."""
    payload = request.get_json(force=True)

    # Verify this is an end-of-call report
    msg_type = payload.get("message", {}).get("type", "")
    if msg_type != "end-of-call-report":
        logger.info(f"Ignoring message type: {msg_type}")
        return jsonify({"status": "ignored", "type": msg_type}), 200

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
        transcript = fetch_transcript_from_vapi(call_id, max_retries=3)

    if not transcript:
        logger.warning(f"Call {call_id}: no transcript found after retries")
        return jsonify({"status": "error", "reason": "no transcript"}), 200

    # Compute duration
    duration_sec = None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        duration_sec = int((end - start).total_seconds())
    except Exception:
        pass

    logger.info(f"Call {call_id}: scoring {len(transcript)} chars across {len(JUDGES)} judges")

    # Run each judge
    scores = {}
    for j in JUDGES:
        rubric_path = PROMPTS_DIR / j["prompt"]
        if not rubric_path.exists():
            logger.error(f"Prompt not found: {rubric_path}")
            scores[j["name"]] = {"verdict": "error", "reasoning": "prompt file missing"}
            continue

        logger.info(f"  Running {j['name']}...")
        result = run_judge(transcript, rubric_path)
        scores[j["name"]] = result
        logger.info(f"  {j['name']}: {result['verdict']}")

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

    # Flatten judge results into columns
    for j in JUDGES:
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
        supabase.table("call_scores").insert(row).execute()
        logger.info(f"Call {call_id}: saved to Supabase")
    except Exception as e:
        logger.error(f"Call {call_id}: Supabase error: {e}")
        return jsonify({"status": "error", "reason": str(e)}), 500

    return jsonify({"status": "scored", "call_id": call_id, "scores": {
        name: scores[name]["verdict"] for name in scores
    }}), 200


# ── Health Check ────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    """Health check endpoint for Railway."""
    return jsonify({
        "status": "ok",
        "judges": [j["name"] for j in JUDGES],
        "model": MODEL,
    }), 200


# ── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
