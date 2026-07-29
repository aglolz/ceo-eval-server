"""SMS participant-feedback survey — post-summary, two questions, Twilio.

Flow:
  call ends -> Vapi sends the summary SMS -> this server's /webhook finishes
  scoring -> we send Q1 ("how realistic, 1-5") -> participant replies to our
  Twilio number -> Twilio POSTs /sms -> we record, send Q2 ("recommend Y/M/N")
  -> record, thank, done.

Attribution is exact and needs no join: when we send Q1 we already know the
call (call_id, assistant_id = the arm, started_at) from the end-of-call webhook,
so we stamp it on the feedback_sms row at creation. A reply is looked up by the
sender's phone number against the one in-flight survey for that number.

Env (add to Railway):
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER   (or TWILIO_MESSAGING_SERVICE_SID)
  SUPABASE_URL, SUPABASE_KEY                                  (already set)
  ARM_A_ASSISTANT_ID, ARM_B_ASSISTANT_ID                      (already set)
  FEEDBACK_SMS_ENABLED = "1"                                  (kill switch; default off)

This module never raises into the request path — feedback is best-effort and
must not affect scoring or the webhook's 200.
"""
import logging
import os

import requests
from supabase import create_client

logger = logging.getLogger(__name__)

# --- question wording (TODO: finalize with the team; keep short for SMS) ------
Q1_TEXT = ("Thanks for practicing with the AI Voice Coach! 2 quick questions. "
           "1) Did it feel like realistic interview practice? "
           "Reply 1-5 (1=Not really, 5=Very realistic).")
Q2_TEXT = ("Thanks! 2) Would you recommend this to someone prepping for "
           "interviews? Reply Y, M (maybe), or N.")
THANKS_TEXT = "Thank you — that's all. Good luck out there!"
REPROMPT_Q1 = "Please reply with a number from 1 to 5."
REPROMPT_Q2 = "Please reply Y (yes), M (maybe), or N (no)."
MAX_REPROMPTS = 1  # one nudge, then give up so we don't nag

STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}


def _enabled():
    return os.environ.get("FEEDBACK_SMS_ENABLED", "") == "1"


def _sb():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def _arm_label(assistant_id):
    if assistant_id and assistant_id == os.environ.get("ARM_A_ASSISTANT_ID"):
        return "A"
    if assistant_id and assistant_id == os.environ.get("ARM_B_ASSISTANT_ID"):
        return "B"
    return ""


def send_sms(to_number, body):
    """Send one SMS via Twilio REST (no SDK dep — matches the requests style)."""
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    data = {"To": to_number, "Body": body}
    svc = os.environ.get("TWILIO_MESSAGING_SERVICE_SID")
    if svc:
        data["MessagingServiceSid"] = svc
    else:
        data["From"] = os.environ["TWILIO_FROM_NUMBER"]
    resp = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=data, auth=(sid, token), timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("sid")


# ── Outbound: kick off the survey after a call ──────────────────────────────

def start_survey(call_id, phone_number, assistant_id, call_started_at):
    """Send Q1 and open a survey row. Best-effort; call at end of the webhook.

    No-ops (returns False) when disabled, phone missing, or a survey for this
    number is already in flight (avoids double-texting on rapid repeat calls).
    """
    if not _enabled() or not phone_number:
        return False
    try:
        sb = _sb()
        active = (sb.table("feedback_sms")
                  .select("id")
                  .eq("phone_number", phone_number)
                  .in_("state", ["awaiting_q1", "awaiting_q2"])
                  .limit(1).execute())
        if active.data:
            logger.info(f"feedback_sms: survey already active for {phone_number}, skip")
            return False

        send_sms(phone_number, Q1_TEXT)
        sb.table("feedback_sms").insert({
            "phone_number": phone_number,
            "call_id": call_id,
            "assistant_id": assistant_id,
            "arm_label": _arm_label(assistant_id),
            "call_started_at": call_started_at or None,
            "state": "awaiting_q1",
        }).execute()
        logger.info(f"feedback_sms: sent Q1 to {phone_number} (arm {_arm_label(assistant_id)})")
        return True
    except Exception as e:  # never break the webhook over a survey
        logger.warning(f"feedback_sms: start_survey failed for {phone_number}: {e}")
        return False


# ── Inbound: parse a reply and advance the state machine ────────────────────

def _parse_rating(body):
    for tok in body.split():
        if tok in {"1", "2", "3", "4", "5"}:
            return int(tok)
    return None


def _parse_recommend(body):
    b = body.strip().lower()
    if b in {"y", "yes"}:
        return "yes"
    if b in {"m", "maybe"}:
        return "maybe"
    if b in {"n", "no"}:
        return "no"
    return None


def handle_inbound(from_number, body):
    """Process one inbound SMS. Returns a reply string to send (or None).

    Wire /sms to call this; reply via send_sms or a TwiML <Message>.
    """
    text = (body or "").strip()
    low = text.lower()
    sb = _sb()

    row = (sb.table("feedback_sms")
           .select("*")
           .eq("phone_number", from_number)
           .in_("state", ["awaiting_q1", "awaiting_q2"])
           .order("created_at", desc=True)
           .limit(1).execute())
    if not row.data:
        return None  # no active survey — ignore (Twilio handles STOP/HELP compliance)
    survey = row.data[0]

    # Honor opt-out even mid-survey.
    if low in STOP_WORDS:
        sb.table("feedback_sms").update(
            {"state": "stopped", "updated_at": "now()"}).eq("id", survey["id"]).execute()
        return None  # Twilio auto-sends the compliance confirmation

    if survey["state"] == "awaiting_q1":
        rating = _parse_rating(text)
        if rating is None:
            return _reprompt(sb, survey, REPROMPT_Q1)
        sb.table("feedback_sms").update({
            "q1_realistic": rating, "state": "awaiting_q2",
            "reprompts": 0, "updated_at": "now()",
        }).eq("id", survey["id"]).execute()
        return Q2_TEXT

    if survey["state"] == "awaiting_q2":
        rec = _parse_recommend(text)
        if rec is None:
            return _reprompt(sb, survey, REPROMPT_Q2)
        sb.table("feedback_sms").update({
            "q2_recommend": rec, "state": "done", "updated_at": "now()",
        }).eq("id", survey["id"]).execute()
        return THANKS_TEXT

    return None


def _reprompt(sb, survey, text):
    """One nudge on an unparseable reply, then give up (mark done)."""
    if survey["reprompts"] >= MAX_REPROMPTS:
        sb.table("feedback_sms").update(
            {"state": "done", "updated_at": "now()"}).eq("id", survey["id"]).execute()
        return None
    sb.table("feedback_sms").update(
        {"reprompts": survey["reprompts"] + 1, "updated_at": "now()"}
    ).eq("id", survey["id"]).execute()
    return text
