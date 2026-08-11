-- 011: Rename ankita_test_calls -> ceo_live_calls.
--
-- "Ankita's test" instance has been the real production pipeline since the
-- live-number swap (2026-07-28). This migration makes the table name say so,
-- matching the code rename (ankita_server.py -> ceo_live_server.py).
--
-- RUN THIS IN THE SUPABASE SQL EDITOR **BEFORE** PUSHING THE CODE RENAME —
-- Railway auto-redeploys on push, and the new code writes to ceo_live_calls.
-- The compatibility view at the bottom keeps the still-running old code
-- writing successfully during the gap between migration and redeploy, so no
-- calls are dropped either way.

-- The validation-phase ceo_live_calls table (created for the judge-less
-- ceo_live_server before judges were added; likely empty) would collide with
-- the rename — archive it instead of dropping, just in case it has rows.
DO $$
BEGIN
  IF to_regclass('public.ceo_live_calls') IS NOT NULL
     AND to_regclass('public.ankita_test_calls') IS NOT NULL THEN
    ALTER TABLE public.ceo_live_calls RENAME TO ceo_live_calls_prejudge_archive;
  END IF;
END $$;

ALTER TABLE IF EXISTS public.ankita_test_calls RENAME TO ceo_live_calls;

-- Compatibility view under the old name. It's a single-table view, so it is
-- auto-updatable: the deployed server's plain INSERTs pass straight through to
-- ceo_live_calls until Railway redeploys with the new code. Local scripts
-- still pointed at ankita_test_calls keep reading too.
--
-- AFTER the redeploy is verified (health endpoint shows "table": "ceo_live_calls"
-- and a fresh call lands), clean up with:
--   DROP VIEW IF EXISTS public.ankita_test_calls;
CREATE VIEW public.ankita_test_calls AS SELECT * FROM public.ceo_live_calls;
