-- Migration 005 — add the "makes_it_a_dialogue" judge (v1) to call_scores.
-- Run ONCE against the Supabase Postgres (SQL editor or psql) BEFORE deploying the server
-- revision that adds this judge to the JUDGES list. If the server deploys first, the
-- whole-row insert will fail (missing columns) and ALL judges' scores for each call will be
-- lost until this runs.
--
-- Each judge stores a verdict (pass | fail | na | error), the model's one-line reasoning, and
-- a JSONB "scan" holding the verbatim evidence quote ({"evidence": "..."}).
--
-- makes_it_a_dialogue (v1): the pass bar is TARGET behavior — a reflective question ("how did
--   that feel?" / "how do you think that went?") plus engagement with the specifics of what the
--   participant said. NO coach in the calibration corpus meets it, so — like `pii` — this judge
--   is certified ONLY on a SYNTHETIC seed set: 18 author-written transcripts (9 pass / 9 fail,
--   incl. adversarial cases), human-reviewed by Maya. v1 scored 16/16 dev, 2/2 test, and held
--   the FAIL line on 12 real corpus rows (0 false-fires). On live calls this judge will almost
--   always return FAIL, because current coaches don't ask reflective questions. Treat any PASS
--   as a signal that the coach exhibited the target behavior — worth REVIEW — not as a
--   calibrated-against-humans ground truth, until real positive examples exist to validate it.

ALTER TABLE call_scores
    ADD COLUMN IF NOT EXISTS makes_it_a_dialogue_verdict     TEXT,
    ADD COLUMN IF NOT EXISTS makes_it_a_dialogue_reasoning   TEXT,
    ADD COLUMN IF NOT EXISTS makes_it_a_dialogue_scan        JSONB;
