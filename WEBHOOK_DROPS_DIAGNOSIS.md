# Why end-of-call reports still drop after the Aug 3 patch (Aug 4 diagnosis)

> **Status (2026-08-11):** the fix below landed on `main` in
> `ceo_live_server.py` (formerly `ankita_server.py` — renamed in `046fcb4`)
> plus the Procfile concurrency bump. The body is kept as written on Aug 4 as
> the historical record; read `ankita_server.py` as today's
> `ceo_live_server.py`. `backfill_missing_calls.py` reconciles any calls
> dropped before the fix deployed.

**TL;DR:** The server scores calls *inside* the webhook request on a **single
sync gunicorn worker**. Vapi only waits ~10s for a webhook response; scoring a
long call takes minutes. Today's Railway log shows the two resulting failure
modes live in production: queued webhook connections dying before the body can
be read (`ConnectionResetError` in `request.get_json`), and gunicorn
SIGKILL-ing the stuck worker (`[CRITICAL] WORKER TIMEOUT`), which destroys any
scoring in progress. Ankita's Aug 3 patch (`f7769d8`) hardened the code *inside*
`handle_call_webhook` — but both of today's failure modes happen **before or
outside** that code, so it couldn't help. Fix: ack the webhook immediately and
score in a background pool, plus a gunicorn concurrency bump.

## Evidence (Railway export `logs.1785880937038.json`, Aug 4 22:01:00–22:01:43 UTC)

1001 log lines in a 43-second slice, all stderr. Sorted by timestamp:

- **40×** `ERROR:ankita_server:Exception on /webhook [POST]` — every traceback
  identical: `request.get_json(force=True)` (ankita_server.py:81) → werkzeug
  `get_data` → gunicorn body read → **`ConnectionResetError: [Errno 104]`**.
  The client (Vapi) had already given up and reset the connection by the time
  the worker got around to reading the POST body.
- **1×** `[CRITICAL] WORKER TIMEOUT (pid:86)` at 22:01:28 → `Worker exiting` →
  `Booting worker with pid: 87`. The worker had been unresponsive past
  `--timeout 120`; gunicorn killed it. Anything it was scoring died with it.
- **Zero** `INFO:server_lib` scoring/"saved to Supabase" lines anywhere in the
  window (stderr INFO lines do appear in Railway exports — see the Jul 29 CSVs —
  so their absence is real: no call was scored in this slice).
- The errors arrive in two dense bursts (18 tracebacks stamped within ~200ms at
  22:01:00, 22 within ~100ms at 22:01:31, right after the replacement worker
  boots). That's a worker draining a backlog of already-dead queued
  connections — the millisecond spacing is log flush batching, the queue built
  up over the preceding minutes.

The Jul 29 CSV exports additionally confirm the deployment shape: gunicorn
logs `Using worker: sync` and boots **one** worker (the Procfile passes no
`--workers`/`--threads`, so gunicorn defaults to a single synchronous worker).

## Root cause chain

1. `/webhook` calls `server_lib.handle_call_webhook` synchronously. For an
   end-of-call report that means: optional transcript fetch from the Vapi API
   (up to ~25s of sleeps/retries), then 9 Claude judges over a transcript that
   can be 15–30 minutes of conversation. Judges run in parallel threads, but
   the request still lasts as long as the slowest judge including its retries —
   routinely minutes for long calls. **That's why misses skew long**: long call
   → long transcript → long scoring → biggest window of vulnerability (and the
   most likely to blow through `--timeout 120` and get SIGKILLed mid-score).
2. Meanwhile the **only** worker is occupied, so every other webhook — Vapi's
   status-updates, speech-updates, the *next* call's end-of-call report, even
   `assistant-request` (which must be answered in 7.5s to route the A/B arm) —
   sits in the socket backlog. Vapi times out and resets. When the worker
   finally accepts those sockets, reading the body raises `ConnectionResetError`
   and the report is gone. **That's why misses cluster**: one slow call poisons
   the queue for everything behind it (e.g. the 7 consecutive misses Jul 30
   17:50–18:41).
3. If scoring itself pins the worker unresponsive past 120s, gunicorn SIGKILLs
   it — the in-flight call is lost with no application log at all, and the
   fresh worker wakes up to a backlog of dead connections (the 22:01:28 →
   22:01:31 sequence in today's log).

### Why the Aug 3 patch didn't stop today's drops

`f7769d8` added Supabase insert retries and "save an unscored row when the
transcript is missing" — real fixes, but they live *inside*
`handle_call_webhook`. Today's two modes bypass it entirely:

- On a connection reset, `request.get_json` raises before a payload exists.
  There is nothing to save and no code of ours left to run.
- On WORKER TIMEOUT, the process is SIGKILLed mid-scoring. No in-code retry
  survives a kill.

(The Jul 28–30 cluster likely mixed these architectural drops with the
transcript/insert failures the patch did fix; the Aug 4 misses are purely
architectural.)

## The fix (now on `main` — see status note above)

Two changes, judge prompts/message-layout untouched (`run_judge` and the
calibrated YAML path are not modified):

1. **Ack first, score in the background** (`ankita_server.py`). The webhook
   now returns `{"status": "accepted"}` immediately for `end-of-call-report`
   and submits scoring to a module-level `ThreadPoolExecutor`
   (`SCORING_EXECUTOR`, 4 threads, `SCORING_WORKERS` env-overridable) — same
   philosophy as the existing `_forward_to_sms` thread, but bounded, and with
   the outcome logged (`background scoring failed/crashed`) so failures stay
   visible. Vapi gets its 200 in milliseconds; nothing queues behind scoring
   anymore. Other message types (status-update etc.) still run inline — they
   return in microseconds.
2. **Gunicorn concurrency** (`Procfile`): `--workers 2 --threads 8` (gthread
   worker). Even a momentarily busy thread can no longer starve
   `assistant-request` routing, and a worker restart only affects half the
   capacity. `--timeout 120` stays as a safety net — with requests now
   instant, it should never fire.

Both A/B routing (stateless `random.choice`) and the SMS survey (state in the
Supabase `feedback_sms` table) are multi-worker-safe, so the worker bump
changes no behavior.

Verified locally: compiled, and a Flask test-client smoke test shows
end-of-call-report acked in ~1ms with `handle_call_webhook` running in the
background with the right table, while status-update stays synchronous.

## Residual risks / follow-ups

- **Deploys still kill in-flight scoring.** The executor's threads are
  non-daemon, so a graceful shutdown waits up to gunicorn's graceful timeout
  (default 30s), but a Railway redeploy mid-score can still lose that call
  (this is what the Jul 29 17:10 container start did). Cheap mitigation:
  deploy during quiet hours; real fix later: a persistent queue or a
  Supabase-side "received, not yet scored" row written before ack.
- **Same pattern in siblings:** `maya_server.py` and `ceo_live_server.py`
  score synchronously in-request too. Not touched here (only ankita_server is
  deployed), but they should get the same treatment before they're deployed.
- After deploying, re-run the Vapi↔Supabase reconciliation for a few days to
  confirm the miss rate goes to zero.
