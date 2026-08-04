-- Migration 010 — participant demographics for the hosted dashboard join.
-- Run ONCE against the Supabase Postgres (SQL editor) BEFORE seeding.
--
-- Seeded from the local Salesforce report*.csv export by
-- ceo_voice_coach/push_demographics.py (re-run it whenever a fresh export
-- lands); the dashboard blueprint joins on ceo_id at build time. Values are
-- pre-bucketed/rolled-up locally (age buckets, gender/race rollups) — no raw
-- ages or free-text categories are stored. The eventual production feed is
-- the Coefficient sync (HANDOVER_PLAN Option B), which can upsert into this
-- same table.

CREATE TABLE IF NOT EXISTS participant_demographics (
    ceo_id     TEXT PRIMARY KEY,   -- normalized CEO Code (trimmed, leading zeros dropped)
    site       TEXT,
    population TEXT,
    age        TEXT,               -- bucketed, e.g. "25–34"
    gender     TEXT,               -- rolled up
    race       TEXT,               -- rolled up
    education  TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Match the rest of this database (ankita_test_calls etc. are RLS-off; access
-- control is the API key). Without this, PostgREST returns 401 on every
-- read/write and the seed + dashboard join both fail.
ALTER TABLE participant_demographics DISABLE ROW LEVEL SECURITY;

-- This project doesn't auto-grant on new tables (42501 "permission denied"
-- without it). UPDATE is needed for the seeder's upsert path.
GRANT SELECT, INSERT, UPDATE ON public.participant_demographics TO anon, authenticated;
