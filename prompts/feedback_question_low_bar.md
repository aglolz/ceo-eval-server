# Feedback to Questions — Low Bar Judge Rubric

You are evaluating an AI voice coaching session on ONE dimension: whether the coach's per-question feedback is egregiously bad.

This is a LOW BAR — the coach passes unless the feedback demonstrates that the coach did not accurately process what the participant said. Generic but sound advice passes. Ordinary encouragement of a mediocre answer passes.

## Step 1 — Scan

Read the transcript. For each mid-session feedback turn by the coach (NOT the end-of-session summary), compare:

- What the participant actually said
- What the coach said about it

For each turn, classify as:

- **aligned**: the coach's feedback accurately reflects what the participant said, even if the feedback is generic or templated
- **disconnected**: the coach's feedback does not match what the participant actually said or did

A turn is disconnected if ANY of the following are true:

- Coach praises a quality (depth, detail, expansion, specificity) that the participant's answer does not actually demonstrate"
- Coach uses a specific quality claim (good expansion, great detail, strong example, clear explanation) that the participant's answer does not actually demonstrate — the coach is describing a quality the answer doesn't have
- Coach validates a nonsensical, incoherent, or garbled response as good
- Coach gives advice that doesn't match the question that was asked
- Coach delivers a complete answer on behalf of the participant instead of responding to what the participant actually said
- Coach responds to a repeated answer as if it's new information
- Coach uses an off-topic redirect on a legitimate, on-topic response from the participant
- Coach gives factually wrong or harmful interview advice



## Step 2 — Judge

A single disconnected turn anywhere in the session is enough to fail.

- If ALL turns are aligned → **pass**
- If ANY turn is disconnected → **fail**



## Evaluation

Here is the transcript to evaluate:

{transcript}

Respond in JSON only. No markdown, no code blocks.

```
{
  "step1_scan": {
    "total_feedback_turns": <int>,
    "aligned_count": <int>,
    "disconnected_count": <int>
  },
  "verdict": "pass" or "fail",
  "reasoning": "<2-3 sentences. If failing, cite the specific exchange where the coach's feedback was disconnected from the participant's actual response.>"
}
```

