-- Migration 002 — add the three calibrated "hero" judges to call_scores.
-- Run once against the Supabase Postgres (SQL editor or psql) before deploying
-- the server revision that adds these judges to the JUDGES list.
--
-- Each judge stores a verdict (pass | fail | na | error), the model's one-line
-- reasoning, and a JSONB "scan" holding the verbatim evidence quote
-- ({"evidence": "..."}).

ALTER TABLE call_scores
    ADD COLUMN IF NOT EXISTS drives_practice_verdict     TEXT,
    ADD COLUMN IF NOT EXISTS drives_practice_reasoning   TEXT,
    ADD COLUMN IF NOT EXISTS drives_practice_scan        JSONB,

    ADD COLUMN IF NOT EXISTS scaffolds_then_fades_verdict     TEXT,
    ADD COLUMN IF NOT EXISTS scaffolds_then_fades_reasoning   TEXT,
    ADD COLUMN IF NOT EXISTS scaffolds_then_fades_scan        JSONB,

    ADD COLUMN IF NOT EXISTS quality_conversational_flow_verdict     TEXT,
    ADD COLUMN IF NOT EXISTS quality_conversational_flow_reasoning   TEXT,
    ADD COLUMN IF NOT EXISTS quality_conversational_flow_scan        JSONB;
