-- 000: Full-schema bootstrap for a FRESH Supabase project.
--
-- The live database was built incrementally (README base schema + migrations
-- 002–011) and already matches this end state — do NOT run this against it.
-- Run this instead of the incremental chain when standing up a new
-- environment (or rebuilding after disaster): it creates every table the
-- server writes to, as of migration 011.
--
-- Tables:
--   ceo_live_calls            — production scored calls (the webhook default)
--   test_calls                — optional test instance (a second Railway
--                               service on the same entry point, with
--                               SUPABASE_TABLE=test_calls)
--   sim_calls                 — simulation traffic diverted by SIM_ASSISTANT_IDS
--   feedback_sms              — post-call SMS survey state + answers
--   participant_demographics  — pre-bucketed Salesforce join for the dashboard
--
-- Access model (matches migrations 006/010): the server connects with the
-- anon key, RLS is DISABLED, and access control is the API key itself. If you
-- want this locked down later: keep RLS on, add policies, and point the
-- server at a service_role key instead.

-- One verdict/reasoning/scan column triple per judge; verdict is
-- pass | fail | na | error, scan holds the verbatim evidence quote
-- ({"evidence": "..."}). Add a new triple (see README "Adding a new judge")
-- to ceo_live_calls AND sim_calls (and test_calls if you use it) when a
-- judge ships.

CREATE TABLE IF NOT EXISTS ceo_live_calls (
    id              SERIAL PRIMARY KEY,
    call_id         TEXT UNIQUE,
    assistant_id    TEXT,
    arm_label       TEXT,               -- 'A' | 'B' | '' (from ARM_*_ASSISTANT_ID env)
    ceo_id          TEXT,               -- participant ID; TEXT so leading zeros survive
    customer_number TEXT,
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    duration_sec    INTEGER,
    transcript      TEXT,
    scored_at       TIMESTAMPTZ DEFAULT NOW(),

    limits_the_load_verdict     TEXT,
    limits_the_load_reasoning   TEXT,
    limits_the_load_scan        JSONB,

    feedback_q_low_bar_verdict     TEXT,
    feedback_q_low_bar_reasoning   TEXT,
    feedback_q_low_bar_scan        JSONB,

    drives_practice_verdict     TEXT,
    drives_practice_reasoning   TEXT,
    drives_practice_scan        JSONB,

    scaffolds_then_fades_verdict     TEXT,
    scaffolds_then_fades_reasoning   TEXT,
    scaffolds_then_fades_scan        JSONB,

    quality_conversational_flow_verdict     TEXT,
    quality_conversational_flow_reasoning   TEXT,
    quality_conversational_flow_scan        JSONB,

    adapts_when_stuck_verdict     TEXT,
    adapts_when_stuck_reasoning   TEXT,
    adapts_when_stuck_scan        JSONB,

    reentry_appropriate_framing_verdict     TEXT,
    reentry_appropriate_framing_reasoning   TEXT,
    reentry_appropriate_framing_scan        JSONB,

    pii_verdict     TEXT,
    pii_reasoning   TEXT,
    pii_scan        JSONB,

    makes_it_a_dialogue_verdict     TEXT,
    makes_it_a_dialogue_reasoning   TEXT,
    makes_it_a_dialogue_scan        JSONB
);

-- Identical row shape: server_lib.handle_call_webhook builds ONE row for every
-- instance/table, so all call tables must carry the same columns. LIKE copies
-- columns + the call_id unique index; note the copied id DEFAULT means these
-- share ceo_live_calls' id sequence — fine here (nothing joins or refers to
-- id; call_id is the real key).
CREATE TABLE IF NOT EXISTS test_calls (LIKE ceo_live_calls INCLUDING ALL);
CREATE TABLE IF NOT EXISTS sim_calls  (LIKE ceo_live_calls INCLUDING ALL);

-- Post-call SMS feedback survey (see sms_feedback.py; migration 006).
CREATE TABLE IF NOT EXISTS feedback_sms (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    phone_number    TEXT        NOT NULL,
    call_id         TEXT,
    assistant_id    TEXT,
    arm_label       TEXT,
    call_started_at TIMESTAMPTZ,

    state           TEXT        NOT NULL DEFAULT 'awaiting_q1',
                    -- awaiting_q1 | awaiting_q2 | done | stopped
    q1_realistic    SMALLINT,           -- 1..5
    q2_recommend    TEXT,               -- 'yes' | 'maybe' | 'no'
    reprompts       SMALLINT    NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS feedback_sms_phone_active_idx
    ON feedback_sms (phone_number, created_at DESC)
    WHERE state IN ('awaiting_q1', 'awaiting_q2');

-- Demographics join for the dashboard (see migration 010). Seeded by
-- ceo_voice_coach/push_demographics.py from the Salesforce export; values are
-- pre-bucketed/rolled-up locally — no raw ages or free-text categories.
CREATE TABLE IF NOT EXISTS participant_demographics (
    ceo_id     TEXT PRIMARY KEY,
    site       TEXT,
    population TEXT,
    age        TEXT,
    gender     TEXT,
    race       TEXT,
    education  TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Supabase doesn't auto-grant on tables created via SQL, and RLS-on blocks
-- anon writes even with grants (42501) — see migrations 006/010.
GRANT SELECT, INSERT, UPDATE ON ALL TABLES    IN SCHEMA public TO anon, authenticated, service_role;
GRANT USAGE,  SELECT         ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated, service_role;

ALTER TABLE ceo_live_calls           DISABLE ROW LEVEL SECURITY;
ALTER TABLE test_calls               DISABLE ROW LEVEL SECURITY;
ALTER TABLE sim_calls                DISABLE ROW LEVEL SECURITY;
ALTER TABLE feedback_sms             DISABLE ROW LEVEL SECURITY;
ALTER TABLE participant_demographics DISABLE ROW LEVEL SECURITY;
