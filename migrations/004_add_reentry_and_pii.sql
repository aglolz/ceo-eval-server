-- Migration 004 — add the "reentry_appropriate_framing" and "pii" hero judges to call_scores.
-- Run ONCE against the Supabase Postgres (SQL editor or psql) BEFORE deploying the server
-- revision that adds these judges to the JUDGES list. If the server deploys first, the
-- whole-row insert will fail (missing columns) and ALL judges' scores for each call will be
-- lost until this runs.
--
-- Each judge stores a verdict (pass | fail | na | error), the model's one-line reasoning, and
-- a JSONB "scan" holding the verbatim evidence quote ({"evidence": "..."}).
--
-- reentry_appropriate_framing (v4_hero, dev 95.7% / test 87.0%): PASS and N/A verdicts are
--   reliable (test PPV 1.00, N/A-recall 0.94). FAIL detection is the soft spot on a thin
--   dimension (78% not_observed) — a bare probation mention can be under-armed. N/A-dominant,
--   so most live calls will abstain.
--
-- pii (v4_hero): detection is certified only on a SYNTHETIC probe set (31/31) — the calibration
--   corpus has ZERO real coach PII breaches. N/A-dominant: on live calls this judge will almost
--   always abstain. Treat any PASS/FAIL as a flag to REVIEW, not ground truth, until real
--   breach data validates it.

ALTER TABLE call_scores
    ADD COLUMN IF NOT EXISTS reentry_appropriate_framing_verdict     TEXT,
    ADD COLUMN IF NOT EXISTS reentry_appropriate_framing_reasoning   TEXT,
    ADD COLUMN IF NOT EXISTS reentry_appropriate_framing_scan        JSONB,
    ADD COLUMN IF NOT EXISTS pii_verdict                             TEXT,
    ADD COLUMN IF NOT EXISTS pii_reasoning                           TEXT,
    ADD COLUMN IF NOT EXISTS pii_scan                                JSONB;
