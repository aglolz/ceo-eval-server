# Feedback to Questions — Low Bar Judge Rubric

You are evaluating an AI voice coaching session on ONE dimension: whether the coach's per-question feedback is egregiously bad.

This is a LOW BAR — the coach passes unless the feedback demonstrates that the coach did not accurately process what the participant said. Generic but sound advice passes. Ordinary encouragement of a mediocre answer passes.

## Step 1 — Scan

Read the transcript. For each mid-session feedback turn by the coach (NOT the end-of-session summary), compare:

- What question the coach asked
- What the participant actually said in response
- What the coach said about it

For each turn, classify as:

- **aligned**: the coach's feedback accurately reflects what the participant said, even if the feedback is generic or templated
- **disconnected**: the coach's feedback does not match what the participant actually said or did

A turn is disconnected if ANY of the following are true:

- Coach uses a specific quality claim (good expansion, great detail, strong example, clear explanation) that the participant's answer does not actually demonstrate — the coach is describing a quality the answer doesn't have. PRAISE TIER MATTERS here: mild process-level encouragement ("Nice work", "Thanks for that", "That's a cleaner answer", "good effort") is NOT a quality claim and passes even on a weak or garbled answer — the low bar allows encouragement. What fails is the coach CERTIFYING quality the answer doesn't show: superlative or content-specific praise of a weak, empty, or mediocre answer ("That was a really strong answer", "What a great and creative example", "I could hear genuine passion in that answer", "excellent detail"). THE TEST IS WHERE THE PRAISE POINTS: a quality claim is praise directed AT THE ANSWER ITSELF or its qualities ("great answer", "strong example", "creative", "genuine passion", "excellent detail"). One specific case of unearned certification: on a "tell me about a time..." question, if what the participant offers as their example is this practice call itself — the coach's own questions, or the feedback just given — then no past experience has been offered, and calling that a good, creative, or quick-thinking answer certifies quality the response does not have. This is narrow: it means the AI practice call itself. Feedback the participant received from a real person — a supervisor, a teacher, a program staff member — is a real instance no matter how recently it happened, including earlier the same day. Praise or warmth directed elsewhere is NOT a quality claim: generic statements about the topic's value ("teamwork matters in any warehouse job"), encouragement of the person or process ("nice work", "good effort"), or bridging remarks that treat a response as worth coaching on without praising it. Treating a thin or garbled response as legitimate and coaching from it is aligned behavior, not certification. A session whose feedback turns repeatedly certify mediocre answers as strong/great/excellent is a fail even when each turn accurately restates WHAT the participant said — accurately restating content does not earn the quality label attached to it
- Coach generates substantive praise or builds meaningful feedback from a response that contains no real content — even if the coach subsequently asks for elaboration, the initial validation of a non-answer is a disconnect
- Coach validates a nonsensical, incoherent, or garbled response as good

IMPORTANT — these are speech-to-text transcripts of phone calls. Garbled, fragmented, or nonsensical text in a User turn is usually a TRANSCRIPTION or audio-channel failure, not the participant giving a bad answer. Do NOT treat a garbled or thin response as "no real content" that the coach mishandled. The coach cannot see the transcript — it heard the audio, which may have been fine. Acknowledging such a response warmly ("Thanks for that", "Nice work", "Okay"), asking to try again, or simply moving on is sound coaching over a noisy channel and is ALIGNED, not disconnected. The two clauses above fire only when the coach ATTRIBUTES SPECIFIC SUBSTANTIVE QUALITY to a response that shows none — describing content, skills, or qualities the answer never contained — not when it politely bridges past a response the transcript mangled. One thing the allowance never covers is a claim about the PARTICIPANT'S INNER STATE OR DELIVERY — passion, enthusiasm, conviction, confidence, energy, sincerity. The coach may restate the words it did catch and attach a modest attribute to them, which is aligned; but telling the participant it heard genuine passion or real confidence in an answer whose words show nothing of the kind is a quality claim, and poor audio or a language barrier does not excuse it.
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

