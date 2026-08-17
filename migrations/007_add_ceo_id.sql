-- Migration 007 — add a `ceo_id` column to every table the shared webhook writer targets.
-- Run ONCE against the Supabase Postgres (SQL editor) BEFORE deploying the server revision
-- that extracts ceo_id in handle_call_webhook. If the server deploys first, the whole-row
-- insert fails (missing column) and ALL judges' scores for each call are lost until this runs.
--
-- ceo_id is the participant ID the caller verified with (keypad entry preferred, falling
-- back to the validate_ceo_id tool-call payload or spoken digits — see extract_ceo_id in
-- server_lib.py). Test calls use 2222; real participants use their own IDs, so consumers
-- (e.g. ceo_voice_coach/build_ab_dashboard.py) can split test vs real on this column
-- instead of grepping transcripts. NULL = no ID observed (call died before verification,
-- or an old row from before this migration).
--
-- TEXT, not INTEGER: IDs are opaque identifiers (leading zeros must survive).
--
-- All four tables get the column because server_lib.handle_call_webhook builds one row
-- shape for every instance (ankita_server / maya_server / ceo_live_server) — only
-- ankita_test_calls receives traffic today, but a Procfile swap must not start 500ing.
--
-- HISTORICAL — do not replay: `ankita_test_calls` was renamed `ceo_live_calls`
-- by migration 011 and `maya_test_calls` no longer exists, so those two ALTERs
-- now error on a missing table. Fresh project: run 000_init.sql instead.

ALTER TABLE ankita_test_calls ADD COLUMN IF NOT EXISTS ceo_id TEXT;
ALTER TABLE maya_test_calls   ADD COLUMN IF NOT EXISTS ceo_id TEXT;
ALTER TABLE ceo_live_calls    ADD COLUMN IF NOT EXISTS ceo_id TEXT;
ALTER TABLE call_scores       ADD COLUMN IF NOT EXISTS ceo_id TEXT;
