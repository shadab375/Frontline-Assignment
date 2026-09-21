# PR #1 review — LiveKit/Telnyx migration and warm transfer

Reviewed pinned base `5a98a66274322954a0a6255f54650eec6e50c41d` against
head `f93f7a5f79efdf08c18c583a6a6f7167084b93ae`.

## Change and lifecycle summary

This PR replaces Daily call ingress, rooms, transport, and dial-out boundaries
with LiveKit rooms and Telnyx-backed SIP, while retaining the existing
single-prompt negotiation agent. It also adds a warm transfer: the caller stays
in Room1, the primary bot gates its media and starts a hold publisher, a Room2
pipeline dials and briefs the broker, then a verified participant move brings the
broker into Room1. Success removes bot audio and retains an observer until either
human departs; failure/cancellation is intended to clean Room2 and restore the
primary Room1 bot.

## Method note

The review traces each retained finding against a read-only checkout at the
pinned head; claims that did not survive that check were retracted. The Python
suites could not execute in this runtime because the image lacks pip/ensurepip;
the web install was separately blocked by the private UI package, as recorded
below.

## Merge recommendation: merge after fixes

The Daily-to-LiveKit boundary and the proposed Room1/Room2 transfer design are
coherent, but three verified failure-path defects can misreport transfer state
or produce spurious provider-facing failures. Resolve the P1 item and add its
regression coverage before merging. The P2 items should also be addressed before
production qualification because they amplify webhook retry behavior.

## P1 findings

### Broker departure can regress a terminal result to `CONSULTING`

- **Location:** `voice-agent/orchestrator.py:220-243, 309-314, 481-513` and
  `voice-agent/room2_pipeline_service.py:302-312`.
- **Classification:** Verified defect.
- **Problem:** The post-dial path has no terminal-state/attempt guard before it
  writes `CONSULTING`.
- **Failure scenario:** `dial_phone()` returns, then the broker disconnects before
  or during `mark_broker_answered()`. The Room2 callback terminalizes the attempt
  as `FAILED/broker_disconnected`; the original coroutine resumes and writes
  `CONSULTING` after the terminal transition has begun.
- **Evidence:** Terminal failure sets the phase before awaiting cleanup, whereas
  the dialing coroutine continues from its awaited calls directly to the
  `CONSULTING` transition. Existing disconnect coverage tests departure after a
  consultation has started, not this pre-consultation interleaving.
- **Impact:** Transfer audit/state can report an active consultation after Room2
  was torn down and Room1 restored; later callbacks or retries see contradictory
  state.
- **Fix:** Guard each post-await transition by attempt generation and terminal
  state, or centralize transitions behind compare-and-set semantics.
- **Regression test:** Interleave a broker-disconnect callback between dial return
  and `mark_broker_answered()`; assert the terminal reason/phase are preserved and
  no `CONSULTING` transition occurs.
- **Confidence:** High.

## P2 findings

### Duplicate webhook delivery becomes a spurious BotRunner failure

- **Location:** `voice-agent/server.py:123-141`; `voice-agent/bot.py:689-704`;
  `voice-agent/server_utils.py:39-49`.
- **Classification:** Verified defect.
- **Problem:** The webhook event ID is not reserved before starting BotRunner.
  BotRunner permits only one active task, so it admits the first overlapping
  request and responds `409` to the second. `start_bot_local()` maps that non-202
  response to `503`; the second handler never records the event ID.
- **Failure scenario:** Deliver the same valid `participant_joined` event twice
  while the first BotRunner request is active.
- **Evidence:** The membership check/await/add are at `server.py:123/140/141`.
  The single-task guard is at `bot.py:693-704`; the `409` to `503` mapping is at
  `server_utils.py:44-49`. The existing test posts its duplicate only after the
  first request completes and does not exercise this path.
- **Impact:** It does **not** create two simultaneous bots. The primary call keeps
  its first bot, but the duplicate is falsely reported as a failed delivery,
  producing noisy 503s and provider retry behavior.
- **Fix:** Atomically reserve the event before the first await and release the
  reservation if startup fails.
- **Regression test:** Run two concurrent webhook requests against a BotRunner
  fake that returns 202 then 409; assert one bot start and an idempotent duplicate
  response rather than 503.
- **Confidence:** High.

### BotRunner startup can hold webhook acknowledgement for the broad client timeout

- **Location:** `voice-agent/server_utils.py:39-56`, called by
  `voice-agent/server.py:140`.
- **Classification:** Verified defect.
- **Problem:** The webhook awaits `start_bot_local()` inline, but its
  `session.post()` has no request-specific timeout.
- **Failure scenario:** BotRunner accepts the TCP connection but never writes
  response headers. The webhook waits for aiohttp's broad client-session default
  rather than a short ingress deadline.
- **Evidence:** `create_app()` creates a default `aiohttp.ClientSession`; the
  `session.post()` call passes no `timeout=`. No existing test simulates a
  non-responding BotRunner.
- **Impact:** Webhook acknowledgement is delayed long enough to encourage
  provider retries, increasing exposure to the duplicate-start defect.
- **Fix:** Apply a short explicit `aiohttp.ClientTimeout`, return a retryable 503
  on expiry, and coordinate idempotency-reservation rollback.
- **Regression test:** Use a local BotRunner endpoint that accepts but never
  responds; assert bounded 503 behavior and no retained reservation.
- **Confidence:** High on the code path; medium on exact provider retry timing
  because no live call was placed.

## Plausible risks

None retained. Candidate concerns about outbound `202` timing and caller-ID/
tenant mismatch depend on an API and authorization contract not established by
this repository, so they are not presented as defects.

## Open questions

None blocking this review. The deployment model remains relevant to the
process-local idempotency design, but is recorded below as an assumption rather
than a new finding.

## Checks actually run

| Command | Result |
| --- | --- |
| `git clone --no-checkout https://github.com/e3-solutions/Frontline-Assignment.git /tmp/frontline && git -C /tmp/frontline checkout --detach f93f7a5f79efdf08c18c583a6a6f7167084b93ae` | Passed; detached HEAD exactly matched the pinned SHA. |
| `python3 -m venv /tmp/frontline/.venv` | Could not run: image lacks `ensurepip`. |
| `python3 -m pip install -r voice-agent/requirements.txt -r voice-agent/tests/requirements-test.txt ruff` | Could not run: image has no `pip`. |
| `cd /tmp/frontline/voice-agent && /usr/bin/python3 -m pytest tests -v --tb=short` | Could not run: `No module named pytest`. |
| `cd /tmp/frontline/shared && /usr/bin/python3 -m pytest tests -v --tb=short` | Could not run: `No module named pytest`. |
| `cd /tmp/frontline && ruff check $(git diff --name-only 5a98a66274322954a0a6255f54650eec6e50c41d f93f7a5f79efdf08c18c583a6a6f7167084b93ae -- '*.py')` | Could not run: `ruff: command not found`. |
| `cd /tmp/frontline/web && npm ci` | Could not run: E401 fetching private `@e3-solutions/ui`; no credentials were used. |
| `cd /tmp/frontline/web && npx --no-install tsc --noEmit` | Could not run: TypeScript package was unavailable because install did not complete. |

No suite produced a pass/fail count. Static source inventory found 91 named
shared tests and 105 named voice-agent test functions; the latter's nine pytest
parametrizations expand consistently with the PR's stated 127 collected cases.
This supports, but does not verify, the PR's **127 / 91** claim. The public
GitHub Actions page also reports `functional-tests` succeeded, but its log is
unavailable without authentication.

## Assumptions and unresolved questions

- The documented one-active-bot-per-runner limit is intentional. Production must
  provide one runner per concurrent call, or replace global task ownership with
  room/call-keyed ownership.
- No credentials or live calls were used. LiveKit/Telnyx provisioning, speech
  services, recordings, Supabase storage, and end-to-end PSTN transfer behavior
  need development-environment qualification after the fixes.
- Process-local webhook deduplication does not survive restart or coordinate
  multiple workers. Whether that is acceptable depends on the deployment model.
