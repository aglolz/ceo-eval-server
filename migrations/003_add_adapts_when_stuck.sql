-- Migration 003 — add the calibrated "adapts_when_stuck" hero judge to call_scores.
-- Run ONCE against the Supabase Postgres (SQL editor or psql) BEFORE deploying the
-- server revision that adds this judge to the JUDGES list. If the server deploys
-- first, the whole-row insert will fail (missing column) and ALL judges' scores for
-- each call will be lost until this runs.
--
-- Stores a verdict (pass | fail | na | error), the model's one-line reasoning, and a
-- JSONB "scan" holding the verbatim evidence quote ({"evidence": "..."}).
--
-- Judge status (dev 91.3% / test 68.2%): trustworthy on FAIL (test NPV 1.00), but
-- over-generous on PASS (test PPV 0.17) — treat PASS verdicts as low-confidence.

ALTER TABLE call_scores
    ADD COLUMN IF NOT EXISTS adapts_when_stuck_verdict     TEXT,
    ADD COLUMN IF NOT EXISTS adapts_when_stuck_reasoning   TEXT,
    ADD COLUMN IF NOT EXISTS adapts_when_stuck_scan        JSONB;
