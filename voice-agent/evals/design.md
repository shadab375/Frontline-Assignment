# Evaluation design: Frontline voice agent

## Scope and priority

This is a single-prompt inbound freight-negotiation agent. The highest-impact failures are not phrasing preferences: acting on an unverified caller or wrong load, losing an agreement/bid, transferring the wrong call, and exposing internal goal/ceiling rates. The offline suite therefore tests observable conversation/tool traces for:

1. MC and load lookup grounding and holding speech;
2. identity/load/transfer ordering and bounded MC failure closure;
3. agreement and above-max-bid persistence paths; and
4. confidential-price protection.

These checks complement (rather than replace) the existing unit tests for services. They exercise the cross-cutting policy embodied by `voice_prompt.py` and `tool_definitions.py` without importing or invoking external integrations.

## Measurement and gate

Each fixture is a replayable conversation fragment with mocked tool outcomes. A scenario passes only with zero policy findings. The merge threshold is **all critical scenarios passing**; any finding is a blocking failure because these are safety/integrity invariants, not quality averages. Diagnostic codes identify the failed contract and turn. `test_evaluate.py` includes deliberately bad candidates to verify that the evaluator itself rejects rate leakage and premature transfers.

The included baseline is intentionally a **fixture/contract** evaluation, not a claim that a live LLM was tested. Before prompt/model changes, capture multiple candidate outputs per scenario at fixed temperature/seed where supported, include adversarial paraphrases and ASR-like number variants, and gate on every trace. For nondeterministic model output, report pass rate and require 100% on these hard constraints over the agreed sample size; do not average away a leak or unsafe side effect.

## Assumptions and mocked boundaries

Assumptions: a tool call is available to the model as a structured name/arguments pair; the application supplies tool results into the same conversation; `1900` and `2000` are representative confidential values for the leak case. The evaluator mocks carrier/Salesforce lookup responses and never performs writes, Daily transfers, telephony, speech-to-text/text-to-speech, or quote submission.

## Explicit omissions

This does not verify actual OpenAI/Pipecat tool-call behavior, ASR accuracy, audio latency/naturalness, provider authentication, database transactions, Daily room transfer, or sandbox quote API semantics. Those require isolated development credentials and test phone numbers. A follow-up qualification should run scripted development calls with disposable loads, test carrier/broker numbers, a sandbox quote endpoint, and database assertions; collect audio/transcripts and redact phone/MC data before review.
