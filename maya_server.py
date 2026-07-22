"""
Maya's Test Assistant — evaluates all 10 judges on Vapi calls.
Writes results to Supabase table: maya_test_calls

Deploy to Railway:
  - Set PROCFILE to point to this file
  - Set TABLE environment variable (or default to maya_test_calls)
"""

import os
import logging
from flask import Flask, request, jsonify
from server_lib import handle_call_webhook

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Maya's test instance: all 10 judges
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

TABLE = os.environ.get("SUPABASE_TABLE", "maya_test_calls")


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Receive end-of-call-report from Vapi, score, write to Supabase."""
    payload = request.get_json(force=True)
    response, status = handle_call_webhook(payload, JUDGES, TABLE)
    return jsonify(response), status


@app.route("/", methods=["GET"])
def health():
    """Health check endpoint for Railway."""
    return jsonify({
        "status": "ok",
        "instance": "maya_test",
        "judges": [j["name"] for j in JUDGES],
        "table": TABLE,
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
