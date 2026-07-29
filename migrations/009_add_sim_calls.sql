-- Migration 009 — create `sim_calls`, the dedicated home for SIMULATION calls.
-- Run ONCE against the Supabase Postgres (SQL editor) BEFORE deploying the server
-- revision that adds resolve_table/SIM_ASSISTANT_IDS. If the server deploys first,
-- only diverted sim calls fail to insert (live/test tables are untouched), but those
-- sim scores are lost until this runs.
--
-- Why: the sim phone line (twilio 10, +17124307897) points at a copy of the arm-B
-- assistant and posts end-of-call reports to the same Railway webhook as live
-- traffic. Without a diversion those synthetic calls would land in the live/test
-- tables and pollute the A/B analysis. server_lib.resolve_table diverts any call
-- whose assistantId is listed in the SIM_ASSISTANT_IDS env var into this table
-- (name overridable via SIM_TABLE). Sim calls also skip the participant SMS
-- pipeline (summary mirror + feedback survey).
--
-- Schema = the exact row shape server_lib.handle_call_webhook writes today:
-- base call columns + arm_label (migration 008) + ceo_id (007) + a
-- verdict/reasoning/scan triple per judge (README base + 002/003/004/005).
-- arm_label will be '' here (a sim assistant is neither ARM_A nor ARM_B);
-- kept anyway so the row builder stays table-agnostic.

CREATE TABLE IF NOT EXISTS sim_calls (
    id              SERIAL PRIMARY KEY,
    call_id         TEXT UNIQUE,
    assistant_id    TEXT,
    arm_label       TEXT,
    ceo_id          TEXT,
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
