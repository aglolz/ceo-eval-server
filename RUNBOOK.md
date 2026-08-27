# Operations Runbook: AI Voice Coach Eval Server

**Audience:** CEO IT / the named operations owner ([OWNER-OPS]).
**Lives in:** the eval-server repo root (`RUNBOOK.md`); update it with the code.
**Written against:** the CEO-owned target state (Railway + Supabase, HANDOVER_PLAN v2).
**Last verified against the code:** 2026-08-26.
Everything here is task-oriented: *when X happens, do Y.*

> **The one comfort to internalize:** the eval server is **not in the call path**. If it is
> down, participants can still call and practice; we lose *scores*, not calls. Almost
> nothing here is a middle-of-the-night emergency.

---

## 1. System map

```
Participant phone call
        │
      Vapi ──(assistant-request: A/B arm pick, must answer <7.5s)──► Eval server
        │                                                            (Railway, Flask+gunicorn,
        └──(end-of-call-report webhook)────────────────────────────►  entrypoint ceo_live_server:app)
                                                                          │
   Twilio ──(inbound survey SMS → /sms)──────────────────────────────────►│
                                                                          │ 9 Claude judges, parallel
                                                                          │ (ANTHROPIC_API_KEY,
                                                                          │  model pinned: claude-sonnet-4-6)
                                                                          ▼
                                                                   Supabase Postgres
                                                                   (calls + scores, feedback_sms,
                                                                    participant_demographics)
                                                                          ▲
   Salesforce ──(Coefficient)──► Google Sheet ──(Apps Script, token-gated)──┘
                                                        (daily sync job — see §2)
                                                                          │
                                                            /dashboard routes (authed),
                                                            rebuilt from Supabase
```

- **Repo:** GitHub `CEO-Coachlink/ACE` (CEO org). Push to `main` = deploy (Railway
  auto-deploys). This is the only repo production builds from.
- **Production entrypoint:** `ceo_live_server:app` (Procfile) — the single entrypoint.
  There are no per-person server variants: a **test instance is a second Railway service
  on this same repo and Procfile with different env** (see README, "Running a test
  instance").
- **Production service:** `https://web-production-b69cfd.up.railway.app` (Railway, CEO
  workspace). `GET /` is the health check and returns the live instance name, judge list,
  and table names. The dashboard lives at `/dashboard/live?key=<DASHBOARD_TOKEN>` — the
  token is in Railway env, not written down here.
- **Secret store:** Railway service env vars. Full reference in the Appendix.
- **Migrations:** `migrations/` in the repo — `000_init.sql` (full-schema bootstrap for a
  fresh database) plus the incremental `002`–`011`. Supabase SQL editor runs them by hand;
  there is no auto-migrate.

## 2. Routine operations

### Deploy a change
1. Merge/push to `main` on the CEO GitHub repo. Railway builds and deploys automatically.
2. Watch the deploy in Railway → service → Deployments. Confirm the health check: `GET /`
   on the service URL returns 200.
3. If the change touches judges or scoring, watch the next real call land a row (or replay
   a webhook, below).

**Roll back:** Railway → Deployments → previous good deploy → Redeploy. (Or `git revert` +
push, which keeps history honest.)

### Put a new coach variant into the A/B test

The A/B wiring lives in two places that must agree: **Vapi** (the assistants) and the
**server env** (`ARM_A_ASSISTANT_ID` / `ARM_B_ASSISTANT_ID` — call routing AND the
dashboard's id→arm map both derive from these). The dashboard keeps only a cosmetic
display label per arm. A new variant, in this order:

1. **Vapi: version by duplicating, never edit a live arm in place.** Duplicate the
   current production assistant, apply the system-prompt changes to the copy, and name it
   with a version (e.g. "PCPT coach 2.2 — practice-loop"). In Vapi, the assistant *is*
   the version: scored rows carry only the assistant id, so editing a live arm's prompt
   in place would mix two different coaches' scores under one id and quietly corrupt the
   comparison. Also commit the new prompt text to the repo with the change, so prompt
   history lives in git, not only in Vapi.
2. **Smoke-test before it meets participants.** Run the persona simulators against the
   new assistant id (`simulate.py --coach <id>`, or a voice run); sim traffic scores with
   the same judges but lands in `sim_calls` (via `SIM_ASSISTANT_IDS`), so it never
   pollutes the live numbers.
3. **Railway: point the arm at it.** Set `ARM_B_ASSISTANT_ID` to the new assistant's id
   and save (service restarts). From the next call, the 50/50 split serves the new
   variant. Arm labels are derived from these env vars at end-of-call time, so rows from
   the *previous* arm-B assistant stop matching the experiment: the B column effectively
   starts fresh, which is what you want. Note the old id in the change log before
   overwriting it.
4. **Dashboard: nothing to edit.** `dashboard.py` builds its id→arm map (`ARM_CODE`)
   from the same `ARM_A_ASSISTANT_ID` / `ARM_B_ASSISTANT_ID` env vars the router uses,
   and labels each arm column with the assistant's live Vapi name ("Arm B — <name>"),
   so the env change in step 3 re-points both the map and the label on its own — this
   is why step 1's versioned-name convention matters: the Vapi assistant name is what
   dashboard readers see. The hardcoded ids and `VARIANTS` labels near the top of the
   file are fallbacks only (env unset / Vapi unreachable). The server's `dashboard.py`
   is the source of truth for dashboard build logic; the local builder
   (`ceo_voice_coach/build_ab_dashboard.py`) is a legacy offline fallback synced by
   hand from it, reading the same env vars from `.env`.
5. **The system-prompt panel takes care of itself.** The board fetches both arms'
   *current* prompts live from Vapi (10-minute cache) and renders the A-vs-B prompt diff
   and generated takeaways. Once `ARM_CODE` points at the new id, the diff reflects the
   new variant on the next rebuild; there is no manual step.
6. **Verify:** watch the next few calls land with arm labels populated; the B column's
   per-dimension n resets and starts accumulating. Who decides promotion is Doc 1 §5; a
   misbehaving variant is the `AB_FORCE_ARM` lever (§3).

**Promoting a winning B to be the new A** is the same procedure pointed at the other env
var: set `ARM_A_ASSISTANT_ID` to the winner, put the *next* candidate in B (or set
`AB_FORCE_ARM=A` until one exists). The dashboard's arm map and labels follow the env
vars and the live Vapi assistant names on their own.

### Read logs / check health
- Logs: Railway → service → Logs (deploy + runtime). Judge errors and Supabase write
  failures log per-call with the Vapi `call_id`.
- Health: `GET /` returns 200 when the app is up:

      curl -s https://web-production-b69cfd.up.railway.app/

  A healthy response names the instance (`ceo_live`), the nine judges **and the prompt file
  each one is running**, and the tables it writes to. The `prompts` map is the only way to
  confirm from outside which judge *versions* a deploy is serving — the dimension names stay
  identical across a prompt promotion, so they cannot tell you. Check it after any deploy
  that touches `prompts/` or the `JUDGES` list. If Railway shows the service crashed, the
  logs' last stack trace is almost always the answer.

**Verifying a deploy, in the right order.** A build takes a couple of minutes, so a health
check run straight after `git push` will still show the *old* response and look like a failed
deploy. Watch Railway → the service → **Deployments** until the newest entry reads ACTIVE with
"Deployment successful" (it names the commit and the source, e.g. `CEO-Coachlink/ACE  main`),
**then** curl the health endpoint.

If a deploy never appears at all, the usual cause is the GitHub link: when the service settings
show the repo but report *"GitHub Repo not found"* under the branch selector, Railway's GitHub App
cannot read the repo and pushes stop triggering builds, with no error anywhere else. Re-grant the
Railway GitHub App access to the repo — an owner of the GitHub org may need to approve the install.

### Replay a failed webhook payload
Vapi retries transient failures, but if a call never scored:
1. Find the call in Vapi's dashboard → copy the end-of-call-report payload (or at minimum
   the transcript + call id).
2. POST it to the server yourself:
   ```
   curl -X POST https://web-production-b69cfd.up.railway.app/webhook \
        -H "Content-Type: application/json" -d @payload.json
   ```
3. Confirm the row landed in Supabase (table `ceo_live_calls`, key `call_id`). Inserts are
   idempotent on `call_id`, so replaying a call that already scored will not duplicate the row.

> **The Supabase write is idempotent. The rest of the pipeline is NOT.**
> For a live (non-sim) `end-of-call-report`, the handler fires the SMS pipeline *before*
> scoring: it mirrors the call to Zapier for the summary text, and starts the two-question
> participant survey against the number in `call.customer.number`. **Replaying a real
> participant's call therefore texts that participant again** — a duplicate summary and a
> fresh survey, possibly weeks after their call. Nothing about the request looks unusual when
> it happens.
>
> Before replaying a live call, either set `FEEDBACK_SMS_ENABLED=0` and clear
> `ZAPIER_SMS_HOOK_URL` for the duration, or blank `call.customer.number` in the payload you
> POST. To exercise the pipeline *without* touching a participant at all, replay under a
> simulation `assistantId` (any id in `SIM_ASSISTANT_IDS`): `resolve_table` marks it as sim,
> which skips both SMS paths and writes to `sim_calls` instead. That is the right shape for a
> post-transfer smoke test.

**Verified end-to-end 2026-08-27** by exactly that method: a synthetic persona transcript
POSTed under the sim assistant id returned `{"status":"accepted"}` in 0.27s (the fast-ack path),
scored on all nine judges, and landed one row in `sim_calls` with no verdict column left
unscored and nothing written to `ceo_live_calls`.

### Rebuild the dashboard manually
The `/dashboard` routes rebuild from Supabase on a timer in-process. To force a rebuild,
redeploy (cheap) or hit the rebuild path documented in `dashboard.py`. If numbers look
stale, check the timer first, then Supabase reachability in logs.

### Re-run the demographics pull by hand

> **Status (2026-08-26): the scheduled sync is NOT live yet.** It is the last open piece of
> the handover (HANDOVER_PLAN Phase 3). Until it ships, demographic roll-ups are whatever
> was last loaded by hand, and the dashboard's demographic facets can go stale without
> anything looking broken.

The designed path — Salesforce → (Coefficient) → the `ceo codes for ai pilot` sheet tab →
a token-gated Apps Script endpoint → Supabase:

```
python sync_demographics.py --dry-run   # prints the tab's real headers + column mapping
python sync_demographics.py             # upsert into participant_demographics
```

It needs `DEMOGRAPHICS_SHEET_URL` + `DEMOGRAPHICS_SHEET_TOKEN` in env, and in normal
operation runs daily as its own Railway cron service (`0 6 * * *`, after Coefficient's
refresh). Failures are non-urgent: dashboards keep serving the last-known roll-ups. Chase
a renamed sheet column first (`--dry-run` shows the headers — a rename is a one-line fix),
then the Apps Script deployment and its token.

**Break-glass:** `push_demographics.py` in the `ceo_voice_coach` repo loads a hand-exported
`report*.csv` straight into `participant_demographics` from a laptop. Same roll-up and
upsert semantics; use it only if the sheet path is down.

### Rotate a secret
1. Generate the new value at the source (Supabase / Vapi / Twilio / Anthropic / Salesforce).
2. Update the Railway env var. Railway restarts the service on save.
3. Verify: health check + one scored call (or a webhook replay).
4. Revoke the old value at the source. **Order matters: new in, verify, then revoke.**

### Restore the database from backup
Supabase Pro keeps daily backups (Dashboard → Database → Backups → Restore). On free tier,
restore = re-run `000_init.sql` + migrations, then re-ingest what can be re-ingested
(scores are recomputable by replaying webhooks from Vapi's call log; demographics repopulate
on the next pull). Decide the backup tier with governance (Doc 4 §5 retention).

## 3. Emergency levers

### `AB_FORCE_ARM`: the A/B kill switch
A misbehaving arm B (bad coach variant) is the one genuinely urgent scenario, because it
affects *live participant calls*:
1. Railway → env vars → set `AB_FORCE_ARM=A` (or `B`).
2. Save (service restarts). All new calls now route to the forced arm.
3. Tell the program owner + judge-quality owner; unset only when the variant is fixed.

### `FEEDBACK_SMS_ENABLED`: the survey switch
The 2-question post-call SMS survey (live since 2026-07-29). Set to `0` to stop all survey
sends immediately. TCPA/consent ownership transfers to CEO at handover; if there is any
consent doubt, turn it off first and resolve second.

### "The line is scoring nothing": triage tree, in order
1. **Vapi webhook:** Vapi dashboard → is the end-of-call-report firing, and at the right
   `server.url`? (Wrong URL = someone repointed it; this is the #1 cause after any
   account/URL change.)
2. **Server 200s:** Railway logs: are webhooks arriving and returning 200? Crashed
   service → last stack trace. 401/403 → auth/secret rotation went wrong.
3. **Judge errors:** logs show per-judge failures (Anthropic key invalid, spend cap hit,
   or model name changed). **If someone "upgraded" `ANTHROPIC_MODEL`, that is a
   recalibration event: revert it and read Doc 3 before doing anything else.**
4. **DB writes:** logs show Supabase errors: key rotated without updating env, table
   schema drift (a judge added without its migration → inserts 500). Schema before code,
   always (§4).

Calls that arrived during an outage are not lost; replay their webhooks (§2).

## 4. Change procedures (learned the hard way)

- **Schema before code, always.** A judge or column referenced by code before its
  migration ran = 500s on insert = scores silently lost. Migration first, deploy second.
- **Add a judge, in this exact order:**
  1. Migration adding the three columns (`<dim>_verdict`, `_reasoning`, `_scan`) → run in Supabase.
  2. Prompt file into `prompts/` (`.md` = file is the system prompt; `.yaml` = judge-suite
     hero format, assembled exactly as the calibration harness does).
  3. Entry in the `JUDGES` list in `ceo_live_server.py`.
  4. Push → Railway deploys.
- **Model or SDK upgrades are recalibration events, not maintenance.** The judges are
  certified on `claude-sonnet-4-6` with the exact current message layout (system prompt /
  transcript-in-user-turn). Changing the model, or any runner change that moves where the
  instructions sit, silently recalibrates every judge. **Stop and hand off to Doc 3
  (certification probes) before deploying.** An API-key swap alone is safe, but run the
  probes once after as a smoke test.
- **Never "clean up" a certified prompt in transit.** Prompts port verbatim or not at all.

## 5. On-call expectations

At current volume, expect **quiet**. Realistic incident inventory:

| Event | Frequency | Urgency | Cost of waiting |
|---|---|---|---|
| Bad arm-B variant live | rare, self-inflicted | **hours** (the one real page) | participants get a worse coach |
| Scoring stopped | rare | days | unscored calls (replayable) |
| Demographics sync failing | occasional | week | stale dashboard breakdowns |
| Railway/Supabase platform incident | rare | none (wait it out) | same as scoring stopped |

Monitoring today: Railway's built-in deploy/crash alerts + the health check. A weekly
habit beats a pager: **once a week, open the dashboard and confirm the most recent call
date is recent.** That single glance catches every failure mode above.

## 6. Appendix: environment variable reference

| Var | What it does |
|---|---|
| `ANTHROPIC_API_KEY` | Judge scoring. **Read implicitly by the SDK; easy to miss when copying env between services.** |
| `ANTHROPIC_MODEL` | Judge model. **Pinned `claude-sonnet-4-6`; changing it = recalibration event (Doc 3).** |
| `SUPABASE_URL` / `SUPABASE_KEY` | Scores database. |
| `SUPABASE_TABLE` | Live-calls table name (`ceo_live_calls`). |
| `SIM_ASSISTANT_IDS` / `SIM_TABLE` | Assistant ids whose calls are simulation traffic → written to `sim_calls`, excluded from SMS. |
| `VAPI_API_KEY` | Vapi API access (assistant-request handling, call artifacts). |
| `ARM_A_ASSISTANT_ID` / `ARM_B_ASSISTANT_ID` | The two coach variants in the A/B split. |
| `AB_FORCE_ARM` | Kill switch: force all calls to `A` or `B`; unset = 50/50. |
| `FEEDBACK_SMS_ENABLED` | Post-call survey on/off. |
| `SURVEY_DELAY_SEC` | Delay before the survey SMS. |
| `ZAPIER_SMS_HOOK_URL` | Summary-SMS mirror (Vapi→Zapier→Twilio). |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Inbound survey SMS auth. |
| `TWILIO_FROM_NUMBER` or `TWILIO_MESSAGING_SERVICE_SID` | Survey sender (toll-free 855). |
| `DEMOGRAPHICS_SHEET_URL` / `DEMOGRAPHICS_SHEET_TOKEN` | Demographics sync: the Apps Script `/exec` endpoint serving the `ceo codes for ai pilot` tab, and the shared secret it checks. **URL + token together read participant demographics — both are secrets.** Not yet set in production (§2). |
| `DASHBOARD_TOKEN` / `DASHBOARD_CACHE_SEC` | Dashboard access token and rebuild interval. |

**Migration history:** `000_init.sql` (full-schema bootstrap) + `002`–`011` (hero judge
columns, ceo_id, arm label, sim calls, feedback_sms, participant_demographics, and 011's
rename of the live table to `ceo_live_calls`). On a fresh database run `000_init.sql`, then
the numbered migrations in order.

**Related docs:** Doc 3, the Judge Methodology & Calibration Playbook
(`judge-suite/JUDGE_PLAYBOOK.md` in the `ceo_voice_coach` repo — judge changes &
recalibration) · Doc 4 (what data is sensitive and
why the dashboard stays behind auth) · Doc 6 (what's deliberately not built).
