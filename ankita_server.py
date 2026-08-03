"""
Ankita's Test Assistant — evaluates all 10 judges on Vapi calls.
Writes results to Supabase table: ankita_test_calls

Deploy to Railway:
  - Set PROCFILE to point to this file
  - Set TABLE environment variable (or default to ankita_test_calls)
"""

import os
import logging
import threading
import time
from xml.sax.saxutils import escape

import requests
from flask import Flask, request, jsonify, Response
from server_lib import handle_call_webhook, handle_assistant_request, resolve_table
import sms_feedback

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ankita's test instance: all 10 judges
JUDGES = [
    {"name": "limits_the_load", "prompt": "limits_the_load.md"},
    {"name": "feedback_q_low_bar", "prompt": "feedback_question_low_bar.md"},
    {"name": "drives_practice", "prompt": "drives_practice_v1_hero.yaml"},
    {"name": "scaffolds_then_fades", "prompt": "scaffolds_then_fades_v4_hero.yaml"},
    {"name": "quality_conversational_flow", "prompt": "quality_conversational_flow_v5_hero.yaml"},
    {"name": "adapts_when_stuck", "prompt": "adapts_when_stuck_v5_hero.yaml"},
    {"name": "reentry_appropriate_framing", "prompt": "reentry_appropriate_framing_v4_hero.yaml"},
    {"name": "pii", "prompt": "pii_v4_hero.yaml"},
    {"name": "makes_it_a_dialogue", "prompt": "makes_it_a_dialogue_v1.yaml"},
]

TABLE = os.environ.get("SUPABASE_TABLE", "ankita_test_calls")

# SMS bridge. Arm A's live assistant posts its end-of-call report straight to a
# Zapier hook that relays to the Google Apps Script -> Twilio SMS. Test-line arms
# post here to Railway instead, so we mirror the same report to that hook so
# evaluated calls still text. Fire-and-forget: a hook hiccup must never break
# scoring. Unset => no-op (safe default until configured on Railway).
ZAPIER_SMS_HOOK_URL = os.environ.get("ZAPIER_SMS_HOOK_URL", "").strip()

# Delay before sending survey Q1, so the summary SMS — which crawls through the
# Zapier chain (filter → python → sheets → Twilio) — reliably lands first.
SURVEY_DELAY_SEC = int(os.environ.get("SURVEY_DELAY_SEC", "75"))


def _forward_to_sms(payload):
    """Mirror a Vapi end-of-call report to the SMS/Sheets relay hook.
    Retries a couple of times — a single dropped attempt used to mean the
    call landed in Supabase but silently never reached Sheets, with no log
    line to show it. Success is now logged too, since the old code only
    logged failures — a quiet Railway log didn't mean delivery happened."""
    call_id = payload.get("message", {}).get("call", {}).get("id", "unknown")
    if not ZAPIER_SMS_HOOK_URL:
        logger.warning(f"Call {call_id}: ZAPIER_SMS_HOOK_URL not set — skipping Sheets/SMS forward")
        return
    last_err = None
    for attempt in range(3):
        try:
            requests.post(ZAPIER_SMS_HOOK_URL, json=payload, timeout=8)
            logger.info(f"Call {call_id}: forwarded to Sheets/SMS hook")
            return
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2)
    logger.warning(f"Call {call_id}: SMS/Sheets forward failed after 3 attempts: {last_err}")


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Vapi webhook. A/B-route on assistant-request (must answer within 7.5s);
    otherwise score the end-of-call-report and write to Supabase."""
    payload = request.get_json(force=True)
    msg_type = payload.get("message", {}).get("type", "")
    if msg_type == "assistant-request":
        response, status = handle_assistant_request(payload)
    else:
        # Simulation calls score with the same judges but land in their own
        # table, and never enter the participant SMS pipeline (summary/survey).
        table, is_sim = resolve_table(payload, TABLE)
        # Mirror end-of-call to the SMS pipeline BEFORE scoring, so texting isn't
        # delayed by judge latency (matches live Arm A's timing).
        if msg_type == "end-of-call-report" and not is_sim:
            threading.Thread(target=_forward_to_sms, args=(payload,), daemon=True).start()
            # Post-call feedback survey. Q1 is delayed SURVEY_DELAY_SEC so the
            # summary SMS (mirrored above, then crawling through Zapier) lands
            # first. Fired off the payload directly — independent of scoring
            # latency below. Best-effort, gated by FEEDBACK_SMS_ENABLED; never
            # raises into the webhook.
            call = payload.get("message", {}).get("call", {})
            q1 = threading.Timer(
                SURVEY_DELAY_SEC,
                sms_feedback.start_survey,
                args=(
                    call.get("id"),
                    call.get("customer", {}).get("number", ""),
                    call.get("assistantId", ""),
                    call.get("startedAt", ""),
                ),
            )
            q1.daemon = True
            q1.start()
        response, status = handle_call_webhook(payload, JUDGES, table)
    return jsonify(response), status


@app.route("/sms", methods=["POST"])
def handle_sms():
    """Twilio inbound webhook for the feedback survey. Twilio POSTs form-encoded
    From/Body; we reply with TwiML. handle_inbound is self-contained and never
    raises into the response path."""
    from_number = request.form.get("From", "")
    body = request.form.get("Body", "")
    reply = None
    try:
        reply = sms_feedback.handle_inbound(from_number, body)
    except Exception as e:
        logger.warning(f"/sms handling failed for {from_number}: {e}")
    inner = f"<Message>{escape(reply)}</Message>" if reply else ""
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response>{inner}</Response>'
    return Response(twiml, mimetype="text/xml")


@app.route("/", methods=["GET"])
def health():
    """Health check endpoint for Railway."""
    return jsonify({
        "status": "ok",
        "instance": "ankita_test",
        "judges": [j["name"] for j in JUDGES],
        "table": TABLE,
        "sim_table": os.environ.get("SIM_TABLE", "sim_calls"),
        "sim_assistant_ids": [s.strip() for s in os.environ.get("SIM_ASSISTANT_IDS", "").split(",") if s.strip()],
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
