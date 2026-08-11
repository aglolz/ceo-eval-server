# CEO Voice Coach — Live Eval Server

Production eval pipeline for the AI Voice Coach: receives Vapi webhooks, routes
the live number's calls into a 50/50 A/B experiment, scores every completed
call against 9 calibrated LLM judges (Claude), writes results to Supabase, and
serves a token-gated live dashboard. It also mirrors each call's end-of-call
report to the Zapier→Twilio SMS chain (participant summary text) and runs a
2-question SMS feedback survey.

Runs on Railway; **pushing to `main` auto-redeploys production.**

## How a call flows

1. A participant calls the live number. The number has no fixed assistant, so
   Vapi sends an `assistant-request` to `/webhook`; the server answers within
   7.5s with the assistant for a randomly chosen A/B arm
   (`ARM_A_ASSISTANT_ID` / `ARM_B_ASSISTANT_ID`).
2. The call happens (coaching session with that assistant).
3. Vapi posts an `end-of-call-report` to `/webhook`. The server **acks
   immediately** and does the slow work in a background pool
   (`SCORING_EXECUTOR`) — scoring inside the request used to drop calls, see
   `WEBHOOK_DROPS_DIAGNOSIS.md`. In the background it runs all judges in
   parallel and inserts one row into `ceo_live_calls` (transcript, arm,
   `ceo_id`, one verdict/reasoning/evidence triple per judge).
4. Independently of scoring, the report is mirrored to `ZAPIER_SMS_HOOK_URL`
   (summary SMS to the participant) and, after `SURVEY_DELAY_SEC`, the SMS
   feedback survey sends its first question. Participant replies arrive at
   `/sms` (Twilio inbound webhook) and land in `feedback_sms`.
5. Calls from assistants listed in `SIM_ASSISTANT_IDS` (simulation traffic)
   are scored the same way but diverted to `sim_calls` and skip the SMS
   pipeline entirely.

## Files

- `ceo_live_server.py` — **production entry point** (Procfile). Webhook, A/B
  routing, background scoring, SMS mirror + survey, `/sms`, `/dashboard`.
- `server_lib.py` — shared machinery: judge runners, transcript re-fetch,
  `ceo_id` extraction, A/B arm helpers, sim-table diversion, Supabase writer.
- `dashboard.py` + `dashboard_template.html` — the live A/B boards.
- `sms_feedback.py` — the 2-question survey state machine.
- `backfill_missing_calls.py` — reconciles Vapi against Supabase and re-scores
  any call that never landed (run `--dry-run` first; scoring bills the API).
- `prompts/` — one file per judge (see "Judges").
- `migrations/` — Supabase schema history; `000_init.sql` bootstraps a fresh
  project.
- `WEBHOOK_DROPS_DIAGNOSIS.md` — root-cause writeup of the Aug 2026 dropped-
  calls incident; read it before touching the webhook or gunicorn config.

## Deploy (Railway)

- The Procfile starts `gunicorn ceo_live_server:app --workers 2 --threads 8`.
  Don't remove the concurrency flags: a single sync worker is what caused the
  dropped-calls incident.
- Railway auto-redeploys on push to `main`. **Run any new migration in the
  Supabase SQL editor BEFORE pushing** — if the server deploys first, the
  whole-row insert fails on the missing column and every judge's scores for
  each call are lost until the migration runs.
- A redeploy kills scoring that is mid-flight (the ack already happened, the
  judges were still running). Deploy in quiet hours, then run
  `backfill_missing_calls.py` to catch anything lost.
- **Running a test instance:** don't fork the code — run a second Railway
  service on the same repo and entry point with different env:
  `SUPABASE_TABLE=maya_test_calls` (or any table with the same shape; see
  `migrations/000_init.sql`), SMS vars unset, `DASHBOARD_TOKEN` its own value.
  Point a test Vapi assistant's webhook at that service's URL.

## Environment variables (Railway dashboard)

Core:
- `ANTHROPIC_API_KEY` — Claude API key (the judges).
- `ANTHROPIC_MODEL` — default `claude-sonnet-4-6`. **Changing the model
  silently uncalibrates every judge** — the dev/test agreement numbers were
  measured on this model. Re-validate against the judge-suite probes before
  switching.
- `SUPABASE_URL`, `SUPABASE_KEY` — the Supabase project + anon key. Note: RLS
  is off in this project, so the anon key has full read/write to participant
  data. Treat it as a secret; see `migrations/000_init.sql` for how to lock
  down later.
- `SUPABASE_TABLE` — destination table; default `ceo_live_calls`.
- `VAPI_API_KEY` — used to re-fetch a transcript when the webhook arrives
  without one.

A/B experiment:
- `ARM_A_ASSISTANT_ID`, `ARM_B_ASSISTANT_ID` — the two Vapi assistants. Also
  how `arm_label` is stamped on rows and how the dashboard maps arms.
- `AB_FORCE_ARM` — `A` or `B`; kill switch pinning all traffic to one arm.

SMS:
- `ZAPIER_SMS_HOOK_URL` — the summary-SMS relay hook (unset = no summary
  texts).
- `FEEDBACK_SMS_ENABLED` — `1` to enable the survey (default off).
- `SURVEY_DELAY_SEC` — delay before survey Q1 so the summary SMS lands first
  (default 75).
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER` or
  `TWILIO_MESSAGING_SERVICE_SID` — survey sending; replies hit `/sms`.

Dashboard:
- `DASHBOARD_TOKEN` — required; with it unset the dashboard refuses to serve.
  Access via `/dashboard?key=<token>`.
- `DASHBOARD_CACHE_SEC` — rebuild interval (default 120).

Simulation + tuning:
- `SIM_ASSISTANT_IDS` — comma-separated assistantIds to divert to `SIM_TABLE`
  (default `sim_calls`).
- `SCORING_WORKERS` — background scoring pool size (default 4).

## Database (Supabase)

Tables: `ceo_live_calls` (production scores; formerly `ankita_test_calls`,
renamed by migration 011), `maya_test_calls`, `sim_calls`, `feedback_sms`,
`participant_demographics`.

- **Fresh project:** run `migrations/000_init.sql` once — it creates the full
  current schema.
- **Existing database:** apply new numbered migrations in order; each file's
  header says when to run it relative to the deploy.

Each judge occupies a column triple on the call tables: `<name>_verdict`
(`pass` | `fail` | `na` | `error`), `<name>_reasoning`, `<name>_scan` (JSONB,
the verbatim evidence quote).

## Judges

The roster is the `JUDGES` list in `ceo_live_server.py`. Two prompt formats,
dispatched by file extension:

- **`.md`** — the file IS the system prompt; the server substitutes
  `{transcript}` inline. Output JSON: `{verdict: pass|fail, reasoning,
  step1_scan}`. (`limits_the_load`, `feedback_q_low_bar`.)
- **`.yaml`** — a structured judge (`dimension`/`definition`/`pass`/`fail`/
  `na`) from the judge-suite hill-climbing harness. The server assembles and
  runs it **exactly** as `judge-suite/scripts/eval_harness_v2.py` does
  (instructions in the system prompt, transcript in the user turn,
  `temperature=0`, tolerant JSON parse, retries), so live scores match the
  calibrated dev/test numbers. **Do not "clean up" or restructure this path**
  — moving the instructions or transcript silently recalibrates the judges.
  (All seven `*_hero.yaml` / `*_v1.yaml` judges.)

Calibration caveats worth knowing (details in each migration's header):
`pii` and `makes_it_a_dialogue` are certified only on synthetic sets — treat
their rare non-N/A / PASS verdicts as review flags, not ground truth;
`adapts_when_stuck` is reliable on FAIL but over-generous on PASS.

### Adding a new judge

1. Add the prompt file to `prompts/`.
2. Write a migration adding the column triple to `ceo_live_calls` and
   `sim_calls`; run it in Supabase.
3. Add the entry to `JUDGES` in `ceo_live_server.py`.
4. Push — Railway auto-redeploys. (Migration first; see Deploy.)

## Testing

Health check (shows instance, judge roster, table):
```
curl https://<your-app>.railway.app/
```

Local run (uses `.venv`; needs the env vars above):
```
.venv/bin/python ceo_live_server.py
```

Simulate a webhook — note the immediate `{"status": "accepted"}`; scoring
runs in the background and logs its outcome:
```
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -d '{"message":{"type":"end-of-call-report","call":{"id":"test-1","artifact":{"transcript":"AI: Hello User: Hi"}}}}'
```

## Known tradeoffs

- Survey Q1 rides an in-process timer: a redeploy inside the
  `SURVEY_DELAY_SEC` window silently drops that one survey.
- A redeploy mid-scoring loses that call's scores (ack already sent);
  `backfill_missing_calls.py` recovers it.
- The anon key + RLS-off access model (above) is deliberate simplicity; lock
  it down if the data-access story changes.
