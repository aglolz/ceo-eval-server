-- SMS participant-feedback survey (post-summary two-question flow).
-- One row per survey attempt, created when we send Q1 after a call ends.
-- Attribution is exact: we stamp the arm + call at creation time, so a reply
-- never needs fuzzy matching. Keyed by phone for the in-flight state lookup.

create table if not exists feedback_sms (
    id              bigint generated always as identity primary key,
    phone_number    text        not null,
    call_id         text,                       -- the call this survey is about
    assistant_id    text,                       -- the arm (== call_scores.assistant_id)
    arm_label       text,                       -- 'A' | 'B' | '' (convenience)
    call_started_at timestamptz,                -- the call's startedAt

    state           text        not null default 'awaiting_q1',
                    -- awaiting_q1 | awaiting_q2 | done | stopped
    q1_realistic    smallint,                   -- 1..5
    q2_recommend    text,                        -- 'yes' | 'maybe' | 'no'
    reprompts       smallint    not null default 0,

    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- Fast lookup of the active survey for an inbound reply.
create index if not exists feedback_sms_phone_active_idx
    on feedback_sms (phone_number, created_at desc)
    where state in ('awaiting_q1', 'awaiting_q2');

-- The server connects with the anon role (SUPABASE_KEY). New tables don't
-- inherit privileges, so without this every start_survey/handle_inbound call
-- gets 42501 permission-denied. start_survey needs select+insert, handle_inbound
-- needs select+update. Matches the grants the existing judge tables already have.
grant select, insert, update on table public.feedback_sms
    to anon, authenticated, service_role;

-- RLS is on by default and blocks the anon INSERT (42501 "new row violates
-- row-level security policy") even with the grant above. The judge tables
-- (ankita_test_calls) run with RLS off, so match them. If you later want this
-- locked down, keep RLS on + add policies, or point the server at service_role.
alter table public.feedback_sms disable row level security;
