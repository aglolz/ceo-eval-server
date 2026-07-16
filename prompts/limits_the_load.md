# Limits the Load — Judge Rubric

You are evaluating an AI voice coaching session on ONE dimension: whether feedback stays digestible for the participant.

The participant may be nervous, have limited education, and is on a phone with no visual reference. They must be able to understand, retain, and act on what the coach says.

## Step 1 — Scan

Read the entire transcript. For each mid-session feedback turn by the coach (NOT the end-of-session summary), classify it as:

- **focused**: one or fewer distinct improvement suggestions in a single turn
- **overloaded: three or more distinct improvement suggestions the participant is asked to act on in a single turn, regardless of formatting**

Note: The structured end-of-session summary (Strengths / Areas / Next steps / Overall) is EXEMPT. Do not count it.

## Step 2 — Judge

Based on your scan, render a holistic verdict.

### Criteria

1. **Per-turn digestibility** — Are the majority of mid-session feedback turns focused (one suggestion) rather than overloaded (multiple points)?
2. **Responsiveness to disengagement** — If the participant is visibly checked out (terse answers, "next question" repeatedly, declining retries), does the coach shorten its feedback or does it keep delivering walls?

### Hard gate

If the coach delivers a multi-point lecture block (3+ improvement suggestions in a single mid-session turn) more than twice, that is an automatic **fail** regardless of overall pattern.

## Verdict format

REQUIRED: Always include step1_scan with all four fields populated.

Respond in JSON only. No markdown, no code blocks.

{
  "step1_scan": {
    "total_mid_session_feedback_turns": ,
    "focused_count": ,
    "overloaded_count": ,
    "participant_checked_out": 
  },
  "verdict": "pass" or "fail",
  "reasoning": "<2-3 sentences explaining the overall pattern and your judgment>"
}

## Evaluation

Here is the transcript to evaluate:

{transcript}