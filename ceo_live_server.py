"""
CEO Live Scoring Pipeline — production instance.
Currently logs calls with NO judges. Judges will be added after validation.
Writes results to Supabase table: ceo_live_calls

Deploy to Railway:
  - Set PROCFILE to point to this file
  - Set TABLE environment variable (or default to ceo_live_calls)
"""

import os
import logging
from flask import Flask, request, jsonify
from server_lib import handle_call_webhook

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# CEO Live instance: NO judges yet (production validation phase)
# Will add all 10 judges once validation is complete
JUDGES = []

TABLE = os.environ.get("SUPABASE_TABLE", "ceo_live_calls")


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Receive end-of-call-report from Vapi, log call (no scoring yet)."""
    payload = request.get_json(force=True)
    response, status = handle_call_webhook(payload, JUDGES, TABLE)
    return jsonify(response), status


@app.route("/", methods=["GET"])
def health():
    """Health check endpoint for Railway."""
    return jsonify({
        "status": "ok",
        "instance": "ceo_live",
        "judges": [j["name"] for j in JUDGES] if JUDGES else "none (validation phase)",
        "table": TABLE,
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
