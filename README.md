# CEO Voice Coach — Live Eval Server

Receives end-of-call webhooks from Vapi, scores transcripts against judge rubrics via Claude API, writes results to Supabase.

## Setup

1. **Supabase:** Create a project at supabase.com. Create the `call_scores` table (schema below).
2. **Railway:** Connect this repo/folder. Set environment variables in Railway dashboard.
3. **Vapi:** Point your assistant's webhook URL at `https://your-app.railway.app/webhook`.

## Environment Variables

Set these in Railway's dashboard:

- `ANTHROPIC_API_KEY` — your Claude API key
- `SUPABASE_URL` — your Supabase project URL
- `SUPABASE_KEY` — your Supabase anon key
- `ANTHROPIC_MODEL` — defaults to `claude-sonnet-4-6`

## Adding New Judges

1. Add the prompt `.md` file to `prompts/`
2. Add an entry to the `JUDGES` list in `server.py`
3. Add the corresponding columns to the `call_scores` table in Supabase
4. Push to GitHub — Railway auto-redeploys

## Database Schema

```sql
CREATE TABLE call_scores (
    id              SERIAL PRIMARY KEY,
    call_id         TEXT UNIQUE,
    assistant_id    TEXT,
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
    feedback_q_low_bar_scan        JSONB
);
```

Add column pairs (`_verdict`, `_reasoning`, `_scan`) as you build more judges.

## Testing

Hit the health check:
```
curl https://your-app.railway.app/
```

Simulate a webhook locally:
```
python server.py
# In another terminal:
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -d '{"message":{"type":"end-of-call-report","call":{"id":"test-1","artifact":{"transcript":"AI: Hello User: Hi"}}}}'
```
