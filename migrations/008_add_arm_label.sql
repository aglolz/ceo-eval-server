-- Migration 008 — add an `arm_label` column ('A' | 'B' | '') to every table the
-- shared webhook writer targets. Run ONCE in Supabase (SQL editor) BEFORE deploying
-- the server revision that writes arm_label in handle_call_webhook. If the server
-- deploys first, the whole-row insert fails (missing column) and ALL judges' scores
-- for each call are lost until this runs.
--
-- arm_label is the A/B experiment arm, reverse-mapped from the call's assistant_id
-- against ARM_A_ASSISTANT_ID / ARM_B_ASSISTANT_ID (see _arm_label in server_lib.py).
-- Stored so consumers don't need the env mapping to split arms; '' when the
-- assistant matches neither arm. Matches feedback_sms.arm_label for easy joins.
--
-- All four tables get the column because server_lib.handle_call_webhook builds one
-- row shape for every instance (ankita_server / maya_server / ceo_live_server).

ALTER TABLE ankita_test_calls ADD COLUMN IF NOT EXISTS arm_label TEXT;
ALTER TABLE maya_test_calls   ADD COLUMN IF NOT EXISTS arm_label TEXT;
ALTER TABLE ceo_live_calls    ADD COLUMN IF NOT EXISTS arm_label TEXT;
ALTER TABLE call_scores       ADD COLUMN IF NOT EXISTS arm_label TEXT;
